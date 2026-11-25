"""Independent local-account authorization/migration checks with synthetic data."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import time
import uuid

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
import httpx
import pytest
from starlette.websockets import WebSocketDisconnect

from matcreator.local_access import ensure_local_access
from matcreator.local_accounts import AccountError, LocalAccountStore, install_local_accounts
import matcreator.local_accounts as account_module


PASSWORD = "synthetic-password-123"


class _FakeRuntimes:
    def __init__(self):
        self.calls = []
        self.entries = {}
        self.stopped = []

    async def ensure(self, account):
        self.calls.append(account["id"])
        if account["id"] not in self.entries:
            home = Path(account["profile_home"])
            access = ensure_local_access(home)
            port = 19000 + len(self.entries)
            self.entries[account["id"]] = SimpleNamespace(
                account_id=account["id"], home=home, access=access,
                web_url=f"http://127.0.0.1:{port}", adk_url=f"http://127.0.0.1:{port + 100}")
        return self.entries[account["id"]]


class _BytesStream(httpx.AsyncByteStream):
    def __init__(self, value):
        self.value = value

    async def __aiter__(self):
        yield self.value


class _BrokenStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"first-chunk"
        raise httpx.ReadError("Synthetic interrupted upstream stream")


@pytest.fixture
def local_accounts(monkeypatch, tmp_path):
    monkeypatch.setenv("MATCREATOR_MODE", "local")
    monkeypatch.setenv("MATCREATOR_FRONTEND_PORT", "18101")
    monkeypatch.setenv("MATCREATOR_WEB_PORT", "18102")
    monkeypatch.setenv("MATCREATOR_ADK_PORT", "18103")
    monkeypatch.delenv("MATCREATOR_LOCAL_ALLOWED_HOSTS", raising=False)
    home = tmp_path / "installation"
    app = FastAPI()

    @app.get("/")
    def index():
        return HTMLResponse("<h1>Local accounts</h1>")

    @app.get("/api/leaky-default-profile")
    def never_reach_default_profile():
        raise AssertionError("A protected request reached the broker's old root profile")

    manager = _FakeRuntimes()
    broker = install_local_accounts(app, home=home, source_root=tmp_path,
                                    public_paths={"/"}, runtime_manager=manager)
    seen = []

    def upstream(request):
        seen.append(request)
        runtime = next(item for item in manager.entries.values()
                       if request.url.port == int(item.web_url.rsplit(":", 1)[1]))
        assert request.headers.get("authorization") == "Bearer " + runtime.access.token
        assert "cookie" not in request.headers
        assert "origin" not in request.headers
        assert request.headers.get("host") == request.url.netloc.decode()
        if request.url.path == "/api/synthetic-broken-stream":
            return httpx.Response(200, stream=_BrokenStream())
        # Make worker cookies/cross-origin headers hostile on purpose; the
        # broker must not expose them to the browser.
        return httpx.Response(200, stream=_BytesStream(json.dumps({"worker_account": runtime.account_id,
                              "path": request.url.path, "query": str(request.url.query)}).encode()),
                              headers={"content-type": "application/json", "set-cookie": "worker-secret=private",
                                       "access-control-allow-origin": "*", "cache-control": "public"})

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(account_module.httpx, "AsyncClient", lambda **kwargs: real_async_client(
        transport=httpx.MockTransport(upstream), **kwargs))
    clients = []

    def client():
        value = TestClient(app, base_url=f"http://{broker.access.browser_host}:18101",
                           headers={"Origin": f"http://{broker.access.browser_host}:18101"})
        value._test_owner_access = broker.access
        clients.append(value)
        return value

    yield SimpleNamespace(app=app, broker=broker, store=broker.store, access=broker.access,
                          manager=manager, client=client, seen=seen, home=home)
    for item in clients:
        item.close()


def _register(client, name="Alice", **extra):
    status = client.get("/api/auth/status").json()
    if not status.get("registration_allowed"):
        owner = client._test_owner_access
        proof = client.post("/api/local-auth/login", json={"token": owner.token})
        assert proof.status_code == 200
        assert proof.json()["authenticated"] is False
    response = client.post("/api/auth/register", json={"display_name": name, "password": PASSWORD, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def test_password_hash_and_stable_identity_are_private_and_names_are_normalized(tmp_path):
    store = LocalAccountStore(tmp_path / "installation")
    account = store.register(" Alice ", PASSWORD)
    assert str(uuid.UUID(account["id"])) == account["id"]
    assert account["display_name"] == "Alice"
    assert account["username"] == "alice"
    assert Path(account["profile_home"]) == store.home / "profiles" / account["id"]
    assert "password_hash" not in account
    assert store.authenticate("ALICE", PASSWORD)["id"] == account["id"]
    with closing(sqlite3.connect(store.path)) as db:
        stored_hash = db.execute("SELECT password_hash FROM accounts").fetchone()[0]
    assert stored_hash.startswith("$argon2id$")
    assert PASSWORD not in stored_hash
    with pytest.raises(AccountError) as duplicate:
        store.register("ＡＬＩＣＥ", PASSWORD)
    assert duplicate.value.status == 409


def test_unknown_and_wrong_password_have_same_error_without_account_details(tmp_path):
    store = LocalAccountStore(tmp_path / "installation")
    store.register("Alice", PASSWORD)
    errors = []
    for username, password in (("missing", PASSWORD), ("Alice", "wrong-password")):
        with pytest.raises(AccountError) as error:
            store.authenticate(username, password)
        errors.append((error.value.status, error.value.detail))
    assert errors[0] == errors[1] == (401, "Account name or password is incorrect")


@pytest.mark.parametrize("password", ["short", "x" * 1025, "\ud800" * 10, None])
def test_invalid_registration_passwords_are_rejected(tmp_path, password):
    store = LocalAccountStore(tmp_path / "installation")
    with pytest.raises(AccountError) as error:
        store.register("Alice", password)
    assert error.value.status == 422
    assert store.status()["account_count"] == 0


def test_unregistered_and_old_instance_token_cannot_unlock_application(local_accounts):
    fixture = local_accounts
    client = fixture.client()
    status = client.get("/api/auth/status").json()
    assert status["authentication_method"] == "local_account"
    assert not status["authenticated"]
    assert status["registration_allowed"] is False
    assert "token" not in status
    for path in ("/api/leaky-default-profile", "/api/admin/sessions?user_id=admin", "/apps/MatCreator/users/user/sessions", "/list-apps"):
        assert client.get(path).status_code == 401
    assert client.post("/run_sse", json={}).status_code == 401
    # Explicit internal token is owner setup proof, never account auth.
    assert client.get("/api/leaky-default-profile", headers=fixture.access.headers()).status_code == 401
    bootstrap = client.post("/api/local-auth/login", json={"token": fixture.access.token})
    assert bootstrap.status_code == 200
    assert bootstrap.json()["authenticated"] is False
    assert client.get("/api/leaky-default-profile").status_code == 401
    assert fixture.manager.calls == []


def test_anonymous_registration_cannot_create_an_owner_uid_execution_profile(local_accounts):
    fixture = local_accounts
    client = fixture.client()
    response = client.post("/api/auth/register", json={"display_name": "Uninvited", "password": PASSWORD})
    assert response.status_code == 403
    assert fixture.store.status()["account_count"] == 0
    assert fixture.manager.calls == []
    assert fixture.manager.entries == {}


def test_existing_authenticated_account_can_provision_a_new_local_account(local_accounts):
    fixture = local_accounts
    client = fixture.client()
    alice = _register(client, "Alice")
    # The owner's launch proof is independent of Alice's established session.
    proof = client.cookies.get(fixture.access.cookie_name)
    if proof:
        fixture.access.revoke(proof)
        client.cookies.delete(fixture.access.cookie_name)
    assert client.get("/api/auth/status").json()["registration_allowed"] is True
    bob = _register(client, "Bob")
    assert alice["user_id"] != bob["user_id"]
    assert fixture.store.status()["account_count"] == 2


def test_two_accounts_select_only_their_own_runtime_and_strip_browser_credentials(local_accounts):
    fixture = local_accounts
    alice_client, bob_client = fixture.client(), fixture.client()
    alice, bob = _register(alice_client, "Alice"), _register(bob_client, "Bob")
    for client, expected, forged in ((alice_client, alice, bob), (bob_client, bob, alice)):
        response = client.get(f"/api/users/{forged['user_id']}/sessions?user_id={forged['user_id']}",
                              headers={"Authorization": "Bearer forged-browser-token"})
        assert response.status_code == 200
        assert response.json()["worker_account"] == expected["user_id"]
        assert response.headers["cache-control"] == "no-store"
        assert "set-cookie" not in response.headers
        assert "access-control-allow-origin" not in response.headers
    assert set(fixture.manager.calls) == {alice["user_id"], bob["user_id"]}


def test_switching_rotates_session_and_logout_does_not_stop_workers(local_accounts):
    fixture = local_accounts
    client = fixture.client()
    alice = _register(client, "Alice")
    alice_cookie = client.cookies.get(fixture.broker.cookie_name)
    assert client.get("/api/private").json()["worker_account"] == alice["user_id"]
    bob = _register(client, "Bob")
    bob_cookie = client.cookies.get(fixture.broker.cookie_name)
    assert alice_cookie != bob_cookie
    assert fixture.store.session_account(alice_cookie) is None
    assert client.get("/api/private").json()["worker_account"] == bob["user_id"]
    response = client.post("/api/auth/logout", json={"user_id": alice["user_id"]})
    assert response.status_code == 200
    assert fixture.store.session_account(bob_cookie) is None
    assert client.get("/api/private").status_code == 401
    assert fixture.manager.stopped == []


def test_password_change_uses_current_session_owner_and_invalidates_other_sessions(local_accounts):
    fixture = local_accounts
    a, b, second_a = fixture.client(), fixture.client(), fixture.client()
    alice = _register(a, "Alice")
    bob = _register(b, "Bob")
    assert second_a.post("/api/auth/login", json={"display_name": "Alice", "password": PASSWORD}).status_code == 200
    other_cookie = second_a.cookies.get(fixture.broker.cookie_name)
    response = a.post("/api/auth/set-password", json={"user_id": bob["user_id"],
                       "old_password": PASSWORD, "new_password": "new-synthetic-password"})
    assert response.status_code == 200
    assert fixture.store.session_account(other_cookie) is None
    assert fixture.store.authenticate("Bob", PASSWORD)["id"] == bob["user_id"]
    with pytest.raises(AccountError):
        fixture.store.authenticate("Alice", PASSWORD)
    assert fixture.store.authenticate("Alice", "new-synthetic-password")["id"] == alice["user_id"]
    assert a.get("/api/auth/status").json()["authenticated"] is True
    assert fixture.manager.stopped == []


def test_wrong_password_change_cannot_rotate_or_revoke_valid_session(local_accounts):
    fixture = local_accounts
    client = fixture.client()
    _register(client)
    cookie = client.cookies.get(fixture.broker.cookie_name)
    response = client.post("/api/auth/set-password", json={"old_password": "wrong-password", "new_password": "changed-password-123"})
    assert response.status_code == 401
    assert fixture.store.session_account(cookie) is not None


def test_legacy_data_claim_requires_owner_proof_is_once_only_and_maps_without_copy(local_accounts):
    fixture = local_accounts
    legacy = fixture.home / ".adk" / "session.db"
    legacy.parent.mkdir()
    legacy.write_bytes(b"synthetic-existing-database")
    client = fixture.client()
    rejected = client.post("/api/auth/register", json={"display_name": "Alice", "password": PASSWORD, "claim_existing_data": True})
    assert rejected.status_code == 403
    assert fixture.store.status()["account_count"] == 0
    assert legacy.read_bytes() == b"synthetic-existing-database"
    assert client.post("/api/local-auth/login", json={"token": fixture.access.token}).status_code == 200
    status = client.get("/api/auth/status").json()
    assert status["can_claim_legacy"] is True and status["authenticated"] is False
    setup_proof = client.cookies.get(fixture.access.cookie_name)
    owner = _register(client, "Alice", claim_existing_data=True)
    account = fixture.store.get(owner["user_id"])
    assert Path(account["profile_home"]) == fixture.home
    assert account["legacy"] is True
    assert legacy.read_bytes() == b"synthetic-existing-database"
    assert fixture.access.valid_session(setup_proof) is False
    assert client.post("/api/auth/register", json={"display_name": "Bob", "password": PASSWORD, "claim_existing_data": True}).status_code == 403
    assert client.post("/api/local-auth/login", json={"token": fixture.access.token}).status_code == 200
    assert client.post("/api/auth/register", json={"display_name": "Bob", "password": PASSWORD, "claim_existing_data": True}).status_code == 409
    assert fixture.store.status()["account_count"] == 1


def test_normal_registration_never_inherits_legacy_configuration(local_accounts):
    fixture = local_accounts
    source = fixture.home / "config.yaml"
    source.write_text("llm:\n  api_key: synthetic-owner-secret\n", encoding="utf-8")
    client = fixture.client()
    account = _register(client, "Alice")
    profile = Path(fixture.store.get(account["user_id"])["profile_home"])
    assert profile != fixture.home
    assert not (profile / "config.yaml").exists()
    assert source.read_text() == "llm:\n  api_key: synthetic-owner-secret\n"


def test_concurrent_legacy_claim_has_only_one_owner(tmp_path):
    store = LocalAccountStore(tmp_path / "installation")
    (store.home / "config.yaml").write_text("synthetic: true\n")
    def claim(name):
        try:
            return store.register(name, PASSWORD, claim_existing_data=True, owner_proof=True)
        except AccountError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("Alice", "Bob")))
    assert sum(isinstance(item, dict) for item in results) == 1
    assert 409 in results
    assert store.status()["account_count"] == 1


def test_session_expiry_and_stale_auth_version_prevent_replay(tmp_path):
    store = LocalAccountStore(tmp_path / "installation")
    account = store.register("Alice", PASSWORD)
    cookie = store.new_session(account["id"])
    with closing(sqlite3.connect(store.path)) as db:
        db.execute("UPDATE account_sessions SET expires=?", (time.time() - 1,))
        db.commit()
    assert store.session_account(cookie) is None
    store.change_password(account["id"], PASSWORD, "new-synthetic-password")
    with pytest.raises(AccountError):
        store.new_session(account["id"], account["auth_version"])


def test_cross_origin_and_shared_hostname_cannot_use_account_cookie(local_accounts):
    fixture = local_accounts
    client = fixture.client()
    _register(client)
    assert client.get("/api/private", headers={"Origin": "https://untrusted.invalid"}).status_code == 403
    assert client.get("/api/private", headers={"Host": "evil.invalid:18101"}).status_code == 403
    assert client.get("/api/private", headers={"Host": "127.0.0.1:18101", "Origin": ""}).status_code == 401
    assert client.post("/api/auth/logout", headers={"Origin": ""}).status_code == 403
    shared = TestClient(fixture.app, base_url="http://127.0.0.1:18101")
    try:
        response = shared.post("/api/auth/login", json={"display_name": "Alice", "password": PASSWORD})
        assert response.status_code == 409
        assert "set-cookie" not in response.headers
    finally:
        shared.close()


def test_unauthenticated_websocket_cannot_reach_or_start_a_runtime(local_accounts):
    fixture = local_accounts
    client = fixture.client()
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(f"ws://{fixture.access.browser_host}:18101/api/workspace/terminal"):
            pass
    assert error.value.code == 4401
    assert fixture.manager.calls == []


def test_stale_tab_expected_identity_cannot_use_another_accounts_cookie(local_accounts):
    fixture = local_accounts
    client = fixture.client()
    alice = _register(client, "Alice")
    bob = _register(client, "Bob")
    assert client.get("/api/private", headers={"X-MC-Account-ID": alice["user_id"]}).status_code == 401
    assert client.get("/api/private?expected_account_id=" + alice["user_id"]).status_code == 401
    response = client.get("/api/private", headers={"X-MC-Account-ID": bob["user_id"]})
    assert response.status_code == 200
    assert response.json()["worker_account"] == bob["user_id"]
    assert fixture.manager.calls == [bob["user_id"]]


def test_interrupted_upstream_stream_never_emits_a_second_response_start(local_accounts):
    fixture = local_accounts
    client = fixture.client()
    _register(client)
    response = client.get("/api/synthetic-broken-stream")
    assert response.status_code == 200
    assert response.content == b"first-chunk"


def test_logout_also_revokes_owner_setup_proof_for_the_next_browser_user(local_accounts):
    fixture = local_accounts
    (fixture.home / "config.yaml").write_text("synthetic: legacy-data\n")
    client = fixture.client()
    assert client.post("/api/local-auth/login", json={"token": fixture.access.token}).status_code == 200
    proof = client.cookies.get(fixture.access.cookie_name)
    assert fixture.access.valid_session(proof)
    _register(client, "Alice")
    assert client.post("/api/auth/logout", json={}).status_code == 200
    assert fixture.access.valid_session(proof) is False
    assert client.get("/api/auth/status").json()["can_claim_legacy"] is False


def test_store_rejects_redirected_account_database_before_creating_files(tmp_path):
    home = tmp_path / "installation"
    target = tmp_path / "other-account"
    home.mkdir()
    target.mkdir()
    link = home / ".local-accounts"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("This OS user cannot create symbolic links")
    with pytest.raises((ValueError, PermissionError, RuntimeError)):
        LocalAccountStore(home)
    assert not (target / "accounts.sqlite").exists()
