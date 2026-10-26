from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from workbench.contracts.errors import AppError
from workbench.infrastructure.database import now
from workbench.infrastructure.secrets import decrypt, encrypt
from workbench.modules.models.domain import ModelConnection, ModelRevision
from workbench.modules.models.engine_adapters import (
    reasoning_choices,
    supported_reasoning_efforts,
    validate_generation,
)
from workbench.modules.models.managed import default_connection
from workbench.modules.models.profile import (
    CAPABILITIES,
    Generation,
    adapter_route_matrix,
    normalized,
)


def view(row: ModelConnection) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "provider_type": row.provider_type,
        "endpoint": row.endpoint,
        "model_identifier": row.model_identifier,
        "credential_configured": row.encrypted_api_key is not None,
        "allow_remote": row.allow_remote,
        "context_window": row.context_window,
        "service_status": row.service_status,
        "capabilities": normalized(row.capabilities),
        "adapter_routes": adapter_route_matrix(row.provider_type),
        "generation": Generation.model_validate(row.generation or {}).model_dump(mode="json"),
        "reasoning_efforts": supported_reasoning_efforts(
            {
                "provider_type": row.provider_type,
                "endpoint": row.endpoint,
                "model_identifier": row.model_identifier,
                "generation": row.generation or {},
            }
        ),
        "reasoning_choices": reasoning_choices(
            {
                "provider_type": row.provider_type,
                "endpoint": row.endpoint,
                "model_identifier": row.model_identifier,
                "generation": row.generation or {},
            }
        ),
        "version": row.version,
    }


def get(db: Session, user_id: str, connection_id: str, *, lock: bool = False) -> ModelConnection:
    query = select(ModelConnection).where(
        ModelConnection.id == connection_id,
        ModelConnection.user_id == user_id,
    )
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise AppError("model_not_found", "模型连接不存在或不可访问。", 404)
    return row


def list_connections(db: Session, user_id: str) -> list[dict]:
    return [
        view(row)
        for row in db.scalars(
            select(ModelConnection)
            .where(
                ModelConnection.user_id == user_id,
            )
            .order_by(ModelConnection.created_at)
        )
    ]


def validate_endpoint(endpoint: str, allow_remote: bool, *, enforce_policy: bool = True) -> str:
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AppError("invalid_endpoint", "模型地址必须是 HTTP 或 HTTPS URL。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AppError("invalid_endpoint", "模型地址不能包含凭据、查询参数或片段。")
    local = parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
    if enforce_policy and not local and not allow_remote:
        raise AppError("remote_not_allowed", "此连接尚未允许向外部服务传输数据。", 403, "policy")
    return endpoint.rstrip("/")


def create(db: Session, user_id: str, values: dict) -> dict:
    key = values.pop("api_key", None)
    endpoint = validate_endpoint(
        values["endpoint"], values.get("allow_remote", False), enforce_policy=False
    )
    values["generation"] = validate_generation(
        values["provider_type"], endpoint, values["model_identifier"], values.get("generation")
    )
    row = ModelConnection(
        user_id=user_id,
        **{**values, "endpoint": endpoint},
        encrypted_api_key=encrypt(key) if key else None,
        capabilities=managed_capabilities({**values, "endpoint": endpoint}),
    )
    db.add(row)
    db.flush()
    return view(row)


def managed_capabilities(values: dict) -> dict:
    managed = default_connection()
    if managed and all(
        values.get(key) == managed.get(key)
        for key in ("provider_type", "endpoint", "model_identifier", "context_window")
    ):
        return normalized(managed["capabilities"])
    return {}


def update(db: Session, user_id: str, connection_id: str, values: dict) -> dict:
    row = get(db, user_id, connection_id, lock=True)
    if row.version != values.pop("expected_version"):
        raise AppError("model_version_conflict", "连接已被更新，请重新读取后修改。", 409)
    key, clear = values.pop("api_key", None), values.pop("clear_api_key", False)
    values["endpoint"] = validate_endpoint(
        values["endpoint"], values["allow_remote"], enforce_policy=False
    )
    values["generation"] = validate_generation(
        values["provider_type"],
        values["endpoint"],
        values["model_identifier"],
        values.get("generation"),
    )
    changed = any(
        getattr(row, key) != values[key]
        for key in ("endpoint", "model_identifier", "provider_type", "context_window")
    )
    for name, value in values.items():
        setattr(row, name, value)
    if key:
        row.encrypted_api_key = encrypt(key)
    elif clear:
        row.encrypted_api_key = None
    if changed:
        row.capabilities = {
            name: {**value, "support": "unknown", "evidence_status": "stale"}
            for name, value in normalized(row.capabilities).items()
        }
        row.service_status, row.checked_at = "unchecked", None
    row.version += 1
    db.flush()
    return view(row)


def declare_capabilities(db: Session, user_id: str, connection_id: str, command) -> dict:
    row = get(db, user_id, connection_id, lock=True)
    if row.version != command.expected_version:
        raise AppError("model_version_conflict", "能力配置已变化，请重新读取后修改。", 409)
    if set(command.declarations) - CAPABILITIES:
        raise AppError("capability_unknown", "能力名称未注册。", 422)
    capabilities = normalized(row.capabilities)
    for name, declaration in command.declarations.items():
        capabilities[name] = {
            "support": declaration.support,
            "enabled": declaration.enabled,
            "evidence_status": "manually_asserted",
            "evidence_source": declaration.note or "用户在连接设置中声明；尚未实测",
            "checked_at": now().isoformat(),
            "deployment_revision": None,
        }
    row.capabilities, row.version = capabilities, row.version + 1
    db.flush()
    return view(row)


def import_managed(db: Session, user_id: str, connection_id: str) -> dict:
    row = get(db, user_id, connection_id, lock=True)
    capabilities = managed_capabilities(view(row))
    if not capabilities:
        raise AppError(
            "deployment_evidence_mismatch",
            "此连接与本机已验证部署的地址、协议、模型和上下文不一致。",
            409,
        )
    row.capabilities, row.version = capabilities, row.version + 1
    db.flush()
    return view(row)


def freeze(db: Session, user_id: str, connection_id: str) -> ModelRevision:
    model = get(db, user_id, connection_id, lock=True)
    revision = db.scalar(
        select(ModelRevision).where(
            ModelRevision.connection_id == model.id,
            ModelRevision.connection_version == model.version,
        )
    )
    if revision is None:
        configuration = view(model)
        revision = ModelRevision(
            connection_id=model.id,
            connection_version=model.version,
            configuration=configuration,
            encrypted_api_key=model.encrypted_api_key,
        )
        db.add(revision)
        db.flush()
    return revision


async def check_connection(db: Session, user_id: str, connection_id: str) -> dict:
    row = get(db, user_id, connection_id)
    endpoint = validate_endpoint(row.endpoint, row.allow_remote)
    identifier = row.model_identifier
    provider_type = row.provider_type
    base = endpoint if endpoint.endswith("/v1") else endpoint + "/v1"
    headers = {}
    key = decrypt(row.encrypted_api_key)
    if key:
        if provider_type == "anthropic":
            headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        else:
            headers = {"Authorization": f"Bearer {key}"}
    # Release the authentication/configuration read transaction before network I/O.
    db.rollback()
    status = "unavailable"
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
            response = await client.get(base + "/models", headers=headers)
            response.raise_for_status()
            listing = response.json()
            identifiers = {
                item.get("id") for item in listing.get("data", []) if isinstance(item, dict)
            }
            status = "ready" if identifier in identifiers else "unavailable"
    except (httpx.HTTPError, ValueError, TypeError):
        pass
    row = get(db, user_id, connection_id)
    if (row.endpoint.rstrip("/"), row.model_identifier, row.provider_type) != (
        endpoint,
        identifier,
        provider_type,
    ):
        raise AppError("connection_changed", "检查期间连接配置已改变，请重新检查。", 409)
    row.service_status = status
    row.checked_at = now()
    row.version += 1
    db.flush()
    return view(row)
