"""Offline contract tests; no production database, listener or model is used."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from workbench.contracts.errors import AppError
from workbench.entrypoints import public_api
from workbench.modules.access.listener import ListenerConfig
from workbench.modules.agent_services import dto, public


@pytest.fixture
def http(monkeypatch):
    monkeypatch.setattr(public_api.listener, "load", lambda: ListenerConfig(enabled=True))
    monkeypatch.setattr(public_api, "write_gate", lambda **_: nullcontext())
    db = MagicMock()

    def database():
        yield db

    public_api.app.dependency_overrides[public_api.get_db] = database
    with TestClient(
        public_api.app, base_url="http://127.0.0.1:8767", client=("127.0.0.1", 50000)
    ) as client:
        yield client, db
    public_api.app.dependency_overrides.clear()


def test_public_listener_defaults_closed_and_never_accepts_owner_cookie(http, monkeypatch):
    client, _ = http
    authenticate = MagicMock()
    monkeypatch.setattr(public_api.access, "authenticate", authenticate)
    client.cookies.set("workbench_session", "owner-cookie")
    response = client.get("/agent-api/v1/agents")
    assert response.status_code == 401
    authenticate.assert_not_called()
    monkeypatch.setattr(public_api.listener, "load", lambda: ListenerConfig(enabled=False))
    assert client.get("/agent-api/v1/agents").status_code == 404


def test_bearer_identity_is_used_without_forwarding_to_owner_api(http, monkeypatch):
    client, db = http
    identity = SimpleNamespace(id=str(uuid4()), owner_user_id=str(uuid4()))
    authenticate = MagicMock(return_value=identity)
    monkeypatch.setattr(public_api.access, "authenticate", authenticate)
    listing = MagicMock(return_value=[])
    monkeypatch.setattr(public_api.public, "list_agents", listing)
    response = client.get("/agent-api/v1/agents", headers={"Authorization": "Bearer test-token"})
    assert response.status_code == 200
    assert response.json() == []
    authenticate.assert_called_once_with(db, "test-token")
    listing.assert_called_once_with(db, identity)
    db.commit.assert_called_once()


def test_owner_and_compatibility_management_endpoints_are_absent(http):
    client, _ = http
    for path in (
        "/api/v1/auth/bootstrap",
        "/api/v1/settings",
        "/api/v1/agent-services",
        "/agent-api/v1/settings",
        "/v1/responses",
        "/.well-known/agent-card.json",
    ):
        assert client.get(path).status_code == 404
    paths = public_api.app.openapi()["paths"]
    assert all(path.startswith("/agent-api/v1/") for path in paths)
    for route, model in (
        ("/agent-api/v1/runs/{run_id}/changes", "ChangeSetView"),
        ("/agent-api/v1/runs/{run_id}/changes/{change_id}", "ChangeDetails"),
    ):
        schema = paths[route]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema == {"$ref": f"#/components/schemas/{model}"}
    security = public_api.app.openapi()["components"]["securitySchemes"]
    assert set(security) == {"HTTPBearer"}


@pytest.mark.parametrize(
    "tools",
    [
        ["read_settings"],
        ["exec_command"],
        ["mcp_call"],
        ["memory_read"],
        ["read_file", "read_file"],
    ],
)
def test_publication_rejects_unscoped_or_duplicate_tools(tools):
    with pytest.raises(ValidationError):
        dto.Definition(model_connection_id=uuid4(), allowed_tools=tools)


def test_public_run_cannot_override_server_execution_policy():
    with pytest.raises(ValidationError):
        dto.RunCreate(
            agent_id=uuid4(),
            input="task",
            request_id=uuid4(),
            options={"sandbox_profile": "native_scoped"},
        )
    with pytest.raises(ValidationError):
        dto.RunCreate(
            agent_id=uuid4(), input="task", request_id=uuid4(), model_connection_id=uuid4()
        )


def test_session_scope_rejects_unknown_or_other_client_before_owner_lookup(monkeypatch):
    db = MagicMock()
    db.scalar.return_value = None
    owner_lookup = MagicMock()
    monkeypatch.setattr(public.conversation_service, "get_thread", owner_lookup)
    with pytest.raises(AppError) as denied:
        public.scoped_session(db, SimpleNamespace(id=str(uuid4())), str(uuid4()))
    assert denied.value.status == 404
    owner_lookup.assert_not_called()


def event(kind, payload):
    return {
        "schema_version": "1",
        "event_id": str(uuid4()),
        "run_id": str(uuid4()),
        "seq": 8,
        "occurred_at": "2026-11-02T00:00:00Z",
        "type": kind,
        "payload": payload,
    }


def test_event_projection_retains_cursor_without_private_snapshot_or_reasoning():
    source = event(
        "run.started",
        {"config_snapshot": {"endpoint": "private-endpoint"}, "storage_path": "/private/path"},
    )
    result = public.public_event(source)
    assert result["seq"] == source["seq"]
    assert result["type"] == "run.progress"
    assert result["payload"] == {}
    assert "private" not in str(result)
    assert (
        public.public_event(event("message.reasoning_delta", {"delta": "private"}))["payload"] == {}
    )


def test_message_and_approval_projection_exclude_internal_fields_and_rewrite_links():
    run_id, artifact_id, workspace_id = str(uuid4()), str(uuid4()), str(uuid4())
    version = "a" * 64
    source = event(
        "message.completed",
        {
            "message": {
                "id": str(uuid4()),
                "role": "assistant",
                "status": "completed",
                "content": (
                    f"[file](/api/v1/workspaces/{workspace_id}/file-links/{artifact_id}"
                    f"?version={version})"
                ),
                "reasoning": "private",
                "provider_message": {"credentials": "private"},
            }
        },
    )
    source["run_id"] = run_id
    result = public.public_event(source)
    assert "private" not in str(result)
    assert f"/agent-api/v1/runs/{run_id}/outputs/{artifact_id}/content?version={version}" in str(
        result
    )
    approval = public.public_event(
        event(
            "approval.requested",
            {
                "approval": {
                    "id": str(uuid4()),
                    "version": 1,
                    "summary": "save",
                    "tool_name": "write_file",
                    "status": "pending",
                    "arguments": {"private_path": "/private/path"},
                    "review": "private",
                }
            },
        )
    )
    assert set(approval["payload"]["approval"]) == set(dto.PublicApproval.model_fields)
    assert "private" not in str(approval)


def test_event_cursor_requires_same_run_and_handles_resume():
    run_id = str(uuid4())
    assert public_api.event_cursor(run_id, 3, f"{run_id}:5") == 5
    assert public_api.event_cursor(run_id, 7, f"{run_id}:5") == 7
    for value in (f"{uuid4()}:5", f"{run_id}:-1", "not-a-cursor"):
        with pytest.raises(AppError):
            public_api.event_cursor(run_id, 0, value)
