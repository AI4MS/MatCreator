from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

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


def test_set_session_output_dir_suffixes_existing_directories(tmp_path, monkeypatch):
    monkeypatch.setenv("MATCLAW_WORKSPACE", str(tmp_path))
    (tmp_path / "case").mkdir()
    (tmp_path / "case" / "old-artifact.txt").write_text("old", encoding="utf-8")
    (tmp_path / "case_01").mkdir()
    (tmp_path / "case_03").mkdir()
    tool_context = _FakeToolContext()

    result = workspace_tools.set_session_output_dir("case", tool_context)

    expected = str((tmp_path / "case_02").resolve())
    assert result == {
        "status": "ok",
        "output_dir": expected,
        "message": f"Session output directory set to {expected}",
    }
    assert tool_context.state["output_dir"] == expected
    assert tool_context.state["session_output_dir"] == expected
    assert (tmp_path / "case_02").is_dir()


def test_set_session_output_dir_suffixes_existing_file_and_symlink(tmp_path, monkeypatch):
    monkeypatch.setenv("MATCLAW_WORKSPACE", str(tmp_path))
    (tmp_path / "file_case").write_text("occupied", encoding="utf-8")
    (tmp_path / "link_target").mkdir()
    (tmp_path / "link_case").symlink_to(tmp_path / "link_target", target_is_directory=True)

    file_result = workspace_tools.set_session_output_dir("file_case", _FakeToolContext())
    link_result = workspace_tools.set_session_output_dir("link_case", _FakeToolContext())

    assert file_result["output_dir"] == str((tmp_path / "file_case_01").resolve())
    assert link_result["output_dir"] == str((tmp_path / "link_case_01").resolve())
    assert (tmp_path / "file_case_01").is_dir()
    assert (tmp_path / "link_case_01").is_dir()


def test_set_session_output_dir_allocates_fresh_directory_on_each_call(tmp_path, monkeypatch):
    monkeypatch.setenv("MATCLAW_WORKSPACE", str(tmp_path))
    tool_context = _FakeToolContext()

    first_result = workspace_tools.set_session_output_dir("repeat", tool_context)
    second_result = workspace_tools.set_session_output_dir("repeat", tool_context)

    assert first_result["output_dir"] == str((tmp_path / "repeat").resolve())
    assert second_result["output_dir"] == str((tmp_path / "repeat_01").resolve())
    assert tool_context.state["output_dir"] == second_result["output_dir"]
    assert tool_context.state["session_output_dir"] == second_result["output_dir"]


def test_set_session_output_dir_allocates_distinct_directories_concurrently(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MATCLAW_WORKSPACE", str(tmp_path))

    def allocate_output_dir(_: int) -> str:
        result = workspace_tools.set_session_output_dir("concurrent", _FakeToolContext())
        assert result["status"] == "ok"
        return result["output_dir"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        output_dirs = list(executor.map(allocate_output_dir, range(8)))

    assert len(set(output_dirs)) == 8
    assert all(Path(path).is_dir() for path in output_dirs)


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