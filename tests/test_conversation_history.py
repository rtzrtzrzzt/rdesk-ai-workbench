"""Offline history boundary checks using explicitly synthetic protocol inputs.

These checks neither read user conversations nor establish live UI/model acceptance.
"""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from workbench.contracts.errors import AppError
from workbench.modules.conversation_history.dto import (
    ListConversations,
    ReadConversation,
    SearchConversations,
)
from workbench.modules.conversation_history.policy import effective_scope, thread_allowed
from workbench.modules.conversation_history.service import text_slice


def test_child_history_permission_never_expands_beyond_its_task_tree():
    assert effective_scope("disabled") == "disabled"
    assert effective_scope("workspace") == "workspace"
    assert effective_scope("user") == "user"
    assert effective_scope("disabled", is_child=True) == "disabled"
    assert effective_scope("workspace", is_child=True) == "task_tree"
    assert effective_scope("user", is_child=True) == "task_tree"


def test_permission_rejects_other_users_and_deleted_threads_in_every_enabled_scope():
    actor = SimpleNamespace(user_id="protocol-user", workspace_id="protocol-workspace")
    for scope in ("workspace", "user", "task_tree"):
        other = SimpleNamespace(
            id="protocol-thread",
            user_id="other-user",
            workspace_id=actor.workspace_id,
            deleting=False,
        )
        assert not thread_allowed(actor, other, scope, task_thread_ids=(other.id,))
        deleting = SimpleNamespace(**{**vars(other), "user_id": actor.user_id, "deleting": True})
        assert not thread_allowed(actor, deleting, scope, task_thread_ids=(deleting.id,))


def test_workspace_and_disabled_permissions_do_not_inherit_user_wide_access():
    actor = SimpleNamespace(user_id="protocol-user", workspace_id="protocol-workspace")
    thread = SimpleNamespace(
        id="protocol-thread",
        user_id=actor.user_id,
        workspace_id="other-workspace",
        deleting=False,
    )
    assert not thread_allowed(actor, thread, "workspace")
    assert thread_allowed(actor, thread, "user")
    thread.workspace_id = actor.workspace_id
    assert thread_allowed(actor, thread, "workspace")
    assert not thread_allowed(actor, thread, "disabled", task_thread_ids=(thread.id,))


def test_child_cannot_read_an_independent_conversation_in_the_same_workspace():
    actor = SimpleNamespace(user_id="protocol-user", workspace_id="protocol-workspace")
    thread = SimpleNamespace(
        id="independent-thread",
        user_id=actor.user_id,
        workspace_id=actor.workspace_id,
        deleting=False,
    )
    assert not thread_allowed(actor, thread, "task_tree", task_thread_ids=("task-thread",))
    assert thread_allowed(actor, thread, "task_tree", task_thread_ids=(thread.id,))


def test_read_and_search_schemas_cannot_supply_a_workspace_or_authorization_scope():
    assert ListConversations().scope == "current"
    assert ReadConversation(thread_id=uuid4()).include_tools is False
    with pytest.raises(ValidationError):
        ListConversations(scope="user")
    with pytest.raises(ValidationError):
        ListConversations(workspace_id=uuid4())
    with pytest.raises(ValidationError):
        ReadConversation(thread_id=uuid4(), workspace_id=uuid4())
    with pytest.raises(ValidationError):
        SearchConversations(query="")


def test_unicode_character_pagination_reconstructs_the_exact_message():
    content = "协议片段α👩‍💻\n" * 40
    chunks = []
    start = 0
    while True:
        page = text_slice(content, start, 128)
        chunks.append(page["content"])
        assert page["start_character"] == start
        assert page["total_characters"] == len(content)
        if page["next_character"] is None:
            assert page["truncated"] is False
            break
        assert page["truncated"] is True
        assert page["next_character"] > start
        start = page["next_character"]
    assert "".join(chunks) == content
    assert text_slice(content, len(content), 128)["content"] == ""
    with pytest.raises(AppError) as invalid:
        text_slice(content, len(content) + 1, 128)
    assert invalid.value.code == "history_cursor_invalid"


@pytest.mark.parametrize("change", ["settings", "scope", "source_message", "memory", "transfer"])
def test_cached_excerpt_is_removed_when_live_permission_or_source_changes(monkeypatch, change):
    from workbench.modules.context import transfers
    from workbench.modules.conversation_history import provenance, service

    actor = SimpleNamespace(user_id="protocol-user", workspace_id="protocol-workspace")
    thread = SimpleNamespace(id="protocol-thread", version=1, workspace_id=actor.workspace_id)
    row = SimpleNamespace(
        id="protocol-message",
        run_id="protocol-source-run",
        role="assistant",
        status="completed",
        content="synthetic source excerpt",
        attachments=[],
    )
    live = {"settings_version": 1, "scope": "workspace", "memory": "generation-1", "transfer": True}
    monkeypatch.setattr(
        provenance.settings,
        "view",
        lambda *_: SimpleNamespace(version=live["settings_version"]),
    )
    monkeypatch.setattr(provenance.settings, "scope_for", lambda *_: (live["scope"], ()))
    monkeypatch.setattr(provenance, "memory_state", lambda *_: live["memory"])
    monkeypatch.setattr(service, "authorized_thread", lambda *_: thread)
    monkeypatch.setattr(service, "visible_rows", lambda *_, **__: [row])
    monkeypatch.setattr(transfers, "allowed_result", lambda *_: live["transfer"])
    db = SimpleNamespace(get=lambda *_: row)
    result = {
        "conversation_history_ref": provenance.stamp(db, actor, [thread], [(thread, row)]),
        "excerpt": row.content,
    }
    message = {
        "role": "tool",
        "tool_call_id": "protocol-history",
        "content": json.dumps(result),
    }
    assert provenance.filter_cached(db, actor, message, result) == message

    if change == "settings":
        live["settings_version"] += 1
    elif change == "scope":
        live["scope"] = "disabled"
    elif change == "source_message":
        row.content = "synthetic changed source"
    elif change == "memory":
        live["memory"] = "generation-2"
    else:
        live["transfer"] = False
    filtered = provenance.filter_cached(db, actor, message, result)
    assert "synthetic source excerpt" not in filtered["content"]
    assert json.loads(filtered["content"])["error"]["code"] == "conversation_history_stale"
    assert filtered["tool_call_id"] == message["tool_call_id"]
