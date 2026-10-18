import asyncio
import json
from typing import TypedDict
from uuid import UUID, uuid5

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.infrastructure.database import engine
from workbench.modules.conversations import Message
from workbench.modules.models.execution import generate
from workbench.modules.runs.ownership import assert_owned
from workbench.modules.tools.presentation import model_view
from workbench.modules.tools.service import execute_one


class State(TypedDict):
    run_id: str
    messages: list[dict]
    round_number: int
    calls: list[dict]
    call_index: int
    join_needed: bool
    input_pending: bool


async def model_node(state: State) -> dict:
    assistant = await generate(state["run_id"], state["messages"], state["round_number"])
    calls = []
    for tool in assistant.get("tool_calls", []):
        calls.append(
            {
                "id": str(uuid5(UUID(state["run_id"]), f"{state['round_number']}:{tool['id']}")),
                "provider_call_id": tool["id"],
                "name": tool["function"]["name"],
                "arguments": json.loads(tool["function"]["arguments"]),
            }
        )
    return {
        "messages": state["messages"] + assistant.get("_continuation_messages", []) + [assistant],
        "calls": calls,
        "call_index": 0,
        "round_number": state["round_number"] + 1,
    }


async def checkpoint_node(state: State) -> dict:
    from workbench.modules.runs.safety import acknowledge, before_model

    wait = await asyncio.to_thread(
        before_model,
        state["run_id"],
        state["round_number"],
        state.get("call_index", 0) >= len(state.get("calls", [])),
    )
    if wait:
        interrupt(wait)
        await asyncio.to_thread(acknowledge, state["run_id"], wait)
        if wait["kind"] == "safety" and state.get("call_index", 0) < len(state.get("calls", [])):
            # Resume a rejected batch by replanning, rather than executing the
            # remaining stale calls before the user's steering can be applied.
            return await skip_pending_calls(
                state,
                "tool_skipped_for_replan",
                "防循环暂停后，此调用未执行；请结合用户补充重新规划。",
            )
    if state.get("call_index", 0) < len(state.get("calls", [])):
        from workbench.modules.collaboration.inbox import has_new_user_input

        def pending_user():
            with Session(engine()) as db:
                return has_new_user_input(db, state["run_id"])

        if await asyncio.to_thread(pending_user):
            return await skip_pending_calls(
                state, "tool_skipped_for_steering", "用户补充了新要求，此调用尚未执行，请重新规划。"
            )
    return {}


async def skip_pending_calls(state: State, code: str, message: str) -> dict:
    replies = []
    call_index = state["call_index"]
    for call in state["calls"][state["call_index"] :]:
        content = await asyncio.to_thread(pending_call_result, state["run_id"], call, code, message)
        if content is None:
            # A recovered command/MCP continuation must finish or reconcile its
            # actual result before steering can skip the rest of the batch.
            break
        reply = {
            "role": "tool",
            "_operation_id": None,
            "tool_call_id": call["provider_call_id"],
            "content": json.dumps(content, ensure_ascii=False),
        }
        await asyncio.to_thread(persist_reply, state["run_id"], call, reply)
        replies.append(reply)
        call_index += 1
    return {"messages": state["messages"] + replies, "call_index": call_index}


def pending_call_result(run_id: str, call: dict, code: str, message: str) -> dict | None:
    from workbench.modules.runs.domain import Run
    from workbench.modules.tools.domain import Operation
    from workbench.modules.tools.service import record_result

    result = {"error": {"code": code, "message": message}}
    operation_id = None
    with Session(engine()) as db, db.begin():
        db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        assert_owned(db, run_id)
        operation = db.scalar(
            select(Operation).where(
                Operation.run_id == run_id, Operation.tool_call_id == call["id"]
            )
        )
        if operation:
            if operation.status in {"succeeded", "failed"} and operation.result is not None:
                return model_view(call["name"], operation.result)
            if operation.status in {"running", "waiting_input", "outcome_unknown"} or (
                operation.effect_status != "not_started"
            ):
                return None
            operation_id = operation.id
    if operation_id:
        record_result(run_id, operation_id, result, effect_status="not_started")
    return result


async def tool_node(state: State) -> dict:
    call = state["calls"][state["call_index"]]
    result = await execute_one(state["run_id"], call)
    reply = {
        "role": "tool",
        "_operation_id": None,
        "tool_call_id": call["provider_call_id"],
        "content": json.dumps(model_view(call["name"], result), ensure_ascii=False),
    }
    await asyncio.to_thread(persist_reply, state["run_id"], call, reply)
    return {"messages": state["messages"] + [reply], "call_index": state["call_index"] + 1}


def persist_reply(run_id: str, call: dict, reply: dict) -> None:
    # Each node executes one tool. Its interrupt ordinal never shifts when a completed
    # operation is replayed after a crash between external completion and checkpoint commit.
    with Session(engine()) as db, db.begin():
        from workbench.modules.runs.domain import Run
        from workbench.modules.tools.domain import Operation

        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        assert_owned(db, run.id)
        operation = db.scalar(
            select(Operation).where(
                Operation.run_id == run.id,
                Operation.tool_call_id == call["id"],
            )
        )
        reply["_operation_id"] = operation.id if operation else None
        message_id = str(uuid5(UUID(run_id), f"tool-reply:{call['id']}"))
        existing = db.get(Message, message_id)
        if operation is not None and existing is None:
            existing = db.scalar(select(Message).where(Message.operation_id == operation.id))
        if existing is None:
            db.add(
                Message(
                    id=message_id,
                    thread_id=run.thread_id,
                    run_id=run.id,
                    role="tool",
                    content=reply["content"],
                    status="completed",
                    provider_message=reply,
                    operation_id=operation.id if operation else None,
                )
            )
            if operation is None:
                from workbench.modules.runs.service import emit

                emit(
                    db,
                    run_id,
                    "tool.rejected",
                    {
                        "tool_name": call["name"],
                        "tool_call_id": call["id"],
                        "result": json.loads(reply["content"]),
                    },
                )


def after_model(state: State) -> str:
    return "checkpoint" if state["calls"] else "join_check"


async def join_check_node(state: State) -> dict:
    from workbench.modules.collaboration.inbox import has_pending_inputs
    from workbench.modules.collaboration.service import has_pending

    def pending_input():
        with Session(engine()) as db:
            return has_pending_inputs(db, state["run_id"])

    return {
        "join_needed": await asyncio.to_thread(has_pending, state["run_id"]),
        "input_pending": await asyncio.to_thread(pending_input),
    }


async def join_node(state: State) -> dict:
    from fastapi.encoders import jsonable_encoder

    from workbench.modules.collaboration.service import finish_wait, prepare_wait

    if await asyncio.to_thread(prepare_wait, state["run_id"]):
        interrupt({"kind": "agents", "run_id": state["run_id"]})
    result = jsonable_encoder(await asyncio.to_thread(finish_wait, state["run_id"]))
    return {
        "messages": state["messages"]
        + [
            {
                "role": "user",
                "source_kind": "agent_results",
                "content": (
                    "子任务的当前实际状态如下（可能仍在执行）。请先处理用户最新补充要求，"
                    "再依据真实结果继续主任务；失败项如实说明。\n"
                    + json.dumps(model_view("inspect_agents", result), ensure_ascii=False)
                ),
            }
        ]
    }


def after_tool(state: State) -> str:
    return "checkpoint"


def build_graph(checkpointer):
    builder = StateGraph(State)
    builder.add_node("checkpoint", checkpoint_node)
    builder.add_node("model", model_node)
    builder.add_node("tool", tool_node)
    builder.add_node("join", join_node)
    builder.add_node("join_check", join_check_node)
    builder.add_edge(START, "checkpoint")
    builder.add_conditional_edges(
        "checkpoint",
        lambda state: (
            "tool" if state.get("call_index", 0) < len(state.get("calls", [])) else "model"
        ),
    )
    builder.add_conditional_edges("model", after_model)
    builder.add_conditional_edges("tool", after_tool)
    builder.add_conditional_edges(
        "join_check",
        lambda state: (
            "join"
            if state.get("join_needed")
            else "checkpoint"
            if state.get("input_pending")
            else END
        ),
    )
    builder.add_edge("join", "checkpoint")
    return builder.compile(checkpointer=checkpointer)
