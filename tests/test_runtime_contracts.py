"""Auxiliary contract checks; these do not substitute for live model/UI acceptance."""

from starlette.routing import Match
from workbench.contracts.requests import RunOptions
from workbench.entrypoints.routes.conversations import router
from workbench.modules.runs.safety import observation_digest, waiting_observation
from workbench.modules.tools.registry import SpawnAgent, definitions


def test_long_task_defaults_and_child_inheritance():
    options = RunOptions()
    assert options.max_model_rounds == 65536
    assert options.checkpoint_rounds == 32
    assert options.root_model_attempt_limit == 524288
    assert options.child_agent_limit == 3
    assert options.approval_mode == "smart"
    assert SpawnAgent.model_fields["max_model_rounds"].default is None
    assert SpawnAgent.model_fields["max_output_tokens"].default is None
    assert SpawnAgent.model_fields["allowed_tools"].default is None
    schema = definitions(["spawn_agent"])[0]["function"]["parameters"]["properties"]
    assert "max_output_tokens" not in schema and "max_model_rounds" not in schema


def test_volatile_ids_do_not_count_as_new_observations():
    from pathlib import Path

    text = Path("README.md").read_text()
    first = {"id": "first", "content": text, "observed_at": "first"}
    second = {"id": "second", "content": text, "observed_at": "second"}
    assert observation_digest(first) == observation_digest(second)
    assert observation_digest(first) != observation_digest({"content": text[:100]})


def test_only_live_work_is_a_valid_wait():
    assert waiting_observation("file_task_status", {"status": "running"})
    assert not waiting_observation("file_task_status", {"status": "succeeded"})
    assert not waiting_observation("inspect_agents", {"agents": []})
    assert not waiting_observation("read_file", {"status": "running"})


def test_resume_route_is_not_shadowed_by_queue_item_uuid():
    scope = {
        "type": "http",
        "path": "/api/v1/threads/00000000-0000-0000-0000-000000000001/queue/resume",
        "method": "POST",
    }
    route = next(route for route in router.routes if route.matches(scope)[0] == Match.FULL)
    assert route.endpoint.__name__ == "queue_resume"
