from __future__ import annotations

import ast
import importlib
from pathlib import Path

from google.adk.skills import load_skill_from_dir

from matcreator.tools import workspace_tools

SKILL_DIR = Path("src/matcreator/skills/paramiko")


def test_paramiko_skill_loads_and_documents_usage() -> None:
    loaded = load_skill_from_dir(SKILL_DIR)

    assert loaded.name == "paramiko"
    assert "ssh_client.py" in loaded.instructions
    assert "AutoAddPolicy" in loaded.instructions
    assert "recv_exit_status" in loaded.instructions
    assert "nohup" in loaded.instructions


def test_paramiko_skill_metadata_tools_are_callable() -> None:
    loaded = load_skill_from_dir(SKILL_DIR)
    metadata = loaded.frontmatter.metadata or {}
    tools = metadata.get("tools", [])

    assert tools, "expected metadata.tools to be populated"
    for tool_name in tools:
        assert hasattr(workspace_tools, tool_name), f"missing tool function: {tool_name}"


def test_paramiko_skill_reference_files_exist() -> None:
    references_dir = SKILL_DIR / "references"

    assert (references_dir / "_template.md").exists()
    assert (references_dir / "paratera.md").exists()


def test_paramiko_ssh_client_script_is_valid_python_and_importable() -> None:
    script_path = SKILL_DIR / "scripts" / "ssh_client.py"
    source = script_path.read_text()

    ast.parse(source)  # syntax check
    importlib.import_module("paramiko")  # confirms paramiko is importable in this environment
