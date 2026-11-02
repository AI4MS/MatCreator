"""Web/ADK isolation regressions using temporary homes and HTTPX transports."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
from pathlib import Path
import sys

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from test_web_session_access import _load_web_main, _load_web_main_server


@contextmanager
def _client(web):
    # Deliberately do not enter TestClient's lifespan context: these route tests
    # must not seed graphs, recover evaluations, or launch background monitors.
    host = web._local_access.browser_host if web._local_access is not None else "127.0.0.1"
    client = TestClient(web.app, base_url=f"http://{host}", raise_server_exceptions=True,
                        headers={"Origin": f"http://{host}"})
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def local_web(monkeypatch, tmp_path):
    """Never let an imported configuration or users_db reach real user state."""
    safe_os_home = tmp_path / "os-home"
    safe_os_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: safe_os_home))
    home = tmp_path / "instance"
    home.mkdir()
    monkeypatch.setenv("MATCREATOR_CONFIG_PATH", str(home / "config.yaml"))
    monkeypatch.setenv("MATCLAW_WORKSPACE", str(home / "workspace"))
    monkeypatch.delenv("MATCREATOR_USERS_DB", raising=False)
    monkeypatch.delitem(sys.modules, "users_db", raising=False)
    web = _load_web_main(monkeypatch, home)
    assert web._MATCREATOR_HOME.resolve() == home.resolve()
    return web


def _proof_response(web, request, *, instance_id=None, audience="adk", proof=None):
    assert request.url.path == "/api/local-auth/status"
    assert "authorization" not in request.headers
    assert "cookie" not in request.headers
    nonce = request.url.params["nonce"]
    pid = 34567
    return httpx.Response(200, json={
        "instance_id": instance_id or web._local_access.instance_id,
        "audience": audience,
        "pid": pid,
        "proof": proof or web._local_access.server_proof(nonce, audience, pid),
    })


def test_public_health_and_local_token_gate_protect_actual_web_routes(local_web):
    web = local_web
    with _client(web) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["instance_id"] == web._local_access.instance_id
        assert health.json()["browser_host"] == web._local_access.browser_host
        assert health.json()["authenticated"] is False
        assert "token" not in health.text
        assert "home" not in health.json()
        assert client.get("/api/users/user/sessions").status_code == 401
        assert client.post("/api/local-auth/login", json={"token": "wrong-instance-token"}).status_code == 401
        logged_in = client.post("/api/local-auth/login", json={"token": web._local_access.token})
        assert logged_in.status_code == 200
        assert "httponly" in logged_in.headers["set-cookie"].lower()
        assert client.get("/api/users/user/sessions").status_code == 200
        assert client.get("/api/health").json()["authenticated"] is True


def test_generic_loopback_health_is_public_but_cannot_issue_browser_cookie(local_web):
    web = local_web
    client = TestClient(web.app, base_url="http://127.0.0.1")
    try:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["browser_host"] == web._local_access.browser_host
        assert health.json()["authenticated"] is False
        login = client.post("/api/local-auth/login", json={"token": web._local_access.token})
        assert login.status_code in {401, 403, 409}
        assert "set-cookie" not in login.headers
    finally:
        client.close()


def test_users_database_and_summaries_follow_instance_home(local_web):
    web = local_web
    home = web._MATCREATOR_HOME
    assert web.users_db.USERS_DB_PATH == home / "users.db"
    assert web.SESSION_DB_PATH == home / ".adk" / "session.db"
    assert web.SUMMARIES_PATH == home / ".adk" / "session_summaries.json"
    web._save_summaries({"synthetic-session": {"summary": "private to this home"}})
    assert web.SUMMARIES_PATH.exists()
    assert web._get_session_summary("synthetic-session") == "private to this home"
    assert not (web.ROOT / "agents" / "MatCreator" / ".adk" / "session_summaries.json").resolve() == web.SUMMARIES_PATH.resolve()


def test_adk_proof_is_verified_without_sending_instance_secret(local_web):
    web = local_web
    seen = []

    def handler(request):
        seen.append(request)
        return _proof_response(web, request)

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            identity = await web._verify_local_adk(client, "http://127.0.0.1:28000")
            assert identity["instance_id"] == web._local_access.instance_id

    asyncio.run(check())
    assert len(seen) == 1


@pytest.mark.parametrize("alteration", ["instance", "proof", "audience"])
def test_wrong_adk_is_rejected_before_any_credentials_are_sent(local_web, alteration):
    web = local_web
    seen = []

    def handler(request):
        seen.append(request)
        return _proof_response(web, request,
            instance_id="foreign-instance" if alteration == "instance" else None,
            audience="web" if alteration == "audience" else "adk",
            proof="forged-proof" if alteration == "proof" else None)

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(HTTPException) as caught:
                await web._verify_local_adk(client, "http://127.0.0.1:28000")
            assert caught.value.status_code == 409

    asyncio.run(check())
    assert len(seen) == 1
    assert all("authorization" not in item.headers for item in seen)


def test_internal_proxy_attaches_own_token_only_after_identity_proof(local_web, monkeypatch):
    web = local_web
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/api/local-auth/status":
            return _proof_response(web, request)
        assert request.url.path == "/apps/MatCreator/users/user/sessions"
        assert request.headers["authorization"] == "Bearer " + web._local_access.token
        assert "cookie" not in request.headers
        assert "origin" not in request.headers
        assert request.url.params["limit"] == "2"
        return httpx.Response(200, json={"synthetic": "proxied"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(web.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs))
    request = Request({
        "type": "http", "method": "GET", "scheme": "http", "path": "/unused",
        "query_string": b"limit=2", "server": ("127.0.0.1", 28001),
        "headers": [(b"authorization", b"Bearer browser-supplied-token"),
                    (b"cookie", b"browser-cookie=never-forward"),
                    (b"origin", b"http://127.0.0.1:25173")],
    }, receive=lambda: None)
    request._body = b""
    response = asyncio.run(web._proxy_request(request, "http://127.0.0.1:28000",
                                             "/apps/MatCreator/users/user/sessions"))
    assert response.status_code == 200
    assert json.loads(response.body) == {"synthetic": "proxied"}
    assert [item.url.path for item in seen] == ["/api/local-auth/status", "/apps/MatCreator/users/user/sessions"]


def test_restart_refuses_wrong_instance_without_killing_or_starting_any_process(local_web, monkeypatch):
    web = local_web
    calls = []
    monkeypatch.setattr(web, "_is_port_open", lambda **kw: True)
    monkeypatch.setattr(web._run_registry, "active_runs", lambda: [])
    monkeypatch.setattr(web.os, "kill", lambda *a: calls.append(("kill", a)))
    monkeypatch.setattr(web.subprocess, "Popen", lambda *a, **kw: calls.append(("start", a)))
    real_client = httpx.AsyncClient
    def handler(request):
        return _proof_response(web, request, instance_id="somebody-elses-instance")
    monkeypatch.setattr(web.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(web.restart_backend())
    assert caught.value.status_code == 409
    assert calls == []


def test_server_mode_keeps_existing_non_local_auth_flow(monkeypatch, tmp_path):
    safe_os_home = tmp_path / "os-home"
    safe_os_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: safe_os_home))
    control = tmp_path / "control"
    control.mkdir()
    monkeypatch.setenv("MATCREATOR_CONFIG_PATH", str(control / "config.yaml"))
    monkeypatch.delitem(sys.modules, "users_db", raising=False)
    web = _load_web_main_server(monkeypatch, control, tmp_path / "data")
    assert web._local_access is None
    with _client(web) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "mode": "server"}
        assert client.get("/api/users/user/sessions").status_code == 200
