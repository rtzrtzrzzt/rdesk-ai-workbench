"""Bound model observations without discarding the complete operation ledger."""


def agent_view(item: dict) -> dict:
    run = item["run"]
    value = {
        "agent_id": item["agent_id"],
        "name": item["name"],
        "run_id": run["id"],
        "status": run["status"],
        "released": item["released_at"] is not None,
    }
    if run.get("error"):
        value["error"] = run["error"]
    if item.get("result"):
        value["result"] = item["result"][:300]
        value["result_truncated"] = len(item["result"]) > 300
    for key in ("questions", "approvals"):
        if run.get(key):
            value[key] = [{"id": row["id"], "status": row["status"]} for row in run[key]]
    return value


def model_view(name: str, result: dict) -> dict:
    if result.get("error") is not None:
        return result
    if name == "spawn_agent" and "run" in result:
        return agent_view(result)
    if name in {"inspect_agents", "wait_agents"} and "agents" in result:
        return {
            "root_status": result["root"]["status"],
            "limit": result["limit"],
            "occupied": result["occupied"],
            "agents": [agent_view(item) for item in result["agents"]],
            "recovery": "需要完整子任务消息时，read_task_history指定run_id读取。",
        }
    return result
