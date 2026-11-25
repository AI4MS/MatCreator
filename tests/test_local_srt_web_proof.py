"""The SRT ingress exposes a signed ADK proof, never ADK credentials or data."""
import asyncio
from pathlib import Path
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

from test_web_session_access import _load_web_main


@pytest.fixture
def srt_web(monkeypatch, tmp_path):
    safe_home = tmp_path / "os-home"
    safe_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: safe_home))
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setenv("MATCREATOR_CONFIG_PATH", str(home / "config.yaml"))
    monkeypatch.setenv("MATCLAW_WORKSPACE", str(home / "workspace"))
    monkeypatch.setenv("MATCREATOR_LOCAL_SRT_WORKER", "1")
    monkeypatch.delenv("MATCREATOR_USERS_DB", raising=False)
    monkeypatch.delitem(sys.modules, "users_db", raising=False)
    return _load_web_main(monkeypatch, home)


def _mock_adk(monkeypatch, web, alteration=None):
    original = httpx.AsyncClient
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.path == "/api/local-auth/status"
        assert request.url.host == "127.0.0.1"
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        nonce = request.url.params["nonce"]
        identity = {"instance_id": web._local_access.instance_id, "audience": "adk", "pid": 34567,
                    "proof": web._local_access.server_proof(nonce, "adk", 34567),
                    "token": "must-not-be-relayed", "home": "/not-public"}
        if alteration:
            identity.update(alteration)
        return httpx.Response(200, json=identity)

    monkeypatch.setattr(web.httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs))
    return seen


def test_srt_public_adk_proof_is_bound_to_callers_nonce_and_has_no_credentials(srt_web, monkeypatch):
    web = srt_web
    seen = _mock_adk(monkeypatch, web)
    # Deliberately avoid lifespan: these tests must not recover real jobs.
    client = TestClient(web.app, base_url="http://127.0.0.1")
    try:
        nonce = "a" * 32
        response = client.get("/api/local-auth/adk-status", params={"nonce": nonce})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert set(response.json()) == {"instance_id", "audience", "pid", "proof"}
        assert response.json()["proof"] == web._local_access.server_proof(nonce, "adk", 34567)
        assert len(seen) == 1
        assert seen[0].url.params["nonce"] == nonce
        assert web._local_access.token not in response.text
        assert "must-not-be-relayed" not in response.text
    finally:
        client.close()


@pytest.mark.parametrize("alteration", [
    {"instance_id": "other-account"}, {"audience": "web"},
    {"proof": "foreign-secret-proof"}, {"pid": -1}, {"pid": "34567"},
])
def test_srt_rejects_wrong_adk_identity(srt_web, monkeypatch, alteration):
    _mock_adk(monkeypatch, srt_web, alteration)
    client = TestClient(srt_web.app, base_url="http://127.0.0.1")
    try:
        assert client.get("/api/local-auth/adk-status", params={"nonce": "b" * 32}).status_code == 409
    finally:
        client.close()


@pytest.mark.parametrize("nonce", ["", "short", "a" * 33, "Z" * 32])
def test_srt_rejects_invalid_nonce_before_contacting_adk(srt_web, monkeypatch, nonce):
    seen = _mock_adk(monkeypatch, srt_web)
    client = TestClient(srt_web.app, base_url="http://127.0.0.1")
    try:
        assert client.get("/api/local-auth/adk-status", params={"nonce": nonce}).status_code == 422
        assert seen == []
    finally:
        client.close()


def test_adk_proof_route_is_absent_when_not_srt(srt_web, monkeypatch):
    monkeypatch.setattr(srt_web, "_is_local_srt_worker", False)
    with pytest.raises(srt_web.HTTPException) as caught:
        asyncio.run(srt_web.local_srt_adk_status("a" * 32))
    assert caught.value.status_code == 404
