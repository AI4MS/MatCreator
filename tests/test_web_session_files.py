from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path


def _load_web_main(monkeypatch, matcreator_home: Path):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("MATCREATOR_MODE", "local")
    monkeypatch.setenv("MATCREATOR_HOME", str(matcreator_home))
    for path in (root / "web", root / "src"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    for module_name in ("matcreator.config", "matcreator.constants", "matcreator.ports", "matcreator.workspace"):
        sys.modules.pop(module_name, None)

    spec = importlib.util.spec_from_file_location("web_main_session_files_test", root / "web" / "main.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_session_file_list_excludes_cancellation_and_trajectory_bookkeeping(monkeypatch, tmp_path):
    web_main = _load_web_main(monkeypatch, tmp_path / ".matcreator")
    workspace = tmp_path / ".matcreator" / "workspace"
    (workspace / "cancellation").mkdir(parents=True)
    (workspace / "trajectories").mkdir()
    (workspace / "results").mkdir()
    (workspace / "cancellation" / "cancelled-session.flag").write_text("user_requested")
    (workspace / "trajectories" / "cancelled-session.jsonl").write_text("{}\n")
    (workspace / "results" / "final.cif").write_text("data_final")

    response = asyncio.run(web_main.list_session_files("cancelled-session"))
    payload = json.loads(response.body)

    assert payload["files"] == [{
        "name": "final.cif",
        "path": str(workspace / "results" / "final.cif"),
        "relative_path": "results/final.cif",
        "size": len("data_final"),
    }]
