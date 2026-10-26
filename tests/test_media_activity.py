"""Media activity links must refer to real children of the visible operation."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

from workbench.modules.media.domain import run_id_for_request
from workbench.modules.runs.activity import ActivityItem, activity_view, media_associations


def operation(name="generate_image", **values):
    return SimpleNamespace(id=str(uuid4()), run_id=str(uuid4()), tool_name=name, **values)


def test_media_association_requires_actual_child_and_matching_parent():
    accepted = operation(result=None)
    missing = operation(result={"run_id": str(uuid4())})
    unrelated = operation()
    ordinary = operation("read_file")
    accepted_id = run_id_for_request(UUID(accepted.id))
    unrelated_id = run_id_for_request(UUID(unrelated.id))
    db = MagicMock()
    db.execute.return_value = [
        (accepted_id, accepted.run_id),
        (unrelated_id, str(uuid4())),
    ]
    assert media_associations(db, "user", [accepted, missing, unrelated, ordinary]) == {
        accepted.id: accepted_id
    }
    assert db.execute.call_count == 1


def test_non_generation_activities_do_not_query_media():
    db = MagicMock()
    assert media_associations(db, "user", [operation("read_file")]) == {}
    db.execute.assert_not_called()


def test_activity_projects_only_explicit_media_link():
    row = operation(
        arguments={},
        result={"id": str(uuid4()), "run_id": str(uuid4())},
        status="succeeded",
        effect_status="applied",
        created_at="2026-10-20T00:00:00Z",
    )
    assert activity_view(row)["media_run_id"] is None
    identifier = str(uuid4())
    projected = ActivityItem(**activity_view(row, identifier))
    assert projected.media_run_id == identifier
