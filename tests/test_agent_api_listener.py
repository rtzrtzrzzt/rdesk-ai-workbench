"""Isolated listener configuration/control checks; no socket or process is started."""

import asyncio
import json
import stat
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from starlette.requests import Request
from workbench.contracts.errors import AppError
from workbench.entrypoints import cli, public_api
from workbench.modules.access import listener


@pytest.fixture
def environment(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setattr(
        listener,
        "get_settings",
        lambda: SimpleNamespace(data_dir=tmp_path, ensure_data_dir=lambda: tmp_path),
    )
    state = {}
    launcher = SimpleNamespace(
        instance_lock=nullcontext,
        read_state=lambda: state,
        process_matches=MagicMock(return_value=True),
        _owns_api_listener=MagicMock(return_value=False),
        launch_component=MagicMock(return_value={"pid": 123, "instance_id": "contract-test"}),
        write_state=MagicMock(),
        terminate_owned=MagicMock(return_value=SimpleNamespace(status="confirmed")),
    )
    monkeypatch.setattr(listener, "_launcher", lambda: launcher)
    return tmp_path, state, launcher


def test_listener_default_is_closed_without_creating_config(environment):
    directory, _, launcher = environment
    config = listener.load()
    assert not config.enabled
    assert config.host == "127.0.0.1" and config.port == 8767 and config.version == 0
    assert not (directory / "agent-api").exists()
    launcher.launch_component.assert_not_called()


def test_config_is_private_atomic_versioned_and_requires_stopping_record(environment):
    _, state, launcher = environment
    saved = listener.save(listener.ListenerSave(enabled=True, expected_version=0))
    assert saved["version"] == 1 and not saved["running"]
    assert stat.S_IMODE(listener.path().stat().st_mode) == 0o600
    original = listener.path().read_bytes()
    with pytest.raises(AppError) as stale:
        listener.save(listener.ListenerSave(expected_version=0))
    assert stale.value.code == "version_conflict"
    state["public-api"] = {"unconfirmed": "record"}
    with pytest.raises(AppError) as running:
        listener.save(listener.ListenerSave(expected_version=1))
    assert running.value.code == "agent_listener_stop_required"
    assert listener.path().read_bytes() == original
    launcher.launch_component.assert_not_called()


@pytest.mark.parametrize(
    "values",
    [
        {"host": "0.0.0.0"},
        {"host": "192.0.2.1"},
        {"host": "api.example.com"},
        {"public_hosts": ["*"]},
        {"public_hosts": ["https://api.example.com"]},
        {"public_hosts": ["api.example.com/path"]},
        {"public_hosts": ["0.0.0.0"]},
        {"ssl_certfile": "/tmp/missing-cert"},
    ],
)
def test_plaintext_remote_bind_and_ambiguous_hosts_are_rejected(values):
    with pytest.raises(ValidationError):
        listener.ListenerValues(**values)


def test_loopback_proxy_checks_exact_host_and_actual_peer_without_forwarded_trust():
    config = listener.ListenerValues(host="localhost", public_hosts=["api.example.com:443"])
    assert config.host == "127.0.0.1"
    assert config.allows("api.example.com", "http", "127.0.0.1")
    assert config.allows("127.0.0.1:8767", "http", "127.0.0.1")
    assert not config.allows("evil.example", "http", "127.0.0.1")
    assert not config.allows("api.example.com", "http", "192.0.2.1")
    assert not config.allows("api.example.com", "https", "127.0.0.1")
    assert not config.allows("api.example.com:444", "http", "127.0.0.1")


@pytest.mark.parametrize(
    ("host", "public_hosts", "expected"),
    [
        ("127.0.0.1", [], "http://127.0.0.1:8767/agent-api/v1"),
        ("127.0.0.1", ["127.0.0.1:8767"], "http://127.0.0.1:8767/agent-api/v1"),
        ("::1", ["[::1]:8767"], "http://[::1]:8767/agent-api/v1"),
        ("127.0.0.1", ["api.example.com"], "https://api.example.com/agent-api/v1"),
    ],
)
def test_displayed_endpoint_preserves_direct_transport_and_proxy_https(
    environment, host, public_hosts, expected
):
    view = listener.save(
        listener.ListenerSave(host=host, public_hosts=public_hosts, expected_version=0)
    )
    assert view["url"] == expected
    assert ("反向代理" in view["message"]) == expected.startswith("https://")


def test_tls_preflight_reads_real_matching_material_without_mutating_or_leaking_it(environment):
    directory, _, _ = environment
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "listener-contract.invalid")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "listener.crt", directory / "listener.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    original = key_path.read_bytes(), key_path.stat().st_mtime_ns
    result = listener.save(
        listener.ListenerSave(
            enabled=True,
            host="0.0.0.0",
            public_hosts=["api.example.com"],
            ssl_certfile=str(cert_path),
            ssl_keyfile=str(key_path),
            expected_version=0,
        )
    )
    assert (key_path.read_bytes(), key_path.stat().st_mtime_ns) == original
    assert "PRIVATE KEY" not in json.dumps(result)
    assert result["url"] == "https://api.example.com/agent-api/v1"
    key_path.chmod(0o644)
    with pytest.raises(AppError):
        listener.ListenerSave(
            enabled=True, ssl_certfile=str(cert_path), ssl_keyfile=str(key_path), expected_version=1
        ).validate_tls()


def test_start_stop_only_use_recorded_optional_component_and_keep_unconfirmed_state(environment):
    _, state, launcher = environment
    listener.save(listener.ListenerSave(enabled=True, expected_version=0))
    state["api"] = {"owner": "existing-owner-api"}
    listener.start()
    listener.start()
    launcher.launch_component.assert_called_once_with("public-api")
    original_record = state["public-api"]
    launcher.terminate_owned.return_value = SimpleNamespace(status="unconfirmed")
    with pytest.raises(AppError) as unknown:
        listener.stop()
    assert unknown.value.code == "agent_listener_stop_unconfirmed"
    assert state["public-api"] == original_record
    launcher.terminate_owned.return_value = SimpleNamespace(status="confirmed")
    listener.stop()
    assert state == {"api": {"owner": "existing-owner-api"}}
    launcher.terminate_owned.assert_called_with(original_record, timeout=15, force=False)


def test_cli_public_api_dispatch_does_not_start_owner_components(monkeypatch):
    execute = MagicMock()
    monkeypatch.setattr(listener, "run", execute)
    monkeypatch.setattr("sys.argv", ["workbench", "public-api", "--instance-id", "contract-test"])
    cli.main()
    execute.assert_called_once_with()


def test_public_child_loads_saved_config_and_bounds_shutdown_without_trusting_proxies(
    environment, monkeypatch
):
    execute = MagicMock()
    monkeypatch.setattr("uvicorn.run", execute)
    with pytest.raises(SystemExit):
        listener.run()
    execute.assert_not_called()
    listener.save(listener.ListenerSave(enabled=True, port=9876, expected_version=0))
    listener.run()
    assert execute.call_args.args == ("workbench.entrypoints.public_api:app",)
    assert execute.call_args.kwargs["port"] == 9876
    assert execute.call_args.kwargs["proxy_headers"] is False
    assert execute.call_args.kwargs["timeout_graceful_shutdown"] == 10


def test_unsafe_cli_runtime_record_becomes_http_error_instead_of_system_exit(environment):
    _, _, launcher = environment
    launcher.instance_lock = MagicMock(side_effect=SystemExit("unsafe runtime"))
    with pytest.raises(AppError) as denied:
        listener.start()
    assert denied.value.code == "agent_listener_state_unavailable"
    launcher.launch_component.assert_not_called()


def test_public_request_releases_maintenance_gate_before_sse_stream_and_cancel_can_interrupt(
    monkeypatch,
):
    state = {"held": False, "interrupt": None}

    @contextmanager
    def gate(*, interrupt_maintenance):
        state["held"], state["interrupt"] = True, interrupt_maintenance
        try:
            yield
        finally:
            state["held"] = False

    monkeypatch.setattr(public_api, "write_gate", gate)
    monkeypatch.setattr(listener, "load", lambda: listener.ListenerConfig(enabled=True))

    async def execute(path):
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": path,
                "headers": [(b"host", b"127.0.0.1:8767")],
                "scheme": "http",
                "query_string": b"",
                "server": ("127.0.0.1", 8767),
                "client": ("127.0.0.1", 50000),
            }
        )

        async def stream():
            assert not state["held"]
            yield "event"

        async def next_response(_):
            assert state["held"]
            return StreamingResponse(stream())

        response = await public_api.public_boundary(request, next_response)
        assert not state["held"]
        assert [item async for item in response.body_iterator] == ["event"]

    asyncio.run(execute("/agent-api/v1/runs/task/events"))
    assert state["interrupt"] is False
    asyncio.run(execute("/agent-api/v1/runs/task/cancel"))
    assert state["interrupt"] is True
