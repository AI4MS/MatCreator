"""Independent adversarial checks for the local instance HTTP/WS boundary."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import shutil
import sqlite3

from fastapi import FastAPI, WebSocket
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from matcreator.local_access import InstanceLease, ensure_local_access, install_local_access


def _instance(monkeypatch, home, *, audience="web", port=18101):
    monkeypatch.setenv("MATCREATOR_MODE", "local")
    monkeypatch.delenv("MATCREATOR_LOCAL_ALLOWED_HOSTS", raising=False)
    app = FastAPI()

    @app.get("/")
    def index():
        return {"public": "bundled UI"}

    @app.get("/assets/public.js")
    def asset():
        return "bundled javascript"

    @app.get("/api/private")
    @app.get("/api/private.js")
    @app.get("/apps/MatCreator/users/user/sessions")
    @app.get("/list-apps")
    def private():
        return {"private": "synthetic instance data"}

    @app.post("/api/change")
    def change():
        return {"changed": True}

    @app.get("/run_sse")
    def stream():
        return StreamingResponse(iter(["data: synthetic\n\n"]), media_type="text/event-stream")

    @app.websocket("/api/workspace/terminal")
    async def terminal(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("synthetic terminal")
        await websocket.close()

    access = install_local_access(
        app, home=home, audience=audience, public_paths={"/", "/assets/public.js"},
    )
    client = TestClient(app, base_url=f"http://{access.browser_host}:{port}")
    return access, client


def _login(client, access):
    response = client.post("/api/local-auth/login", json={"token": access.token})
    assert response.status_code == 200
    return response


@pytest.mark.parametrize("audience", ["web", "adk"])
def test_protected_http_routes_reject_missing_query_and_invalid_bearer(monkeypatch, tmp_path, audience):
    access, client = _instance(monkeypatch, tmp_path / audience, audience=audience)
    for route in ("/api/private", "/api/private.js", "/api/xrd/workspaces", "/docs",
                  "/openapi.json", "/list-apps", "/run_sse",
                  "/apps/MatCreator/users/user/sessions"):
        assert client.get(route).status_code == 401
        assert client.get(route, params={"token": access.token, "user_id": "admin"}).status_code == 401
        assert client.get(route, headers={"Authorization": "Bearer incorrect"}).status_code == 401
    if audience == "web":
        assert client.get("/").status_code == 200
        assert client.get("/assets/public.js").status_code == 200
    else:
        assert client.get("/").status_code == 401


def test_health_metadata_does_not_disclose_any_access_secret(monkeypatch, tmp_path):
    access, client = _instance(monkeypatch, tmp_path)
    for route in ("/api/health", "/api/local-auth/status"):
        response = client.get(route)
        assert response.status_code == 200
        payload = response.json()
        assert payload["authenticated"] is False
        assert payload["instance_id"] == access.instance_id
        leaked = access.token in response.text or str(access.token_path) in response.text
        assert not leaked
        assert response.headers["cache-control"] == "no-store"


def test_two_homes_reject_each_others_tokens_and_browser_cookies(monkeypatch, tmp_path):
    first, a = _instance(monkeypatch, tmp_path / "a", port=18101)
    second, b = _instance(monkeypatch, tmp_path / "b", port=18102)
    assert first.instance_id != second.instance_id
    assert first.cookie_name != second.cookie_name
    assert b.post("/api/local-auth/login", json={"token": first.token}).status_code == 401
    assert b.get("/api/private", headers=first.headers()).status_code == 401
    _login(a, first)
    assert a.get("/api/private").status_code == 200
    assert b.get("/api/private", headers={"Cookie": f"{first.cookie_name}={a.cookies.get(first.cookie_name)}"}).status_code == 401
    # Even renaming another instance's cookie cannot make its session valid.
    assert b.get("/api/private", headers={"Cookie": f"{second.cookie_name}={a.cookies.get(first.cookie_name)}"}).status_code == 401
    assert b.get("/api/private", headers=second.headers()).status_code == 200


def test_browser_cookie_does_not_override_explicit_bad_authorization(monkeypatch, tmp_path):
    access, client = _instance(monkeypatch, tmp_path)
    _login(client, access)
    assert client.get("/api/private", headers={"Authorization": "Bearer wrong-instance"}).status_code == 401
    assert client.get("/api/private", headers={"Authorization": "Basic ignored"}).status_code == 401


def test_browser_cookie_cannot_be_used_for_cross_origin_changes_or_rebinding(monkeypatch, tmp_path):
    access, client = _instance(monkeypatch, tmp_path)
    _login(client, access)
    assert client.post("/api/change", headers={"Origin": "https://attacker.invalid"}).status_code == 403
    assert client.post("/api/change", headers={"Origin": "null"}).status_code == 403
    assert client.post("/api/change").status_code == 403
    assert client.post("/api/change", headers={"Origin": str(client.base_url).rstrip("/")}).status_code == 200
    assert client.get("/api/private", headers={"Host": "attacker.invalid", **access.headers()}).status_code == 403
    # Non-browser internal clients authenticate explicitly with Bearer.
    assert client.post("/api/change", headers=access.headers()).status_code == 200


def test_websocket_requires_authentication_and_safe_origin_before_accept(monkeypatch, tmp_path):
    access, client = _instance(monkeypatch, tmp_path)
    url = str(client.base_url).replace("http:", "ws:").rstrip("/") + "/api/workspace/terminal"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(url):
            pytest.fail("Unauthenticated terminal was accepted")
    _login(client, access)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(url, headers={"Origin": "https://attacker.invalid"}):
            pytest.fail("Cross-origin terminal was accepted")
    with client.websocket_connect(url, headers={"Origin": str(client.base_url).rstrip("/")}) as websocket:
        assert websocket.receive_text() == "synthetic terminal"


def test_expired_and_logged_out_session_replays_are_rejected(monkeypatch, tmp_path):
    access, client = _instance(monkeypatch, tmp_path)
    response = _login(client, access)
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=strict" in response.headers["set-cookie"].lower()
    cookie = f"{access.cookie_name}={client.cookies.get(access.cookie_name)}"
    assert client.post("/api/local-auth/logout", headers={"Origin": str(client.base_url).rstrip("/")}).status_code == 200
    assert client.get("/api/private", headers={"Cookie": cookie}).status_code == 401
    _login(client, access)
    with sqlite3.connect(access.session_path) as database:
        database.execute("UPDATE sessions SET expires = 0")
    assert client.get("/api/private").status_code == 401


def test_malformed_tokens_fail_closed_without_server_error(monkeypatch, tmp_path):
    _, client = _instance(monkeypatch, tmp_path)
    for value in (None, [], {}, 123, "", "wrong", "\ud800"):
        response = client.post("/api/local-auth/login", content=json.dumps({"token": value}),
                               headers={"Content-Type": "application/json"})
        assert response.status_code == 401
    assert client.post("/api/local-auth/login", content="not json",
                       headers={"Content-Type": "application/json"}).status_code == 401
    assert client.post("/api/local-auth/login", content="x" * 9000,
                       headers={"Content-Type": "application/json"}).status_code == 413


def test_concurrent_web_and_adk_initialization_share_exactly_one_identity(tmp_path):
    with ThreadPoolExecutor(max_workers=6) as executor:
        identities = list(executor.map(lambda _: ensure_local_access(tmp_path), range(12)))
    assert len({item.instance_id for item in identities}) == 1
    same_token = all(item.token == identities[0].token for item in identities)
    assert same_token
    assert len({item.cookie_name for item in identities}) == 1


def test_status_challenge_is_bound_to_nonce_instance_and_audience(monkeypatch, tmp_path):
    access, client = _instance(monkeypatch, tmp_path, audience="adk")
    nonce = "a" * 32
    payload = client.get("/api/local-auth/status", params={"nonce": nonce}).json()
    assert payload["audience"] == "adk"
    correct = payload["proof"] == access.server_proof(nonce, "adk", payload["pid"])
    assert correct
    replay = client.get("/api/local-auth/status", params={"nonce": "b" * 32}).json()
    assert payload["proof"] != replay["proof"]
    same_as_web = payload["proof"] == access.server_proof(nonce, "web", payload["pid"])
    assert not same_as_web


def test_preexisting_session_database_symlink_is_rejected_before_open(tmp_path):
    home = tmp_path / "instance"
    access = ensure_local_access(home)
    access.session_path.unlink()
    target = tmp_path / "unrelated-file"
    target.write_bytes(b"must remain untouched")
    try:
        access.session_path.symlink_to(target)
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this host")
    with pytest.raises(PermissionError):
        ensure_local_access(home)
    assert target.read_bytes() == b"must remain untouched"


@pytest.mark.parametrize("alias", ["localhost", "127.0.0.1"])
def test_shared_loopback_host_never_issues_or_accepts_session_cookie(monkeypatch, tmp_path, alias):
    access, client = _instance(monkeypatch, tmp_path)
    url = f"http://{alias}:18101"
    blocked = client.post(url + "/api/local-auth/login", json={"token": access.token})
    assert blocked.status_code == 409
    assert blocked.json()["browser_host"] == access.browser_host
    assert "set-cookie" not in blocked.headers
    health = client.get(url + "/api/health").json()
    assert health["browser_host"] == access.browser_host
    _login(client, access)
    stolen = f"{access.cookie_name}={client.cookies.get(access.cookie_name)}"
    assert client.get(url + "/api/private", headers={"Cookie": stolen}).status_code == 401
    assert client.get(url + "/api/private", headers=access.headers()).status_code == 200
    redirection = client.get(url + "/local-access", follow_redirects=False)
    assert redirection.status_code == 303
    assert redirection.headers["location"] == f"http://{access.browser_host}:18101/local-access"


def test_normal_cross_instance_urls_do_not_receive_each_others_host_only_cookie(monkeypatch, tmp_path):
    first, a = _instance(monkeypatch, tmp_path / "a", port=18101)
    second, b = _instance(monkeypatch, tmp_path / "b", port=18102)
    _login(a, first)
    assert first.browser_host != second.browser_host
    b.cookies.update(a.cookies)
    assert b.get("/api/private").status_code == 401
    assert a.get("/api/private").status_code == 200
    assert a.post("/api/change", headers={"Origin": f"http://{second.browser_host}:18101"}).status_code == 403


def test_copying_private_identity_to_a_new_home_is_rejected_without_rotating(tmp_path):
    original = tmp_path / "original"
    clone = tmp_path / "clone"
    access = ensure_local_access(original)
    before = access.token_path.read_bytes()
    shutil.copytree(original, clone)
    with pytest.raises(ValueError, match="not bound to this data directory"):
        ensure_local_access(clone)
    unchanged = (clone / ".local-access" / "identity.json").read_bytes() == before
    assert unchanged
    same = ensure_local_access(original)
    assert same.instance_id == access.instance_id


def test_one_live_web_owner_per_home_and_release_allows_recovery(tmp_path):
    first = InstanceLease(tmp_path / "a", "web").acquire()
    adk = InstanceLease(tmp_path / "a", "adk").acquire()
    other_home = InstanceLease(tmp_path / "b", "web").acquire()
    replacement = None
    try:
        with pytest.raises(RuntimeError, match="active service of the same role"):
            InstanceLease(tmp_path / "a", "web").acquire()
        first.close()
        replacement = InstanceLease(tmp_path / "a", "web").acquire()
    finally:
        first.close()
        adk.close()
        other_home.close()
        if replacement is not None:
            replacement.close()
