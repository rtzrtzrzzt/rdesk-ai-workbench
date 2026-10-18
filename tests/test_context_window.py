"""Unit boundaries using README text and controlled protocol structures only.

These checks do not create business records, call a model, or establish live
model/product acceptance. Tool-shaped messages describe protocol grouping only.
"""

from collections import Counter
from pathlib import Path

import pytest
from workbench.modules.context.window import (
    durable,
    fingerprint,
    retain_tail,
    sources_valid,
    summary_message,
)


@pytest.fixture
def paragraphs():
    readme = Path(__file__).resolve().parents[1] / "README.md"
    return [part for part in readme.read_text(encoding="utf-8").split("\n\n") if part.strip()]


def test_summary_invalidates_when_only_retained_tail_source_changes(paragraphs):
    covered_message = {"role": "user", "content": paragraphs[1]}
    tail_message = {"role": "tool", "tool_call_id": "readme-tail", "content": paragraphs[2]}
    covered = Counter([fingerprint(covered_message)])
    dependencies = Counter([fingerprint(covered_message), fingerprint(tail_message)])
    original = Counter([fingerprint(covered_message), fingerprint(tail_message)])
    assert sources_valid(original, covered, dependencies)

    changed_tail = {**tail_message, "content": paragraphs[3]}
    changed = Counter([fingerprint(covered_message), fingerprint(changed_tail)])
    assert changed[fingerprint(covered_message)] == covered[fingerprint(covered_message)]
    assert not sources_valid(changed, covered, dependencies)


def test_summary_dependencies_count_repeated_messages_and_allow_later_occurrences(paragraphs):
    message = {"role": "user", "content": paragraphs[1]}
    key = fingerprint(message)
    covered = Counter({key: 1})
    dependencies = Counter({key: 2})
    assert not sources_valid(Counter({key: 1}), covered, dependencies)
    assert sources_valid(Counter({key: 2}), covered, dependencies)
    assert sources_valid(Counter({key: 3}), covered, dependencies)
    assert not sources_valid(Counter({key: 2}), Counter(), dependencies)
    assert not sources_valid(Counter({key: 2}), covered, Counter())


def test_fingerprint_tracks_source_content_not_incidental_message_metadata(paragraphs):
    message = {
        "role": "user",
        "content": paragraphs[1],
        "attachments": [{"artifact_id": "readme-source", "version": "before"}],
    }
    reordered = {"attachments": message["attachments"], "content": paragraphs[1], "role": "user"}
    assert fingerprint(reordered) == fingerprint(message)
    assert fingerprint({**message, "_message_id": "ledger-id"}) == fingerprint(message)
    assert fingerprint({**message, "content": paragraphs[2]}) != fingerprint(message)
    assert fingerprint(
        {**message, "attachments": [{"artifact_id": "readme-source", "version": "after"}]}
    ) != fingerprint(message)
    assert fingerprint({**message, "source_kind": "user_steering"}) != fingerprint(message)


def test_compiled_representation_keeps_the_original_source_fingerprint(paragraphs):
    original = {"role": "user", "content": paragraphs[1]}
    source = fingerprint(original)
    compiled = {
        "role": "user",
        "content": [{"type": "text", "text": paragraphs[1]}],
        "_context_source_fingerprint": source,
    }
    assert fingerprint(compiled) == source


def test_latest_real_steering_and_pending_input_survive_runtime_continuation(paragraphs):
    system = {"role": "system", "content": paragraphs[1]}
    original = {"role": "user", "content": paragraphs[2]}
    pending = {
        "role": "user",
        "source_kind": "agent_message",
        "_input_message_id": "pending-readme",
        "_input_pending": True,
        "content": paragraphs[3],
    }
    steering = {
        "role": "user",
        "source_kind": "user_steering",
        "_input_message_id": "steering-readme",
        "content": paragraphs[4],
    }
    continuation = {
        "role": "user",
        "source_kind": "runtime_continuation",
        "content": paragraphs[5],
    }
    prior_summary = summary_message(paragraphs[2], version=1)
    tail, covered = retain_tail(
        [system, original, pending, steering, prior_summary, continuation], [], input_budget=1
    )
    assert tail == [pending, steering]
    assert covered == Counter([fingerprint(original)])


def test_tool_calls_and_all_results_are_retained_or_removed_as_whole_groups(paragraphs):
    def call(identifier):
        return {
            "id": identifier,
            "type": "function",
            "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
        }

    system = {"role": "system", "content": paragraphs[1]}
    older_call = {"role": "assistant", "content": "", "tool_calls": [call("older-readme")]}
    older_result = {"role": "tool", "tool_call_id": "older-readme", "content": paragraphs[2]}
    latest_user = {"role": "user", "content": paragraphs[3]}
    latest_call = {
        "role": "assistant",
        "content": "",
        "tool_calls": [call("latest-readme-1"), call("latest-readme-2")],
    }
    results = [
        {"role": "tool", "tool_call_id": "latest-readme-1", "content": paragraphs[4]},
        {"role": "tool", "tool_call_id": "latest-readme-2", "content": paragraphs[5]},
    ]
    tail, covered = retain_tail(
        [system, older_call, older_result, latest_user, latest_call, *results], [], input_budget=1
    )
    assert tail == [latest_user, latest_call, *results]
    assert covered == Counter([fingerprint(older_call), fingerprint(older_result)])
    retained_calls = {item["id"] for item in latest_call["tool_calls"]}
    retained_results = {item["tool_call_id"] for item in tail if item["role"] == "tool"}
    assert retained_results == retained_calls


def test_removed_identical_messages_preserve_counter_multiplicity(paragraphs):
    older = {"role": "assistant", "content": paragraphs[1]}
    latest_user = {"role": "user", "content": paragraphs[2]}
    tail, covered = retain_tail(
        [{"role": "system", "content": paragraphs[3]}, older, dict(older), latest_user],
        [],
        input_budget=1,
    )
    assert tail == [latest_user]
    assert covered == Counter({fingerprint(older): 2})


def test_summary_is_versioned_historical_material_not_a_new_user_goal(paragraphs):
    message = summary_message(paragraphs[1], version=7)
    assert message["role"] == "user"
    assert message["source_kind"] == "task_state"
    assert message["_context_summary_version"] == 7
    assert message["content"].endswith(paragraphs[1])
    assert "不增加权限" in message["content"]
    assert not durable(message)
