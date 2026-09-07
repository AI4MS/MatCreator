from __future__ import annotations

from pathlib import Path

from google.adk.skills import load_skill_from_dir

from matcreator.agents.execution_agent import remote_job_tools

SKILL_DIR = Path("src/matcreator/skills/remote-job")


def test_remote_job_skill_loads_and_documents_lifecycle() -> None:
    loaded = load_skill_from_dir(SKILL_DIR)

    assert loaded.name == "remote-job"
    assert "submit_bohr_sandbox" in loaded.instructions
    assert "submit_bohr_job" in loaded.instructions
    assert "collect_remote_job_outputs" in loaded.instructions
    assert "needs_replanning" in loaded.instructions
    assert "pause_remote_job" in loaded.instructions
    assert "NOT supported by either bohr provider" in loaded.instructions


def test_remote_job_skill_metadata_tools_exclude_e2b_and_are_callable() -> None:
    loaded = load_skill_from_dir(SKILL_DIR)
    metadata = loaded.frontmatter.metadata or {}
    tools = metadata.get("tools", [])

    assert tools, "expected metadata.tools to be populated"
    assert "submit_e2b_sandbox" not in tools
    for tool_name in tools:
        assert hasattr(remote_job_tools, tool_name), f"missing tool function: {tool_name}"


def test_remote_job_skill_reference_files_exist() -> None:
    references_dir = SKILL_DIR / "references"

    assert (references_dir / "bohr-sandbox-ref.md").exists()
    assert (references_dir / "bohr-job-ref.md").exists()
