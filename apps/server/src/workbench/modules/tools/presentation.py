"""Bound model observations without discarding the complete operation ledger."""


def agent_view(item: dict) -> dict:
    run = item["run"]
    value = {
        "agent_id": item["agent_id"],
        "name": item["name"],
        "run_id": run["id"],
        "status": run["status"],
        "released": item["released_at"] is not None,
        "state": item.get(
            "state", "idle" if run["status"] in {"succeeded", "failed", "cancelled"} else "busy"
        ),
        "turn_count": item.get("turn_count", 1),
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


def media_view(item: dict) -> dict:
    value = {
        key: item[key]
        for key in ("run_id", "capability", "status", "phase", "progress", "resources_closed")
        if key in item
    }
    value["artifacts"] = [
        {
            key: artifact[key]
            for key in ("id", "version", "filename", "mime_type", "byte_size")
            if key in artifact
        }
        for artifact in item.get("artifacts", [])
    ]
    if item.get("error"):
        value["error"] = item["error"]
    if value["artifacts"]:
        value["next_step"] = "用artifacts中真实id和version调用view_media_output，查看后再交付。"
    elif item.get("status") in {"succeeded", "failed", "cancelled"}:
        value["next_step"] = "没有可查看的产物，请按实际状态报告，不虚构文件。"
    else:
        value["next_step"] = (
            "后台任务尚未完成，尚无artifact_id。run_id仅用于查询或停止任务，不能用于查看文件。"
            "本批工具结束后工作台会自动等待真实结果，再继续模型；不要反复查询、"
            "猜测artifact_id或到工作区寻找后台文件。完成后只用返回的artifacts中真实id/version查看。"
        )
    return value


def model_view(name: str, result: dict) -> dict:
    if result.get("error") is not None:
        return result
    if name in {"spawn_agent", "followup_agent", "release_agent"} and "run" in result:
        return agent_view(result)
    if (
        name
        in {
            "generate_image",
            "generate_video",
            "generate_audio",
            "generate_3d",
            "media_task_status",
            "cancel_media_task",
        }
        and "run_id" in result
    ):
        return media_view(result)
    if name in {"inspect_agents", "wait_agents"} and "agents" in result:
        return {
            "root_status": result["root"]["status"],
            "limit": result["limit"],
            "occupied": result["occupied"],
            "agents": [agent_view(item) for item in result["agents"]],
            "media_tasks": [media_view(item) for item in result.get("media_tasks", [])],
            "file_tasks": [
                {
                    "run_id": item["id"],
                    "status": item["status"],
                    "operation": item["operation"],
                    "error": item.get("error"),
                    "results": [
                        {key: artifact[key] for key in ("id", "version", "filename", "mime_type")}
                        for artifact in item.get("results", [])
                    ],
                    "next_step": "只用results中的真实id读取或查看文件；没有产物时按实际状态报告。",
                }
                for item in result.get("file_tasks", [])
            ],
            "recovery": "需要完整子任务消息时，read_task_history指定run_id读取。",
        }
    return result
