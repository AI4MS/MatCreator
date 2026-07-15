from __future__ import annotations

from matcreator.tools import workspace_tools


class _FakeToolContext:
    def __init__(self):
        self.state = {}


def test_set_session_output_dir_sets_output_state_under_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MATCLAW_WORKSPACE", str(tmp_path))
    tool_context = _FakeToolContext()

    result = workspace_tools.set_session_output_dir("case_001", tool_context)

    expected = str((tmp_path / "case_001").resolve())
    assert result["status"] == "ok"
    assert result["output_dir"] == expected
    assert tool_context.state["output_dir"] == expected
    assert tool_context.state["session_output_dir"] == expected
    assert "workdir" not in tool_context.state
    assert "workspace_dir" not in tool_context.state
    assert (tmp_path / "case_001").is_dir()


def test_set_session_workdir_rejects_paths_outside_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MATCLAW_WORKSPACE", str(tmp_path))
    tool_context = _FakeToolContext()

    absolute_result = workspace_tools.set_session_output_dir(str(tmp_path.parent), tool_context)
    traversal_result = workspace_tools.set_session_output_dir("../outside", tool_context)
    root_result = workspace_tools.set_session_output_dir(".", tool_context)

    assert absolute_result["status"] == "error"
    assert traversal_result["status"] == "error"
    assert root_result["status"] == "error"
    assert tool_context.state == {}