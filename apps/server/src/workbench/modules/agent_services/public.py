"""Native public API adapter: authorization precedes every owner-service call."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.modules import conversation_service
from workbench.modules.access import service as access
from workbench.modules.access.domain import ExecutionBinding, ExternalNamespace
from workbench.modules.agent_services import dto, service
from workbench.modules.agent_services.domain import (
    AgentService,
    AgentServiceRevision,
    AgentSessionBinding,
)
from workbench.modules.conversations import Message, Thread
from workbench.modules.files.domain import Artifact
from workbench.modules.interactions import Approval, Question
from workbench.modules.runs import service as runs
from workbench.modules.runs.domain import Run
from workbench.modules.tools.domain import Operation


def callable_agent(db, client, service_id, action="read"):
    access.authorize_service(db, client.id, service_id, action)
    row = service.get(db, client.owner_user_id, service_id)
    if not row.enabled:
        raise AppError("agent_disabled", "Agent 当前不可调用。", 403, "policy")
    return row


def list_agents(db: Session, client) -> list[dict]:
    access.require_client(db, client.id)
    rows = db.scalars(
        select(AgentService)
        .where(AgentService.owner_user_id == client.owner_user_id, AgentService.enabled.is_(True))
        .order_by(AgentService.created_at)
    )
    result = []
    for row in rows:
        try:
            access.authorize_service(db, client.id, row.id, "read")
            access.authorize_service(db, client.id, row.id, "invoke")
        except AppError as error:
            if error.status in {403, 404}:
                continue
            raise
        result.append(agent_view(db, client, row))
    return result


def agent_view(db, client, row):
    grant = access.authorize_service(db, client.id, row.id, "read")
    result = service.public_view(db, row)
    capabilities = result["capabilities"]
    if grant.allowed_tools is not None:
        capabilities["tools"] = [
            name for name in capabilities["tools"] if name in grant.allowed_tools
        ]
    capabilities["child_agents"] = capabilities["child_agents"] and (
        "spawn_agent" in capabilities["tools"]
    )
    capabilities["inputs"] = "write" in grant.actions
    capabilities["questions"] = "write" in grant.actions
    capabilities["cancellation"] = "write" in grant.actions
    capabilities["uploads"] = "files" in grant.actions
    capabilities["approvals"] = "approve" in grant.actions
    return result


def session_view(db, binding, thread):
    revision = db.get(AgentServiceRevision, binding.revision_id)
    return {
        "id": thread.id,
        "agent_id": revision.service_id,
        "revision_id": revision.id,
        "title": thread.title,
        "created_at": thread.created_at,
    }


def create_session(db: Session, client, command: dto.SessionCreate):
    row = callable_agent(db, client, str(command.agent_id), "invoke")
    namespace = access.get_namespace(db, client.id, row.id)

    def execute():
        current = service.revision(db, row)
        thread_data = conversation_service.create_thread(
            db, client.owner_user_id, namespace.workspace_id, command.title
        )
        thread = db.get(Thread, thread_data["id"])
        binding = AgentSessionBinding(
            thread_id=thread.id, namespace_id=namespace.id, revision_id=current.id
        )
        db.add(binding)
        db.flush()
        return session_view(db, binding, thread)

    return runs.idempotent(
        db,
        client.owner_user_id,
        access.external_request_id(client.id, command.request_id),
        "public:session",
        command.model_dump(mode="json"),
        execute,
    )


def scoped_session(db: Session, client, session_id: str, action="read"):
    binding = db.scalar(
        select(AgentSessionBinding)
        .join(ExternalNamespace, ExternalNamespace.id == AgentSessionBinding.namespace_id)
        .where(
            AgentSessionBinding.thread_id == session_id, ExternalNamespace.client_id == client.id
        )
    )
    if binding is None:
        raise AppError("session_not_found", "会话不存在或不可访问。", 404)
    namespace = db.get(ExternalNamespace, binding.namespace_id)
    callable_agent(db, client, namespace.service_id, action)
    thread = conversation_service.get_thread(db, client.owner_user_id, session_id)
    if thread.workspace_id != namespace.workspace_id:
        raise AppError("session_not_found", "会话不存在或不可访问。", 404)
    return binding, thread


def get_session(db: Session, client, session_id: str):
    binding, thread = scoped_session(db, client, session_id)
    return session_view(db, binding, thread)


def create_run(db: Session, client, command: dto.RunCreate):
    row = callable_agent(db, client, str(command.agent_id), "invoke")
    namespace = access.get_namespace(db, client.id, row.id)

    def execute():
        if command.session_id:
            binding, thread = scoped_session(db, client, str(command.session_id), "invoke")
            if binding.namespace_id != namespace.id:
                raise AppError("session_agent_mismatch", "会话不属于指定 Agent。", 422)
        else:
            current = service.revision(db, row)
            thread_data = conversation_service.create_thread(
                db, client.owner_user_id, namespace.workspace_id, "新会话", explicit_title=False
            )
            thread = db.get(Thread, thread_data["id"])
            binding = AgentSessionBinding(
                thread_id=thread.id, namespace_id=namespace.id, revision_id=current.id
            )
            db.add(binding)
            db.flush()
        selected = db.get(AgentServiceRevision, binding.revision_id)
        definition = dto.Definition.model_validate(selected.definition)
        if "model_output_recovery_tokens" not in selected.definition.get("runtime_options", {}):
            # A software default must not add an allowance to a published revision.
            definition.runtime_options.model_output_recovery_tokens = 0
        execution = access.create_execution_binding(
            db, client, row.id, selected.id, namespace.id, definition.model_dump(mode="json")
        )
        # The outer request also binds optional session creation to the request ID.
        # Use a separate deterministic key for the shared Run admission receipt.
        from uuid import NAMESPACE_URL, uuid5

        values = {
            "input": command.input,
            "request_id": str(uuid5(NAMESPACE_URL, f"agent-run:{client.id}:{command.request_id}")),
            "deadline_seconds": command.deadline_seconds,
            "attachments": [item.model_dump(mode="json") for item in command.attachments],
            "model_connection_id": str(definition.model_connection_id),
            "options": definition.runtime_options.model_dump(mode="json"),
        }
        runs.create_run(
            db, client.owner_user_id, thread.id, values, execution_binding_id=execution.id
        )
        run = db.scalar(select(Run).where(Run.execution_binding_id == execution.id))
        return run_view(db, run, execution)

    return runs.idempotent(
        db,
        client.owner_user_id,
        access.external_request_id(client.id, command.request_id),
        "public:run",
        command.model_dump(mode="json"),
        execute,
    )


def scoped_run(db: Session, client, run_id: str, action="read"):
    run = db.scalar(
        select(Run)
        .join(ExecutionBinding, ExecutionBinding.id == Run.execution_binding_id)
        .join(ExternalNamespace, ExternalNamespace.id == ExecutionBinding.namespace_id)
        .where(
            Run.id == run_id,
            ExecutionBinding.client_id == client.id,
            ExternalNamespace.client_id == client.id,
            ExternalNamespace.workspace_id == Run.workspace_id,
        )
    )
    if run is None:
        raise AppError("run_not_found", "任务不存在或不可访问。", 404)
    binding = access.require_run(db, client.id, run, action)
    callable_agent(db, client, binding.service_id, action)
    return run, binding


def run_view(db, run, binding):
    questions = db.scalars(select(Question).where(Question.run_id == run.id))
    approvals = db.scalars(select(Approval).where(Approval.run_id == run.id))
    return {
        "id": run.id,
        "session_id": run.thread_id,
        "agent_id": binding.service_id,
        "revision_id": binding.revision_id,
        "status": run.status,
        "created_at": run.created_at,
        "last_event_seq": run.last_event_seq,
        "final_message_id": run.final_message_id,
        "finalized_event_seq": run.finalized_event_seq,
        "questions": [
            {key: value for key, value in runs.question_view(q).items() if key != "run_id"}
            for q in questions
        ],
        "approvals": [approval_view(item) for item in approvals],
        "error": {"code": "execution_failed", "message": "任务未能完成。"} if run.error else None,
    }


def approval_view(row):
    return {key: getattr(row, key) for key in ("id", "version", "summary", "tool_name", "status")}


def get_run(db, client, run_id):
    return run_view(db, *scoped_run(db, client, run_id))


def cancel(db, client, run_id, command):
    run, binding = scoped_run(db, client, run_id, "write")
    runs.cancel(
        db, client.owner_user_id, run.id, access.external_request_id(client.id, command.request_id)
    )
    return run_view(db, run, binding)


def add_input(db, client, run_id, command):
    from workbench.modules.collaboration.service import steer

    run, _ = scoped_run(db, client, run_id, "write")
    values = command.model_dump(mode="json")
    values["request_id"] = access.external_request_id(client.id, command.request_id)
    return steer(db, client.owner_user_id, run.id, values)


def answer(db, client, run_id, command):
    run, _ = scoped_run(db, client, run_id, "write")
    question = db.get(Question, str(command.question_id))
    if question is None or question.run_id != run.id:
        raise AppError("question_not_found", "问题不存在或不可访问。", 404)
    values = command.model_dump(mode="json", exclude={"question_id"})
    values["request_id"] = access.external_request_id(client.id, command.request_id)
    result = runs.answer_question(db, client.owner_user_id, question.id, values)
    return {key: value for key, value in result.items() if key != "run_id"}


def decide(db, client, run_id, command):
    run, _ = scoped_run(db, client, run_id, "approve")
    approval = db.get(Approval, str(command.approval_id))
    if approval is None or approval.run_id != run.id:
        raise AppError("approval_not_found", "批准请求不存在或不可访问。", 404)
    values = command.model_dump(mode="json", exclude={"approval_id"})
    values["request_id"] = access.external_request_id(client.id, command.request_id)
    runs.decide_approval(db, client.owner_user_id, approval.id, values)
    return approval_view(approval)


def published_artifacts(db, run):
    # Only present_file publications are downloadable. Context, model and tool
    # internals may also be Artifacts; workspace membership does not publish them.
    family = (
        select(Run.id)
        .join(ExecutionBinding, ExecutionBinding.id == Run.execution_binding_id)
        .where(
            (Run.id == run.id) | (Run.root_run_id == run.id),
            ExecutionBinding.namespace_id
            == db.get(ExecutionBinding, run.execution_binding_id).namespace_id,
        )
    )
    operations = db.scalars(
        select(Operation).where(
            Operation.run_id.in_(family),
            Operation.tool_name == "present_file",
            Operation.status == "succeeded",
        )
    )
    identifiers = {
        operation.result.get("artifact_id")
        for operation in operations
        if isinstance(operation.result, dict)
    }
    if not identifiers:
        return []
    return list(
        db.scalars(
            select(Artifact).where(
                Artifact.id.in_(identifiers),
                Artifact.user_id == run.user_id,
                Artifact.workspace_id == run.workspace_id,
                Artifact.status == "available",
            )
        )
    )


def output_view(run_id, artifact):
    return {
        "id": artifact.id,
        "name": artifact.display_name,
        "mime_type": artifact.mime_type,
        "byte_size": artifact.byte_size,
        "version": artifact.content_hash,
        "download_url": f"/agent-api/v1/runs/{run_id}/outputs/{artifact.id}/content"
        f"?version={artifact.content_hash}",
    }


def outputs(db, client, run_id):
    run, binding = scoped_run(db, client, run_id)
    grant = access.authorize_service(db, client.id, binding.service_id, "read")
    messages = db.scalars(
        select(Message)
        .where(Message.run_id == run.id, Message.role == "assistant", Message.status == "completed")
        .order_by(Message.created_at)
    )
    final = db.get(Message, run.final_message_id) if run.final_message_id else None
    return {
        "run_id": run.id,
        "final_answer": rewrite_links(final.content, run.id) if final else None,
        "messages": [rewrite_links(message.content, run.id) for message in messages],
        "artifacts": [output_view(run.id, item) for item in published_artifacts(db, run)]
        if "files" in grant.actions
        else [],
    }


def download(db, client, run_id, artifact_id, version):
    from workbench.modules.files.links import validated_artifact

    run, binding = scoped_run(db, client, run_id)
    access.authorize_service(db, client.id, binding.service_id, "files")
    if artifact_id not in {item.id for item in published_artifacts(db, run)}:
        raise AppError("output_not_found", "产物不存在或不可访问。", 404)
    return validated_artifact(db, client.owner_user_id, run.workspace_id, artifact_id, version)


def public_event(event):
    """Explicit wire projection; unknown events retain only their durable cursor."""
    kind, payload = event["type"], event["payload"]
    allowed = {
        "run.state_changed": {"status"},
        "message.delta": {"message_id", "delta"},
        "run.input_received": {"message_id", "effective"},
        "question.answered": {"question_id"},
        "question.invalidated": {"question_id"},
        "approval.resolved": {"approval_id", "approved"},
        "approval.expired": {"approval_id"},
    }
    if kind == "question.created":
        question = payload.get("question", {})
        payload = {"question": {key: question.get(key) for key in dto.PublicQuestion.model_fields}}
    elif kind == "approval.requested":
        approval = payload.get("approval", {})
        payload = {"approval": {key: approval.get(key) for key in dto.PublicApproval.model_fields}}
    elif kind == "message.completed":
        message = payload.get("message", {})
        if message.get("role") == "assistant":
            payload = {
                "message": {key: message.get(key) for key in ("id", "role", "content", "status")}
            }
        else:
            kind, payload = "run.progress", {}
    elif kind in allowed:
        payload = {key: value for key, value in payload.items() if key in allowed[kind]}
    else:
        kind, payload = "run.progress", {}
    if kind == "message.completed" and "message" in payload:
        payload["message"]["content"] = rewrite_links(
            payload["message"].get("content") or "", event["run_id"]
        )
    if kind == "message.delta":
        payload["delta"] = rewrite_links(payload.get("delta", ""), event["run_id"])
    return {
        key: event[key] for key in ("schema_version", "event_id", "run_id", "seq", "occurred_at")
    } | {
        "type": kind,
        "payload": payload,
    }


_FILE_LINK = re.compile(
    r"/api/v1/workspaces/[0-9a-fA-F-]{36}/(?:file-links|artifacts)/"
    r"(?P<artifact>[0-9a-fA-F-]{36})(?:/content)?\?version=(?P<version>[0-9a-f]{64})"
)


def rewrite_links(content: str, run_id: str) -> str:
    return _FILE_LINK.sub(
        lambda match: (
            f"/agent-api/v1/runs/{run_id}/outputs/{match['artifact']}/content"
            f"?version={match['version']}"
        ),
        content,
    )
