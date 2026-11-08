"""Private listener configuration and ownership-aware optional component control."""

import ipaddress
from contextlib import contextmanager

from pydantic import Field, field_validator, model_validator

from workbench.config import get_settings
from workbench.contracts.errors import AppError
from workbench.contracts.requests import Command
from workbench.infrastructure.access_policy import (
    AccessPolicy,
    AccessPolicyError,
    _authority,
    _host,
    _loopback,
    _url,
)
from workbench.infrastructure.vault import VaultError
from workbench.infrastructure.vault.private import private_directory, read_json, write_json

COMPONENT = "public-api"


class ListenerValues(Command):
    enabled: bool = Field(default=False, strict=True)
    host: str = "127.0.0.1"
    port: int = Field(default=8767, ge=1, le=65535, strict=True)
    public_hosts: list[str] = Field(default_factory=list, max_length=32)
    ssl_certfile: str | None = Field(default=None, max_length=4096)
    ssl_keyfile: str | None = Field(default=None, max_length=4096)

    @field_validator("host")
    @classmethod
    def bind_host(cls, value):
        value = _host(value)
        # Bind literal addresses so safety does not depend on DNS resolution.
        if value == "localhost":
            return "127.0.0.1"
        try:
            return ipaddress.ip_address(value).compressed
        except ValueError:
            raise ValueError("监听地址必须为IP地址或localhost。") from None

    @field_validator("public_hosts")
    @classmethod
    def exact_hosts(cls, value):
        result = []
        for authority in value:
            host, port = _authority(authority, "https")
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address and address.is_unspecified:
                raise ValueError("公开Host不能为通配地址。")
            canonical = _url("https", host, port).removeprefix("https://")
            if canonical not in result:
                result.append(canonical)
        return result

    @model_validator(mode="after")
    def transport(self):
        if (self.ssl_certfile is None) != (self.ssl_keyfile is None):
            raise ValueError("TLS证书和密钥必须同时配置。")
        if not _loopback(self.host) and self.ssl_certfile is None:
            raise ValueError("非回环直连必须配置TLS证书和密钥。")
        if ipaddress.ip_address(self.host).is_unspecified and not self.public_hosts:
            raise ValueError("通配bind必须配置精确公开Host。")
        # Reuse the existing local path parser and explicit TLS file validator.
        self.tls_policy()
        return self

    def tls_policy(self):
        return AccessPolicy.from_values(
            port=self.port,
            secure_cookies=self.ssl_certfile is not None,
            ssl_certfile=self.ssl_certfile,
            ssl_keyfile=self.ssl_keyfile,
        )

    def validate_tls(self):
        try:
            self.tls_policy().validate_tls_files()
        except AccessPolicyError as error:
            raise AppError(error.code, error.message, 422) from None

    def allows(self, host_header: str, scheme: str, peer: str) -> bool:
        try:
            if scheme != ("https" if self.ssl_certfile else "http"):
                return False
            if self.ssl_certfile is None and not _loopback(peer):
                return False
            direct = _authority(host_header, scheme)
            if not ipaddress.ip_address(self.host).is_unspecified and direct == (
                self.host,
                self.port,
            ):
                return True
            # The reverse proxy must preserve an explicitly configured Host.
            # Forwarded/X-Forwarded-* never change scheme, peer or authorization.
            public = _authority(host_header, "https")
            return public in {_authority(item, "https") for item in self.public_hosts}
        except (ValueError, TypeError):
            return False

    def endpoint_url(self):
        if self.public_hosts:
            authority = self.public_hosts[0]
            if _authority(authority, "https") == (self.host, self.port):
                return (
                    _url("https" if self.ssl_certfile else "http", self.host, self.port)
                    + "/agent-api/v1"
                )
            return "https://" + authority + "/agent-api/v1"
        if ipaddress.ip_address(self.host).is_unspecified:
            return None
        return (
            _url("https" if self.ssl_certfile else "http", self.host, self.port) + "/agent-api/v1"
        )


class ListenerConfig(ListenerValues):
    version: int = Field(default=0, ge=0, strict=True)


class ListenerSave(ListenerValues):
    expected_version: int = Field(ge=0, strict=True)


class ListenerView(ListenerConfig):
    running: bool
    url: str | None
    message: str


def path():
    return get_settings().data_dir / "agent-api" / "listener.json"


def load() -> ListenerConfig:
    location = path()
    try:
        if not location.exists() and not location.is_symlink():
            return ListenerConfig()
        private_directory(location.parent)
        return ListenerConfig.model_validate(read_json(location))
    except (ValueError, OSError, VaultError):
        raise AppError(
            "agent_listener_config_invalid",
            "API监听配置无法安全读取，原文件已保留。",
            409,
            "policy",
        ) from None


def _launcher():
    # Lazy import avoids a CLI -> listener -> CLI import cycle and reuses the
    # single launcher lock/process record rather than inventing PID ownership.
    from workbench.entrypoints import cli

    return cli


def _state():
    try:
        return _launcher().read_state()
    except SystemExit:
        raise AppError(
            "agent_listener_state_unavailable",
            "组件记录无法安全读取，原记录已保留。",
            409,
            "policy",
        ) from None


@contextmanager
def _component_lock():
    try:
        with _launcher().instance_lock():
            yield
    except SystemExit:
        # CLI helpers use SystemExit on unsafe runtime files. An owner HTTP
        # request must turn that into a response, never terminate the server.
        raise AppError(
            "agent_listener_state_unavailable",
            "组件运行目录或记录无法安全访问，原记录已保留。",
            409,
            "policy",
        ) from None


def view(state=None):
    config = load()
    state = _state() if state is None else state
    record = state.get(COMPONENT)
    matched = bool(record and _launcher().process_matches(record))
    running = bool(matched and _launcher()._owns_api_listener(record, config))
    message = (
        "监听已就绪。"
        if running
        else (
            "组件进程存在，监听尚未确认就绪。"
            if matched
            else "组件退出或归属尚未确认，请先停止核对现有记录。"
            if record
            else "已保存；启动后生效。"
            if config.enabled
            else "独立API监听默认关闭。"
        )
    )
    if not config.ssl_certfile and any(
        _authority(authority, "https") != (config.host, config.port)
        for authority in config.public_hosts
    ):
        message += "公开入口需由反向代理提供TLS，并透传已配置Host。"
    return {
        **config.model_dump(),
        "running": running,
        "url": config.endpoint_url(),
        "message": message,
    }


def save(command: ListenerSave):
    try:
        with _component_lock():
            current = load()
            if current.version != command.expected_version:
                raise AppError("version_conflict", "监听配置已更新，请重新读取。", 409)
            if COMPONENT in _state():
                raise AppError(
                    "agent_listener_stop_required", "请先停止独立API监听，再修改配置。", 409
                )
            command.validate_tls()
            location = path()
            base = private_directory(get_settings().ensure_data_dir())
            private_directory(base / "agent-api", create=True)
            candidate = ListenerConfig(
                **command.model_dump(exclude={"expected_version"}), version=current.version + 1
            )
            write_json(location, candidate.model_dump(), exclusive=False)
            return view({})
    except (OSError, VaultError):
        raise AppError(
            "agent_listener_save_failed", "监听配置未能安全保存。", 409, "policy"
        ) from None


def ensure_optional(state):
    """Called inside the existing launcher lock; starts only an explicitly enabled component."""
    config = load()
    if not config.enabled:
        return
    config.validate_tls()
    launcher = _launcher()
    if COMPONENT in state:
        if not launcher.process_matches(state[COMPONENT]):
            raise AppError(
                "agent_listener_identity_unconfirmed",
                "原API组件记录尚未确认，请先停止核对。",
                409,
                "policy",
            )
        return
    state[COMPONENT] = {**launcher.launch_component(COMPONENT), "listener_version": config.version}
    launcher.write_state(state)


def start():
    with _component_lock():
        config = load()
        if not config.enabled:
            raise AppError("agent_listener_disabled", "请先保存启用配置，再启动独立API监听。", 409)
        state = _state()
        ensure_optional(state)
        return view(state)


def stop():
    launcher = _launcher()
    with _component_lock():
        state = _state()
        record = state.get(COMPONENT)
        if record is None:
            return view(state)
        result = launcher.terminate_owned(record, timeout=15, force=False)
        if result.status != "confirmed":
            raise AppError(
                "agent_listener_stop_unconfirmed",
                "API组件归属或退出未确认，原记录已保留。",
                409,
                "policy",
            )
        del state[COMPONENT]
        launcher.write_state(state)
        return view(state)


def run():
    """CLI child entry; reads persistent configuration before uvicorn binds any socket."""
    import uvicorn

    config = load()
    if not config.enabled:
        raise SystemExit("独立API监听未启用，未启动服务。")
    config.validate_tls()
    uvicorn.run(
        "workbench.entrypoints.public_api:app",
        host=config.host,
        port=config.port,
        proxy_headers=False,
        # Close streaming transports within the ownership stop deadline. Durable
        # Runs live in workers and are independent of an HTTP connection.
        timeout_graceful_shutdown=10,
        ssl_certfile=config.ssl_certfile,
        ssl_keyfile=config.ssl_keyfile,
    )
