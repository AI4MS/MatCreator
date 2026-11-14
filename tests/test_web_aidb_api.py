"""AIDB reads through the production app and its SQLite session ownership."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from matcreator.aidb.service import SessionAIDB
from test_web_session_access import _load_web_main, _load_web_main_server


@pytest.mark.parametrize("mode,state_key", [
    ("local", "workdir"), ("local", "custom_workdir"), ("server", "workdir"),
])
def test_session_owner_can_read_results_with_shared_workspace_isolation(
    monkeypatch, tmp_path, mode, state_key,
):
    if mode == "server":
        web = _load_web_main_server(monkeypatch, tmp_path / "control", tmp_path / "data")
        user_home = tmp_path / "data" / "users" / "alice" / ".matcreator"
        workspace = user_home / "workspace" / "shared"
        session_db = user_home / ".adk" / "session.db"
        state_path = "/root/.matcreator/workspace/shared"
    else:
        web = _load_web_main(monkeypatch, tmp_path / "home")
        workspace = tmp_path / "shared-workspace"
        session_db = web.SESSION_DB_PATH
        state_path = str(workspace)
    workspace.mkdir(parents=True)
    config = workspace / "config.json"
    config.write_text("{}")
    session_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(session_db) as connection:
        connection.execute(
            "CREATE TABLE sessions (app_name TEXT, user_id TEXT, id TEXT, state TEXT)"
        )
        connection.executemany(
            "INSERT INTO sessions VALUES (?, ?, ?, ?)",
            [
                (web.APP_NAME, "alice", session, json.dumps({state_key: state_path}))
                for session in ("first", "second")
            ] + [("other-app", "alice", "unrelated", json.dumps({state_key: state_path}))],
        )
    service = SessionAIDB(
        workspace, "first", query_database=lambda c, w, q: (
            [{"record_id": "silicon", "formula_reduced": "Si"}],
            {"conditions": q, "match_count": 1, "complete": True},
        ),
    )
    service.bind(config)
    reference = service.publish(service.query({"formula": "Si"}))
    client = TestClient(web.app)
    path = "/api/users/alice/sessions/first/aidb/results/" + reference["result_id"]

    response = client.get(path)

    assert response.status_code == 200, response.text
    assert response.json()["records"][0]["formula_reduced"] == "Si"
    assert client.get(path.replace("/alice/", "/bob/")).status_code == 404
    assert client.get(path.replace("/first/", "/missing/")).status_code == 404
    assert client.get(path.replace("/first/", "/unrelated/")).status_code == 404

    second = SessionAIDB(workspace, "second")
    second.bind(config)
    response = client.get(path.replace("/first/", "/second/"))
    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "not_found"

    detail = "/api/users/alice/sessions/first/aidb/records/silicon"
    params = {"binding_id": reference["binding_id"]}
    assert client.get(detail.replace("/alice/", "/bob/"), params=params).status_code == 404
    response = client.get(detail.replace("/first/", "/second/"), params=params)
    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "stale_binding"

    service.bind(config)
    for endpoint, query in ((path, {}), (detail, params)):
        response = client.get(endpoint, params=query)
        assert response.status_code == 409
        assert response.json()["detail"]["status"] == "stale_binding"


def test_server_band_export_uses_the_completion_owners_store(monkeypatch, tmp_path):
    from matcreator.aidb import managed_vasp

    web = _load_web_main_server(monkeypatch, tmp_path / "control", tmp_path / "data")
    owner, session_id = "alice", "bands"
    store = web._remote_job_store_for_owner(owner)
    workspace = store.path.parent.parent / "workspace" / session_id
    workspace.mkdir(parents=True)
    with sqlite3.connect(store.path.parent / "session.db") as connection:
        connection.execute("CREATE TABLE sessions (app_name TEXT, user_id TEXT, id TEXT, state TEXT)")
        connection.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (
            web.APP_NAME, owner, session_id, json.dumps({"workdir": f"/root/.matcreator/workspace/{session_id}"}),
        ))

    job = store.create_job(owner_id=owner, session_id=session_id, provider="bohr_batchjob",
                           idempotency_key="bands", specification={"managed_vasp": {"schema": 1}})
    job_id = job["job_id"]
    for status in ("submitting", "succeeded", "collecting", "collected"):
        store.transition_job(job_id, status)
    path = managed_vasp._completion_path(store.path.parent, job_id)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"status": "verified", "compute_status": "succeeded", "record_id": "band",
                                "binding_id": "bound", "calculation_type": "nscf"}))

    def verify(record, user_id, *, store, job_id):
        assert record["record_id"] == "band"
        assert user_id == owner
        assert store.path == web._remote_job_store_for_owner(owner).path
        assert store.get_job(job_id)["session_id"] == session_id
        return {"bands.json": "verified"}

    def export(self, record_id, formats, *, binding_id, verify):
        assert self.workspace == workspace
        assert binding_id == "bound"
        return {"files": verify({"record_id": record_id})}

    monkeypatch.setattr(managed_vasp, "retained_band_artifacts", verify)
    monkeypatch.setattr(SessionAIDB, "export", export)
    client = TestClient(web.app)
    url = f"/api/users/{owner}/sessions/{session_id}/aidb/completions/{job_id}/records/band/export"
    response = client.post(url, json={"binding_id": "bound"})
    assert response.status_code == 200, response.text
    assert response.json()["files"] == {"bands.json": "verified"}
    assert client.post(url.replace("/alice/", "/bob/"), json={"binding_id": "bound"}).status_code == 404
