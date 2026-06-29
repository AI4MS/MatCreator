from pathlib import Path

from matcreator.agents.session_log import (
    SESSION_ARTIFACT_PREFIX,
    SESSION_ARTIFACTS_KEY,
    SESSION_LOG_EVENT_PREFIX,
    SESSION_LOG_KEY,
    append_session_log_entry,
    collect_artifact_paths,
    is_session_log_state_key,
    normalize_artifact_paths,
)
from matcreator.agents.thinking_agent.history_tools import read_session_log


class _FakeSession:
    id = "test-session"


class _FakeInvocationContext:
    session = _FakeSession()


class _FakeToolContext:
    def __init__(self):
        self.state = {"session_id": "test-session"}
        self._invocation_context = _FakeInvocationContext()


def test_collect_artifact_paths_from_nested_payload(tmp_path):
    plot = tmp_path / "plot.png"
    model = tmp_path / "model.pt"
    structure = tmp_path / "structure.cif"

    payload = {
        "plot_path": str(plot),
        "nested": {
            "artifacts": [str(model)],
            "responses": [{"structure_path": str(structure)}],
        },
    }

    assert collect_artifact_paths(payload) == [
        str(plot.resolve()),
        str(model.resolve()),
        str(structure.resolve()),
    ]


def test_normalize_artifact_paths_deduplicates_and_absolutizes(tmp_path, monkeypatch):
    artifact = Path("result.txt")
    monkeypatch.chdir(tmp_path)

    assert normalize_artifact_paths([artifact, str(artifact), ""]) == [
        str((tmp_path / artifact).resolve())
    ]


def test_append_session_log_entry_updates_log_and_artifact_index(tmp_path):
    tool_context = _FakeToolContext()
    artifact = tmp_path / "result.txt"

    record = append_session_log_entry(
        tool_context,
        {"kind": "step_complete", "result": {"artifacts": [str(artifact)]}},
    )

    assert record["kind"] == "step_complete"
    assert record["event_id"]
    assert record["artifacts"] == [str(artifact.resolve())]
    assert tool_context.state[SESSION_LOG_KEY][0] == record
    assert tool_context.state[f"{SESSION_LOG_EVENT_PREFIX}{record['event_id']}"] == record
    assert tool_context.state[SESSION_ARTIFACTS_KEY] == [str(artifact.resolve())]
    assert any(key.startswith(SESSION_ARTIFACT_PREFIX) for key in tool_context.state)


def test_read_session_log_overview_merges_keyed_events_and_artifacts(tmp_path):
    tool_context = _FakeToolContext()
    first_artifact = tmp_path / "first.txt"
    second_artifact = tmp_path / "second.txt"
    append_session_log_entry(
        tool_context,
        {
            "kind": "step_start",
            "step_id": "execution_0__node_step_a",
            "node_id": "step_a",
            "parent_id": "execution_0",
            "step_number": 1,
            "action": "Run A",
            "events": {"conversation": [{"content": "hidden"}]},
        },
    )
    append_session_log_entry(
        tool_context,
        {
            "kind": "step_complete",
            "step_id": "execution_0__node_step_a",
            "node_id": "step_a",
            "parent_id": "execution_0",
            "status": "success",
            "result": {"artifacts": [str(first_artifact)], "concise_summary": "Done."},
        },
        artifacts=[str(second_artifact)],
    )

    # Simulate an ADK parallel merge that preserved keyed records but lost the
    # convenience aggregate list.
    tool_context.state[SESSION_LOG_KEY] = []
    tool_context.state[SESSION_ARTIFACTS_KEY] = []

    log = read_session_log(tool_context)

    assert log["event_count"] == 2
    assert log["view"] == "overview"
    assert log["artifact_count"] == 2
    assert log["graph"]["edges"] == [{"from": "execution_0", "to": "execution_0__node_step_a"}]
    assert log["graph"]["nodes"][0]["id"] == "execution_0__node_step_a"
    assert log["graph"]["nodes"][0]["status"] == "success"
    assert log["graph"]["nodes"][0]["artifact_count"] == 2


def test_read_session_log_detail_requires_selector(tmp_path):
    tool_context = _FakeToolContext()
    log = read_session_log(tool_context, view="detail")

    assert log["status"] == "error"
    assert "requires exactly one selector" in log["message"]


def test_read_session_log_detail_returns_one_executor_without_conversation(tmp_path):
    tool_context = _FakeToolContext()
    artifact = tmp_path / "result.txt"
    append_session_log_entry(
        tool_context,
        {
            "kind": "step_complete",
            "step_id": "execution_0__node_step_a",
            "node_id": "step_a",
            "parent_id": "execution_0",
            "status": "success",
            "events": {
                "conversation": [{"content": "hidden"}],
                "tool_calls": [{"name": "run_python"}],
            },
        },
        artifacts=[str(artifact)],
    )

    log = read_session_log(tool_context, view="detail", node_id="step_a")

    assert log["status"] == "ok"
    assert log["view"] == "detail"
    assert log["event_count"] == 1
    assert log["artifacts"] == [str(artifact.resolve())]
    assert "conversation" not in log["events"][0]["events"]
    assert log["events"][0]["events"]["tool_calls"] == [{"name": "run_python"}]


def test_read_session_log_artifacts_view_lists_all_paths(tmp_path):
    tool_context = _FakeToolContext()
    artifact = tmp_path / "result.txt"
    append_session_log_entry(tool_context, {"kind": "step_complete"}, artifacts=[str(artifact)])

    log = read_session_log(tool_context, view="artifacts")

    assert log["status"] == "ok"
    assert log["view"] == "artifacts"
    assert log["artifacts"] == [str(artifact.resolve())]


def test_session_log_state_keys_are_identified_for_context_filtering():
    assert is_session_log_state_key(SESSION_LOG_KEY)
    assert is_session_log_state_key(SESSION_ARTIFACTS_KEY)
    assert is_session_log_state_key(f"{SESSION_LOG_EVENT_PREFIX}abc")
    assert is_session_log_state_key(f"{SESSION_ARTIFACT_PREFIX}abc")
    assert not is_session_log_state_key("execution_graph")