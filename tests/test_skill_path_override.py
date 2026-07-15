from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_module_skills_root_env_overrides_builtin_root(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    selected_skills = tmp_path / "selected-skills"
    workspace = tmp_path / "workspace"
    selected_skills.mkdir()
    (workspace / "skills").mkdir(parents=True)

    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath = f"{project_root / 'src'}{os.pathsep}{project_root}"
    if existing_pythonpath:
        pythonpath = f"{pythonpath}{os.pathsep}{existing_pythonpath}"

    env = {
        **os.environ,
        "PYTHONPATH": pythonpath,
        "MATCLAW_WORKSPACE": str(workspace),
        "MATCREATOR_MODULE_SKILLS_ROOT": str(selected_skills),
    }

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from matcreator.skill import _MODULE_SKILLS_ROOT; "
            "import os; "
            "assert str(_MODULE_SKILLS_ROOT) == os.environ['MATCREATOR_MODULE_SKILLS_ROOT'], _MODULE_SKILLS_ROOT",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr


def test_module_skills_root_config_override_applies_before_skill_import(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    matcreator_home = tmp_path / ".matcreator"
    selected_skills = tmp_path / "selected-skills"
    workspace = tmp_path / "workspace"
    matcreator_home.mkdir()
    selected_skills.mkdir()
    (workspace / "skills").mkdir(parents=True)
    (matcreator_home / "config.yaml").write_text(
        "skills:\n"
        f"  module_root: {selected_skills}\n",
        encoding="utf-8",
    )

    existing_pythonpath = os.environ.get("PYTHONPATH", "")
    pythonpath = f"{project_root / 'src'}{os.pathsep}{project_root}"
    if existing_pythonpath:
        pythonpath = f"{pythonpath}{os.pathsep}{existing_pythonpath}"

    env = {
        **os.environ,
        "PYTHONPATH": pythonpath,
        "MATCREATOR_HOME": str(matcreator_home),
        "MATCLAW_WORKSPACE": str(workspace),
    }
    env.pop("MATCREATOR_MODULE_SKILLS_ROOT", None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from matcreator.config import apply_config_env_overrides; "
            "apply_config_env_overrides(); "
            "from matcreator.skill import _MODULE_SKILLS_ROOT; "
            "import os; "
            "assert str(_MODULE_SKILLS_ROOT) == os.environ['MATCREATOR_MODULE_SKILLS_ROOT'], _MODULE_SKILLS_ROOT",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert result.returncode == 0, result.stderr
