"""Explicit real-SDK stdio + PostgreSQL acceptance; does not call any model/API.

Run from the project root with --output-dir /tmp/<chosen-directory>. The
dedicated mcp_acceptance_<uuid> schema is retained as inspectable test evidence.
"""

import argparse
import asyncio
import json
import socket
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import psycopg
import uvicorn
from psycopg import sql

from workbench.config import PROJECT_ROOT, get_settings
from workbench.modules.mcp.documents_server import build_http_app
from workbench.modules.mcp.domain import Authorization
from workbench.modules.mcp.dto import ConnectionConfig, Continuation, InputAnswer
from workbench.modules.mcp.postgres_acceptance import AcceptancePostgresStore
from workbench.modules.mcp.schema import check_schema, digest
from workbench.modules.mcp.service import MCPService
from workbench.modules.mcp.transport import SessionManager


class NoSecrets:
    async def resolve(self, reference, identity_ref):
        raise PermissionError("Acceptance server has no configured credentials")


class LocalDocumentsOnly:
    http_endpoint = None

    async def check(self, config, identity_ref):
        if (
            identity_ref == "local-protocol-acceptance"
            and config.transport == "streamable_http"
            and config.endpoint == self.http_endpoint
        ):
            return
        if (
            identity_ref != "local-protocol-acceptance"
            or config.transport != "stdio"
            or config.command != sys.executable
            or Path(config.cwd) != PROJECT_ROOT
        ):
            raise PermissionError("Only the explicitly configured local docs process is authorized")


async def run(output: Path):
    output.mkdir(parents=True, exist_ok=False)
    dsn = get_settings().database_dsn()
    if not dsn:
        raise RuntimeError("Real local PostgreSQL is not configured")
    dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)

    @asynccontextmanager
    async def connect():
        async with await psycopg.AsyncConnection.connect(dsn, connect_timeout=3) as connection:
            yield connection

    schema = "mcp_acceptance_" + uuid.uuid4().hex
    store = AcceptancePostgresStore(connect, schema)
    await store.install()
    sessions = SessionManager(NoSecrets(), LocalDocumentsOnly())
    service = MCPService(sessions, store)
    config = ConnectionConfig(
        connection_id="local_docs_acceptance",
        version=1,
        name="真实项目文档协议验收",
        transport="stdio",
        command=sys.executable,
        cwd=str(PROJECT_ROOT),
        args=[str(Path(__file__).with_name("documents_server.py")), "--root", str(PROJECT_ROOT)],
        allowed_tools=["read_document", "read_selected_document"],
        connect_timeout_seconds=10,
        call_timeout_seconds=15,
    )
    records = {"sdk_version": "2.3.0", "postgres_schema": schema, "cases": {}}

    def save(name, value):
        data = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        (output / (name + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2))
        return data

    def grant(snapshot, operation, tool, arguments):
        return Authorization(
            operation_id=operation,
            connection_id=config.connection_id,
            connection_version=config.version,
            identity_ref=snapshot.identity_ref,
            snapshot_id=snapshot.snapshot_id,
            remote_tool=tool,
            arguments_digest=digest(arguments),
            allowed=True,
            effect_policy="read_only",
        )

    try:
        snapshot = await service.discover(config, "local-protocol-acceptance")
        save("modern-directory", snapshot)
        assert snapshot.protocol_version == "2026-07-28"
        args = {"path": "technical/tools-and-recovery.md", "max_lines": 10}
        op, attempt = uuid.uuid4().hex, uuid.uuid4().hex
        authorization = grant(snapshot, op, "read_document", args)
        first = await service.invoke(
            config,
            snapshot,
            "read_document",
            args,
            operation_id=op,
            attempt_id=attempt,
            arguments_ref="acceptance:real-doc-arguments",
            authorization=authorization,
        )
        save("modern-read", first)
        assert first.status == "succeeded", first.model_dump()
        actual = first.structured_content
        expected = "\n".join((PROJECT_ROOT / "docs" / args["path"]).read_text().splitlines()[:10])
        assert actual["text"] == expected and actual["inherited_sensitive_names"] == []
        assert actual["client_capabilities"] == {"elicitation": {"form": {}, "url": {}}}
        assert not Path(f"/proc/{actual['pid']}").exists()
        duplicate = await service.invoke(
            config,
            snapshot,
            "read_document",
            args,
            operation_id=op,
            attempt_id=attempt,
            arguments_ref="acceptance:real-doc-arguments",
            authorization=authorization,
        )
        save("duplicate-reservation", duplicate)
        assert duplicate.exchange_id == first.exchange_id
        assert duplicate.structured_content == first.structured_content
        denied = await service.invoke(
            config,
            snapshot,
            "read_document",
            args,
            operation_id=op,
            attempt_id=uuid.uuid4().hex,
            arguments_ref="acceptance:real-doc-arguments",
            authorization=replace(authorization, effect_policy="side_effect"),
        )
        save("approval-required-despite-readonly-hint", denied)
        assert denied.status == "not_started" and not denied.dispatched
        records["cases"]["modern_real_read_and_duplicate_dedup"] = True
        records["cases"]["stdio_environment_and_owned_process_reaped"] = True
        records["cases"]["annotation_does_not_grant_approval"] = True

        args = {"max_lines": 8}
        op, attempt = uuid.uuid4().hex, uuid.uuid4().hex
        authorization = grant(snapshot, op, "read_selected_document", args)
        waiting = await service.invoke(
            config,
            snapshot,
            "read_selected_document",
            args,
            operation_id=op,
            attempt_id=attempt,
            arguments_ref="acceptance:selection-arguments",
            authorization=authorization,
        )
        save("awaiting-input", waiting)
        assert waiting.status == "awaiting_input", waiting.model_dump()
        # Reconstruct durable state from a new PG connection/store/service object.
        async with connect() as connection:
            cursor = await connection.execute(
                sql.SQL("SELECT outcome FROM {} WHERE id=%s").format(store.table),
                (waiting.exchange_id,),
            )
            persisted = (await cursor.fetchone())[0]
        saved = Continuation.model_validate(persisted["continuation"])
        question = saved.questions[0]
        second_service = MCPService(sessions, AcceptancePostgresStore(connect, schema))
        finished = await second_service.resume(
            config,
            snapshot,
            saved,
            args,
            {
                question.key: InputAnswer(
                    question_version=question.question_version,
                    action="accept",
                    content={"path": "requirements.md"},
                )
            },
            answer_ref="acceptance:explicit-real-document-selection",
            authorization=authorization,
        )
        save("resumed-real-document", finished)
        assert finished.status == "succeeded", finished.model_dump()
        assert finished.structured_content["path"] == "docs/requirements.md"
        records["cases"]["modern_form_pg_persisted_continuation"] = True

        op, attempt = uuid.uuid4().hex, uuid.uuid4().hex
        authorization = grant(snapshot, op, "read_selected_document", args)
        waiting = await service.invoke(
            config,
            snapshot,
            "read_selected_document",
            args,
            operation_id=op,
            attempt_id=attempt,
            arguments_ref="acceptance:lost-transport-arguments",
            authorization=authorization,
        )
        assert waiting.status == "awaiting_input"
        saved = waiting.continuation
        await sessions.release(sessions.get(saved.transport_lease_id))
        lost = await service.resume(
            config,
            snapshot,
            saved,
            args,
            {
                saved.questions[0].key: InputAnswer(
                    question_version=saved.questions[0].question_version,
                    action="accept",
                    content={"path": "requirements.md"},
                )
            },
            answer_ref="acceptance:lost-transport-answer",
            authorization=authorization,
        )
        save("lost-stdio-not-replayed", lost)
        assert lost.status == "outcome_unknown" and not lost.dispatched
        records["cases"]["lost_stdio_never_replays_original_call"] = True

        legacy_config = config.model_copy(update={"mode": "legacy"})
        legacy = await service.discover(legacy_config, "local-protocol-acceptance")
        save("legacy-directory", legacy)
        assert legacy.protocol_version == "2025-11-25"
        args = {"path": "requirements.md", "max_lines": 8}
        op = uuid.uuid4().hex
        result = await service.invoke(
            legacy_config,
            legacy,
            "read_document",
            args,
            operation_id=op,
            attempt_id=uuid.uuid4().hex,
            arguments_ref="acceptance:legacy-arguments",
            authorization=grant(legacy, op, "read_document", args),
        )
        save("legacy-read", result)
        assert result.status == "succeeded", result.model_dump()
        assert result.structured_content["protocol"] == "2025-11-25"
        assert result.structured_content["client_capabilities"] == {}
        records["cases"]["legacy_initialize_real_document_read"] = True

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        http_server = uvicorn.Server(
            uvicorn.Config(
                build_http_app(PROJECT_ROOT),
                log_level="warning",
                access_log=False,
            )
        )
        http_task = asyncio.create_task(http_server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(5):
                while not http_server.started:
                    await asyncio.sleep(0.02)
            endpoint = f"http://127.0.0.1:{port}/mcp/"
            sessions.gate.http_endpoint = endpoint
            config = ConnectionConfig(
                connection_id="local_docs_http_acceptance",
                version=1,
                name="本机真实文档StreamableHTTP验收",
                transport="streamable_http",
                endpoint=endpoint,
                allowed_tools=["read_document", "read_selected_document"],
                call_timeout_seconds=15,
            )
            http_snapshot = await service.discover(config, "local-protocol-acceptance")
            save("http-directory", http_snapshot)
            op = uuid.uuid4().hex
            http_read = await service.invoke(
                config,
                http_snapshot,
                "read_document",
                args,
                operation_id=op,
                attempt_id=uuid.uuid4().hex,
                arguments_ref="acceptance:http-document-arguments",
                authorization=grant(http_snapshot, op, "read_document", args),
            )
            save("http-real-document", http_read)
            assert http_read.status == "succeeded", http_read.model_dump()
            assert http_read.structured_content["path"] == "docs/requirements.md"
            records["cases"]["streamable_http_real_document_read"] = True

            args = {"max_lines": 8}
            op, attempt = uuid.uuid4().hex, uuid.uuid4().hex
            auth = grant(http_snapshot, op, "read_selected_document", args)
            waiting = await service.invoke(
                config,
                http_snapshot,
                "read_selected_document",
                args,
                operation_id=op,
                attempt_id=attempt,
                arguments_ref="acceptance:http-form-arguments",
                authorization=auth,
            )
            save("http-awaiting-input", waiting)
            assert waiting.status == "awaiting_input", waiting.model_dump()
            assert waiting.continuation.transport_lease_id is None
            continuation = Continuation.model_validate_json(waiting.continuation.model_dump_json())
            http_resume = await service.resume(
                config,
                http_snapshot,
                continuation,
                args,
                {
                    continuation.questions[0].key: InputAnswer(
                        question_version=continuation.questions[0].question_version,
                        action="accept",
                        content={"path": "requirements.md"},
                    )
                },
                answer_ref="acceptance:http-form-answer",
                authorization=auth,
            )
            save("http-fresh-transport-resume", http_resume)
            assert http_resume.status == "succeeded", http_resume.model_dump()
            records["cases"]["http_persisted_form_fresh_transport_resume"] = True

            class ActualPostgresFailure:
                reserve = store.reserve
                mark_dispatched = store.mark_dispatched

                async def finish(self, exchange_id, outcome):
                    # Explicit fault injection: a real PG error at response commit,
                    # while the remote tool still reads a real document normally.
                    async with connect() as connection:
                        await connection.execute("SELECT 1/0")

            fault_service = MCPService(sessions, ActualPostgresFailure())
            args = {"path": "requirements.md", "max_lines": 8}
            op, attempt = uuid.uuid4().hex, uuid.uuid4().hex
            auth = grant(http_snapshot, op, "read_document", args)
            failed_commit = await fault_service.invoke(
                config,
                http_snapshot,
                "read_document",
                args,
                operation_id=op,
                attempt_id=attempt,
                arguments_ref="acceptance:pg-failure-document",
                authorization=auth,
            )
            save("actual-pg-response-commit-failure", failed_commit)
            assert failed_commit.status == "outcome_unknown"
            assert failed_commit.response_received and failed_commit.raw_response is not None
            assert failed_commit.code == "RESPONSE_PERSISTENCE_FAILED"
            duplicate = await service.invoke(
                config,
                http_snapshot,
                "read_document",
                args,
                operation_id=op,
                attempt_id=attempt,
                arguments_ref="acceptance:pg-failure-document",
                authorization=auth,
            )
            save("uncommitted-result-never-replayed", duplicate)
            assert duplicate.code == "EXCHANGE_ALREADY_RESERVED" and not duplicate.dispatched
            records["cases"]["real_pg_response_commit_fault_preserves_unknown_no_replay"] = True
        finally:
            http_server.should_exit = True
            await http_task
            sock.close()

        try:
            check_schema(
                {"type": "object", "properties": {"x": {"$ref": "https://example.invalid/x"}}}
            )
        except ValueError:
            records["cases"]["external_schema_ref_rejected_without_fetch"] = True
        else:
            raise AssertionError("External references must be rejected")
        async with connect() as connection:
            cursor = await connection.execute(
                sql.SQL(
                    "SELECT id,operation_id,attempt_id,sequence,may_have_been_sent,outcome FROM {}"
                ).format(store.table)
            )
            rows = await cursor.fetchall()
        save("real-pg-exchanges", [list(row) for row in rows])
        assert len(rows) == 9
        records["passed"] = True
    finally:
        await sessions.aclose()
        save("result", records)
    print(json.dumps(records, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.output_dir))


if __name__ == "__main__":
    main()
