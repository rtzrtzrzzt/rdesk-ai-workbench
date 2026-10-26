"""The user's live privacy setting bounds every query, including child agents."""


def effective_scope(scope, is_child=False):
    return "disabled" if scope == "disabled" else "task_tree" if is_child else scope


def thread_allowed(actor, thread, scope, task_thread_ids=()):
    if scope == "disabled" or thread.user_id != actor.user_id or thread.deleting:
        return False
    if scope == "task_tree":
        return thread.id in task_thread_ids and thread.workspace_id == actor.workspace_id
    if scope == "workspace":
        return thread.workspace_id == actor.workspace_id
    return scope == "user"
