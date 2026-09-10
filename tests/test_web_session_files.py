from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path


def _load_web_main(monkeypatch, matcreator_home: Path):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("MATCREATOR_MODE", "local")
    monkeypatch.setenv("MATCREATOR_HOME", str(matcreator_home))
    # The web server also resolves ~/.matcreator directly in local mode.
    expanduser = Path.expanduser
    monkeypatch.setattr(Path, "expanduser", lambda path: matcreator_home if str(path) == "~/.matcreator" else expanduser(path))
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


def test_session_file_list_skips_non_utf8_filename(monkeypatch, tmp_path):
    web_main = _load_web_main(monkeypatch, tmp_path / ".matcreator")
    workspace = tmp_path / ".matcreator" / "workspace"
    workspace.mkdir(parents=True)
    valid_file = workspace / "valid.extxyz"
    valid_file.write_text("valid")

    invalid_path = os.fsencode(workspace) + b"/\x80-invalid-name"
    descriptor = os.open(invalid_path, os.O_WRONLY | os.O_CREAT, 0o600)
    os.close(descriptor)

    response = asyncio.run(web_main.list_session_files("session-with-invalid-name"))
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["skipped_files"] == 1
    assert [item["relative_path"] for item in payload["files"]] == [valid_file.name]


def test_folder_upload_preserves_paths_and_existing_files(monkeypatch, tmp_path):
    import httpx

    web_main = _load_web_main(monkeypatch, tmp_path / '.matcreator')
    workspace = tmp_path / 'workspace'
    monkeypatch.setattr(web_main, '_load_session_state', lambda _: (None, {}))
    monkeypatch.setattr(web_main, '_get_workdir_for_session', lambda _: workspace)
    def request(method, url, **kwargs):
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web_main.app), base_url="http://test") as client:
                return await client.request(method, url, **kwargs)
        return asyncio.run(send())
    url = '/api/sessions/test-folder/files'
    relative = 'retest/151_Li3SCl/original.extxyz'
    for content in (b'first structure', b'second structure'):
        response = request("POST", url, files={'file': ('original.extxyz', content)}, data={'relative_path': relative})
        assert response.status_code == 200, response.text
    assert response.json()['relative_path'] == 'uploads/retest/151_Li3SCl/original-1.extxyz'
    assert (workspace / 'uploads' / relative).read_bytes() == b'first structure'
    assert (workspace / response.json()['relative_path']).read_bytes() == b'second structure'
    response = request("POST", url, files={'file': ('plain.txt', b'plain')})
    assert response.status_code == 200
    assert response.json()['relative_path'] == 'uploads/plain.txt'
    listing = request("GET", url)
    assert listing.status_code == 200
    assert 'uploads/' + relative in {item['relative_path'] for item in listing.json()['files']}


def test_folder_upload_rejects_escaping_paths(monkeypatch, tmp_path):
    import httpx

    web_main = _load_web_main(monkeypatch, tmp_path / '.matcreator')
    workspace = tmp_path / 'workspace'
    monkeypatch.setattr(web_main, '_load_session_state', lambda _: (None, {}))
    monkeypatch.setattr(web_main, '_get_workdir_for_session', lambda _: workspace)
    uploads = workspace / 'uploads'
    uploads.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (uploads / 'link').symlink_to(outside, target_is_directory=True)
    def request(method, url, **kwargs):
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web_main.app), base_url="http://test") as client:
                return await client.request(method, url, **kwargs)
        return asyncio.run(send())
    for path in ('../escape.txt', '/absolute.txt', 'folder/../../escape.txt', 'C:\\escape.txt', 'link/escape.txt'):
        response = request('POST', '/api/sessions/test-folder/files', files={'file': ('escape.txt', b'bad')}, data={'relative_path': path})
        assert response.status_code == 400, (path, response.text)
    assert not list(outside.iterdir())
