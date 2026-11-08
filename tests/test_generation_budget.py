"""Auxiliary policy and isolated-PG integration; no inference or product acceptance."""

from datetime import timedelta
from pathlib import Path

import pytest
from test_run_presentation import (  # noqa: F401 - shared private PG fixtures
    authorization_engine,
    isolated_postgres_engine,
    run_facts,
)
from workbench.contracts.errors import AppError
from workbench.contracts.requests import RunOptions
from workbench.infrastructure.database import new_id, now
from workbench.modules.models import execution
from workbench.modules.models import service as models
from workbench.modules.models.domain import ModelConnection
from workbench.modules.models.profile import Generation
from workbench.modules.preferences import service as preferences
from workbench.modules.preferences.domain import RuntimeDefaults, WorkspaceRuntimeDefaults
from workbench.modules.runs.budget import generation_budget
from workbench.modules.runs.domain import Run


def test_context_capacity_is_not_a_generation_allowance():
    run = Run(created_at=now(), config_snapshot={"runtime_options": RunOptions().model_dump()})
    budget = generation_budget(run)
    assert budget.output_tokens == 8192
    assert budget.timeout(600) == 600
    assert budget.remaining_seconds is None and not budget.finishing
    assert budget.notice()["source_kind"] == "runtime_budget"


def test_short_deadline_preserves_working_phase_and_never_extends_deadline():
    started = now()
    run = Run(created_at=started, deadline_at=started + timedelta(seconds=10), config_snapshot={})
    working = generation_budget(run, observed_at=started)
    assert working.working_seconds == 8 and not working.finishing
    finishing = generation_budget(run, observed_at=started + timedelta(seconds=9))
    assert finishing.finishing and finishing.output_tokens == 2048
    assert finishing.timeout(600) == 1
    assert finishing.working_seconds is None
    with pytest.raises(AppError, match="截止时间"):
        generation_budget(run, observed_at=run.deadline_at)


def test_reserve_can_be_disabled_without_disabling_hard_deadline():
    started = now()
    run = Run(
        created_at=started,
        deadline_at=started + timedelta(seconds=10),
        config_snapshot={"runtime_options": {"finish_reserve_seconds": 0}},
    )
    budget = generation_budget(run, observed_at=started + timedelta(seconds=9))
    assert not budget.finishing and budget.timeout(600) == 1
    assert budget.working_seconds is None


def configure_connection(db, run, monkeypatch):
    connection = db.get_bind()
    monkeypatch.setattr(execution, "engine", lambda: connection)
    model = ModelConnection(
        id=new_id(),
        user_id=run.user_id,
        name="Generation-contract connection; never invoked",
        provider_type="openai_compatible",
        endpoint="http://127.0.0.1:30000/v1",
        model_identifier="Qwen3.8-Flash-Next",
        context_window=131072,
        generation=Generation(
            engine_kind="sglang", thinking_mode="enabled", reasoning_effort="medium"
        ).model_dump(),
    )
    db.add(model)
    db.flush()
    revision = models.freeze(db, run.user_id, model.id)
    run.model_connection_id = model.id
    run.config_snapshot = {
        "model_revision_id": revision.id,
        "max_output_tokens": None,
        "runtime_options": RunOptions().model_dump(),
        "tool_names": ["read_file"],
        "allowed_tools": ["read_file"],
    }
    db.commit()


def test_configuration_caps_output_and_preserves_native_medium(run_facts, monkeypatch):  # noqa: F811
    db, run, _, _ = run_facts
    configure_connection(db, run, monkeypatch)
    source = [{"role": "user", "content": Path("README.md").read_text()[:100]}]
    config, tools, messages, _ = execution.configuration(run.id, source)
    assert config.context_window == 131072 and config.max_output_tokens == 8192
    assert config.reasoning_effort == "medium"
    assert tools and messages[-1]["source_kind"] == "runtime_budget"
    prefix = messages[0]
    run.created_at = now() - timedelta(seconds=550)
    run.deadline_at = now() + timedelta(seconds=50)
    db.commit()
    finishing, tools, messages, _ = execution.configuration(run.id, source)
    assert finishing.generation_budget.finishing
    assert finishing.max_output_tokens == 2048 and tools == []
    assert messages[0] == prefix


def test_project_defaults_apply_between_user_and_explicit_workspace(run_facts):  # noqa: F811
    db, run, _, root = run_facts
    db.add(RuntimeDefaults(user_id=run.user_id, options={"command_timeout_seconds": 75}))
    db.commit()
    directory = root / ".rdesk"
    directory.mkdir()
    (directory / "config.json").write_text(
        '{"version":1,"defaults":{"command_timeout_seconds":80}}'
    )
    resolved = preferences.resolve(db, run.user_id, run.workspace_id)
    assert resolved.options.command_timeout_seconds == 80
    assert resolved.sources["command_timeout_seconds"] == "project:.rdesk/config.json"
    db.add(
        WorkspaceRuntimeDefaults(
            user_id=run.user_id,
            workspace_id=run.workspace_id,
            options={"command_timeout_seconds": 95},
        )
    )
    db.commit()
    assert (
        preferences.resolve(db, run.user_id, run.workspace_id).options.command_timeout_seconds == 95
    )
    task = preferences.resolve(
        db, run.user_id, run.workspace_id, task_options={"command_timeout_seconds": 100}
    )
    assert task.options.command_timeout_seconds == 100
    (directory / "config.json").write_text('{"version":1,"defaults":{"approval_mode":"manual"}}')
    assert preferences.resolve(db, run.user_id, run.workspace_id).options.approval_mode == "smart"


def length_attempt(db, run, *, used=8192, round_number=0, **metrics):
    """Protocol fixture using real persisted attempts; never calls a model."""
    aid, mid, _ = execution.begin_attempt(run.id, round_number, [])
    execution.append_text(run.id, mid, Path("README.md").read_text()[:300], reasoning=True)
    execution.finish_attempt(
        run.id,
        aid,
        mid,
        assistant=None,
        metrics={"output_tokens_total": used, **metrics},
        error=AppError("model_output_limit", "Recorded generation boundary"),
    )
    db.expire_all()
    return aid, mid


def test_reasoning_only_limit_gets_one_bounded_recovery_and_success_clears_phase(
    run_facts,  # noqa: F811
    monkeypatch,
):
    db, run, _, _ = run_facts
    configure_connection(db, run, monkeypatch)
    run.root_run_id = run.id
    run.config_snapshot = {
        **run.config_snapshot,
        "runtime_options": {**run.config_snapshot["runtime_options"], "model_retry_limit": 0},
    }
    db.commit()
    length_attempt(db, run)
    assert execution.prepare_continuation(run.id, 0)
    config, tools, messages, _ = execution.configuration(
        run.id, [{"role": "user", "content": run.input}]
    )
    assert config.max_output_tokens == 2048 and config.generation_budget.recovering
    assert tools and "有限恢复" in messages[-1]["content"]
    assert not any(item.get("source_kind") == "runtime_continuation" for item in messages)
    # The recovery is a genuine root-budgeted attempt even when transient retries are disabled.
    aid, mid, _ = execution.begin_attempt(run.id, 0, [])
    execution.finish_attempt(
        run.id,
        aid,
        mid,
        assistant={"role": "assistant", "content": run.input},
        metrics={"output_tokens_total": 100, "output_recovery": True},
    )
    db.expire_all()
    assert run.config_snapshot["safety"]["output_recovery_used"]
    assert "output_recovery" not in run.config_snapshot["safety"]
    assert generation_budget(run, 1).output_tokens == 8192
    length_attempt(db, run, round_number=1)
    assert not execution.prepare_continuation(run.id, 1)


@pytest.mark.parametrize(
    "boundary", ["disabled", "unknown_usage", "partial_tool", "root_limit", "cancelled"]
)
def test_recovery_preserves_existing_boundaries(run_facts, monkeypatch, boundary):  # noqa: F811
    db, run, _, _ = run_facts
    configure_connection(db, run, monkeypatch)
    run.root_run_id = run.id
    db.commit()
    aid, _ = length_attempt(db, run, partial_tool_calls=boundary == "partial_tool")
    if boundary == "unknown_usage":
        from sqlalchemy import select
        from workbench.modules.usage import UsageRecord

        db.scalar(select(UsageRecord).where(UsageRecord.attempt_id == aid)).metrics = {}
    elif boundary == "disabled":
        run.config_snapshot = {
            **run.config_snapshot,
            "runtime_options": {
                **run.config_snapshot["runtime_options"],
                "model_output_recovery_tokens": 0,
            },
        }
    elif boundary == "root_limit":
        run.config_snapshot = {
            **run.config_snapshot,
            "runtime_options": {
                **run.config_snapshot["runtime_options"],
                "root_model_attempt_limit": 1,
            },
        }
    elif boundary == "cancelled":
        run.cancel_requested_at = now()
    db.commit()
    if boundary == "root_limit":
        with pytest.raises(AppError) as error:
            execution.prepare_continuation(run.id, 0)
        assert error.value.code == "root_model_budget"
    else:
        assert not execution.prepare_continuation(run.id, 0)
    db.expire_all()
    assert not run.config_snapshot.get("safety", {}).get("output_recovery_used")


def test_recovery_shares_only_its_own_continuation_allowance(run_facts, monkeypatch):  # noqa: F811
    db, run, _, _ = run_facts
    configure_connection(db, run, monkeypatch)
    aid, mid = length_attempt(db, run, used=4096)
    execution.append_text(run.id, mid, run.input)
    assert execution.prepare_continuation(run.id, 0)
    length_attempt(db, run, used=4096)
    assert execution.prepare_continuation(run.id, 0)
    assert (
        execution.configuration(run.id, [{"role": "user", "content": run.input}])[
            0
        ].max_output_tokens
        == 2048
    )
    _, mid = length_attempt(db, run, used=512, output_recovery=True)
    execution.append_text(run.id, mid, run.input)
    assert execution.prepare_continuation(run.id, 0)
    config = execution.configuration(run.id, [{"role": "user", "content": run.input}])[0]
    assert config.max_output_tokens == 1536


def test_recovery_does_not_change_hard_deadline_or_finish_tools(run_facts, monkeypatch):  # noqa: F811
    db, run, _, _ = run_facts
    configure_connection(db, run, monkeypatch)
    length_attempt(db, run)
    run.created_at = now() - timedelta(seconds=550)
    run.deadline_at = now() + timedelta(seconds=40)
    db.commit()
    deadline = run.deadline_at
    assert execution.prepare_continuation(run.id, 0)
    config, tools, _, _ = execution.configuration(run.id, [{"role": "user", "content": run.input}])
    assert config.generation_budget.finishing and config.generation_budget.recovering
    assert not tools and config.max_output_tokens <= 2048
    db.expire_all()
    assert run.deadline_at == deadline
    run.deadline_at = now() - timedelta(seconds=1)
    db.commit()
    with pytest.raises(AppError) as error:
        execution.configuration(run.id, [{"role": "user", "content": run.input}])
    assert error.value.code == "deadline_exceeded"


def test_recovery_is_a_separate_explicit_allowance(run_facts, monkeypatch):  # noqa: F811
    db, run, _, _ = run_facts
    configure_connection(db, run, monkeypatch)
    run.config_snapshot = {
        **run.config_snapshot,
        "runtime_options": {**run.config_snapshot["runtime_options"], "model_output_tokens": 256},
    }
    db.commit()
    length_attempt(db, run, used=256)
    assert execution.prepare_continuation(run.id, 0)
    config = execution.configuration(run.id, [{"role": "user", "content": run.input}])[0]
    assert config.max_output_tokens == 2048 and config.generation_budget.recovering


def test_published_binding_does_not_acquire_a_new_budget_by_default():
    from types import SimpleNamespace

    from workbench.modules.access.service import defaults_for_binding

    frozen = RunOptions().model_dump(exclude={"model_output_recovery_tokens"})
    binding = SimpleNamespace(snapshot={"runtime_options": frozen, "model_connection_id": new_id()})
    assert defaults_for_binding(binding).options.model_output_recovery_tokens == 0
    assert RunOptions().model_output_recovery_tokens == 2048
