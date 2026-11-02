"""Private local accounts and an authenticated per-profile application broker.

Account IDs are stable identities; browser-provided user IDs are never routing
credentials. LocalAccess tokens are internal service credentials / owner setup
proof only. No account or personal data is uploaded by this module.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from contextlib import closing
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import time
import unicodedata
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
import httpx
from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.websockets import WebSocket, WebSocketDisconnect

from .local_access import (SESSION_SECONDS, InstanceLease, LocalAccess,
                           LocalAccessMiddleware, _check_private_file,
                           ensure_local_access, private_directory)


class AccountError(Exception):
    def __init__(self, status: int, detail: str):
        self.status, self.detail = status, detail
        super().__init__(detail)


class LocalAccountStore:
    def __init__(self, home: Path):
        self.home = private_directory(home)
        if (self.home / ".local-accounts").is_symlink() or (self.home / "profiles").is_symlink():
            raise AccountError(409, "Account storage cannot be redirected by a symbolic link")
        self.directory = private_directory(self.home / ".local-accounts")
        self.path = self.directory / "accounts.sqlite"
        if os.path.lexists(self.path):
            _check_private_file(self.path)
        self.hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1)
        # Give unknown-name attempts the same expensive password check.
        self._dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
                    profile_home TEXT NOT NULL UNIQUE, legacy INTEGER NOT NULL DEFAULT 0,
                    workspace TEXT NOT NULL, auth_version INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_legacy_owner ON accounts(legacy) WHERE legacy=1;
                CREATE TABLE IF NOT EXISTS account_sessions (
                    digest TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                    auth_version INTEGER NOT NULL, expires REAL NOT NULL,
                    FOREIGN KEY(account_id) REFERENCES accounts(id)
                );
                CREATE TABLE IF NOT EXISTS account_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, account_id TEXT,
                    event TEXT NOT NULL, created_at REAL NOT NULL
                );
            """)
            db.commit()
        _check_private_file(self.path)

    def _db(self):
        return closing(sqlite3.connect(self.path, timeout=15))

    @staticmethod
    def _username(value: str) -> tuple[str, str]:
        if not isinstance(value, str):
            raise AccountError(422, "Enter an account name")
        display = unicodedata.normalize("NFKC", value).strip()
        if not 1 <= len(display) <= 64 or any(unicodedata.category(c).startswith("C") for c in display):
            raise AccountError(422, "Account name must contain 1–64 visible characters")
        return display.casefold(), display

    @staticmethod
    def _password(value: str) -> str:
        if not isinstance(value, str):
            raise AccountError(422, "Enter a password of at least 10 characters")
        try:
            valid = 10 <= len(value) and len(value.encode("utf-8")) <= 1024
        except UnicodeError:
            valid = False
        if not valid:
            raise AccountError(422, "Password must contain at least 10 characters and at most 1024 UTF-8 bytes")
        return value

    @staticmethod
    def _account(row) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        result.pop("password_hash", None)
        result["legacy"] = bool(result["legacy"])
        return result

    def get(self, account_id: str) -> dict | None:
        with self._db() as db:
            db.row_factory = sqlite3.Row
            return self._account(db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone())

    def status(self) -> dict:
        with self._db() as db:
            count, claimed = db.execute("SELECT COUNT(*), COALESCE(MAX(legacy),0) FROM accounts").fetchone()
        existing = (self.home / ".adk" / "session.db").exists() or (self.home / "config.yaml").exists()
        return {"account_count": count, "legacy_available": bool(existing and not claimed)}

    def register(self, username: str, password: str, *, claim_existing_data=False, owner_proof=False) -> dict:
        normalized, display = self._username(username)
        password_hash = self.hasher.hash(self._password(password))
        if claim_existing_data and not owner_proof:
            raise AccountError(403, "Open this installation from its owner launcher to claim existing data")
        account_id = str(uuid.uuid4())
        profile = self.home if claim_existing_data else self.home / "profiles" / account_id
        if (self.home / "profiles").is_symlink() or profile.is_symlink():
            raise AccountError(409, "Account profile storage cannot be a symbolic link")
        # Existing records/config are mapped in place, never copied or relabelled.
        workspace = str(profile / "workspace")
        if claim_existing_data:
            workspace = os.environ.get("MATCLAW_WORKSPACE", workspace)
        with self._db() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                if claim_existing_data and not self.status()["legacy_available"]:
                    raise AccountError(409, "Existing data is unavailable or already belongs to an account")
                db.execute("INSERT INTO accounts VALUES (?,?,?,?,?,?,?,?,?)", (
                    account_id, normalized, display, password_hash, str(profile.resolve()),
                    int(claim_existing_data), workspace, 0, time.time()))
                private_directory(profile)
                db.execute("INSERT INTO account_events(account_id,event,created_at) VALUES (?,?,?)",
                           (account_id, "legacy_claim" if claim_existing_data else "registered", time.time()))
                db.commit()
            except sqlite3.IntegrityError as exc:
                db.rollback()
                raise AccountError(409, "Account name is already in use or existing data was already claimed") from exc
        return self.get(account_id)

    def authenticate(self, username: str, password: str) -> dict:
        normalized, _ = self._username(username)
        # Cap malformed login values before invoking the password library.
        if not isinstance(password, str) or len(password) > 1024:
            raise AccountError(401, "Account name or password is incorrect")
        with self._db() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM accounts WHERE username=?", (normalized,)).fetchone()
        try:
            valid = self.hasher.verify(row["password_hash"] if row else self._dummy_hash, password)
        except (VerificationError, InvalidHashError, UnicodeError):
            valid = False
        if not row or not valid:
            raise AccountError(401, "Account name or password is incorrect")
        return self._account(row)

    def new_session(self, account_id: str, auth_version: int | None = None) -> str:
        value = secrets.token_urlsafe(32)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT auth_version FROM accounts WHERE id=?", (account_id,)).fetchone()
            if not row or (auth_version is not None and row[0] != auth_version):
                raise AccountError(401, "Account credentials changed; log in again")
            db.execute("DELETE FROM account_sessions WHERE expires<=?", (time.time(),))
            db.execute("INSERT INTO account_sessions VALUES (?,?,?,?)",
                       (hashlib.sha256(value.encode()).hexdigest(), account_id, row[0], time.time() + SESSION_SECONDS))
            db.commit()
        return value

    def session_account(self, value: str) -> dict | None:
        if not isinstance(value, str) or len(value) != 43:
            return None
        try:
            digest = hashlib.sha256(value.encode("ascii")).hexdigest()
        except UnicodeError:
            return None
        with self._db() as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT a.* FROM account_sessions s JOIN accounts a ON a.id=s.account_id "
                             "WHERE s.digest=? AND s.expires>? AND s.auth_version=a.auth_version",
                             (digest, time.time())).fetchone()
        return self._account(row)

    def revoke(self, value: str):
        with self._db() as db:
            db.execute("DELETE FROM account_sessions WHERE digest=?", (hashlib.sha256(value.encode()).hexdigest(),))
            db.commit()

    def change_password(self, account_id: str, old_password: str, new_password: str):
        account = self.get(account_id)
        if not account:
            raise AccountError(401, "Local account login required")
        verified = self.authenticate(account["username"], old_password)
        password_hash = self.hasher.hash(self._password(new_password))
        with self._db() as db:
            changed = db.execute("UPDATE accounts SET password_hash=?, auth_version=auth_version+1 "
                                 "WHERE id=? AND auth_version=?", (password_hash, account_id, verified["auth_version"]))
            if changed.rowcount != 1:
                raise AccountError(409, "Account credentials changed; log in again")
            db.execute("DELETE FROM account_sessions WHERE account_id=?", (account_id,))
            db.execute("INSERT INTO account_events(account_id,event,created_at) VALUES (?,?,?)",
                       (account_id, "password_changed", time.time()))
            db.commit()


_HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"}


class LocalAccountBroker:
    def __init__(self, app, *, store: LocalAccountStore, access: LocalAccess,
                 runtime_manager, public_paths=()):
        self.app, self.store, self.access, self.runtime_manager = app, store, access, runtime_manager
        self.public_paths = frozenset(public_paths)
        self.cookie_name = "mc_account_" + access.instance_id[:16]
        self.policy = LocalAccessMiddleware(app, access)
        self.failures = defaultdict(deque)
        self._password_slots = asyncio.Semaphore(2)

    def metadata(self, account=None, owner_proof=False):
        result = self.access.metadata(False)
        result.update(authentication_method="local_account", authenticated=bool(account),
                      registration_allowed=bool(account or owner_proof),
                      execution_isolation=getattr(self.runtime_manager, "execution_isolation", "separate_processes_same_os_user"),
                      runtime_backend=getattr(self.runtime_manager, "runtime_backend", "process"),
                      **self.store.status())
        supports_legacy = getattr(self.runtime_manager, "supports_legacy", True)
        result["can_claim_legacy"] = bool(owner_proof and result["legacy_available"] and supports_legacy)
        result["legacy_migration_required"] = bool(not supports_legacy and (result["legacy_available"] or (account and account.get("legacy"))))
        if account:
            result["user"] = {key: account[key] for key in ("id", "username", "display_name")}
            result["user_id"] = account["id"]
        return result

    def _result(self, account):
        return {"user_id": account["id"], "profile_id": account["id"], "username": account["username"],
                "display_name": account["display_name"], "is_admin": False}

    def _cookie(self, response, request, value):
        response.set_cookie(self.cookie_name, value, max_age=SESSION_SECONDS, httponly=True,
                            secure=request.url.scheme == "https", samesite="strict", path="/")

    async def _body(self, scope, receive):
        request = Request(scope, receive)
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            raise AccountError(415, "Expected application/json")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 8192:
                raise AccountError(413, "Account request too large")
        try:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError()
            return body
        except (ValueError, UnicodeError) as exc:
            raise AccountError(422, "Expected a JSON object") from exc

    async def _credentials(self, request, callback):
        peer = request.scope.get("client") or ("unknown", 0)
        failures = self.failures[peer[0]]
        now = time.monotonic()
        while failures and failures[0] <= now - 60:
            failures.popleft()
        if len(failures) >= 10:
            raise AccountError(429, "Too many account attempts; retry later")
        async with self._password_slots:
            try:
                return await asyncio.to_thread(callback)
            except AccountError:
                failures.append(now)
                raise

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        request = HTTPConnection(scope)
        path, method = scope.get("path", "/"), scope.get("method", "GET")
        response_started = False
        async def forward(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)
        try:
            if request.url.hostname not in self.policy._hosts() or not self.policy._origin_allowed(request):
                raise AccountError(403, "Host or Origin is not allowed for this local installation")
            canonical = request.url.hostname == self.access.browser_host
            value = request.cookies.get(self.cookie_name, "")
            account = self.store.session_account(value) if canonical else None
            authorization = request.headers.get("authorization", "")
            owner_proof = ((authorization.startswith("Bearer ") and self.access.valid_token(authorization[7:]))
                           or (canonical and self.access.valid_session(request.cookies.get(self.access.cookie_name, ""))))
            if scope["type"] == "http" and method == "GET":
                if path in {"/api/health", "/api/auth/status", "/api/auth/me", "/api/local-auth/status"}:
                    result = self.metadata(account, owner_proof)
                    return await JSONResponse(result, headers={"Cache-Control": "no-store"})(scope, receive, send)
                if path == "/local-access":
                    return await RedirectResponse(str(request.url.replace(hostname=self.access.browser_host, path="/", query="")),
                                                  status_code=303, headers={"Cache-Control": "no-store"})(scope, receive, send)
                if path in self.public_paths:
                    return await self.app(scope, receive, send)
            if not canonical:
                raise AccountError(409 if path.startswith("/api/auth/") or path == "/api/local-auth/login" else 401,
                                   "Use this installation's browser host to log in")
            if scope["type"] == "http" and path == "/api/local-auth/login" and method == "POST":
                # Backward-compatible launcher link establishes setup proof,
                # never an account session or general application access.
                body = await self._body(scope, receive)
                if not self.access.valid_token(body.get("token", "")):
                    raise AccountError(401, "Owner launcher proof is invalid")
                response = JSONResponse(self.metadata(account, True), headers={"Cache-Control": "no-store"})
                response.set_cookie(self.access.cookie_name, self.access.new_session(), max_age=SESSION_SECONDS,
                                    httponly=True, secure=request.url.scheme == "https", samesite="strict", path="/")
                return await response(scope, receive, send)
            if scope["type"] == "http" and path in {"/api/auth/login", "/api/auth/register"} and method == "POST":
                if path.endswith("register") and not (owner_proof or account):
                    raise AccountError(403, "Open the local launcher or sign in before creating another account")
                body = await self._body(scope, receive)
                username = body.get("username", body.get("display_name", ""))
                if path.endswith("register"):
                    claim = body.get("claim_existing_data", False)
                    if not isinstance(claim, bool):
                        raise AccountError(422, "claim_existing_data must be a boolean")
                    if claim and not getattr(self.runtime_manager, "supports_legacy", True):
                        raise AccountError(409, "Existing installation data needs an offline migration before isolated execution; no data was moved")
                    account = await self._credentials(request, lambda: self.store.register(username, body.get("password"),
                                                       claim_existing_data=claim, owner_proof=owner_proof))
                else:
                    account = await self._credentials(request, lambda: self.store.authenticate(username, body.get("password")))
                    if account.get("legacy") and not getattr(self.runtime_manager, "supports_legacy", True):
                        raise AccountError(409, "This account's existing data needs an offline migration before isolated execution; sign in through its original process installation")
                # Rotate browser login and revoke the previous profile session.
                if value:
                    self.store.revoke(value)
                session = self.store.new_session(account["id"], account["auth_version"])
                response = JSONResponse(self._result(account), status_code=201 if path.endswith("register") else 200,
                                        headers={"Cache-Control": "no-store"})
                self._cookie(response, request, session)
                if path.endswith("register") and owner_proof:
                    self.access.revoke(request.cookies.get(self.access.cookie_name, ""))
                    response.delete_cookie(self.access.cookie_name, path="/")
                return await response(scope, receive, send)
            if not account:
                raise AccountError(401, "Local account login required")
            expected_account = request.headers.get("x-mc-account-id") or request.query_params.get("expected_account_id")
            if expected_account and expected_account != account["id"]:
                # A different tab may have switched the shared browser cookie.
                raise AccountError(401, "Local account login required")
            if (scope["type"] == "websocket" or method not in {"GET", "HEAD", "OPTIONS"}) and not request.headers.get("origin"):
                raise AccountError(403, "Account session requests require an Origin header")
            if scope["type"] == "http" and path in {"/api/auth/logout", "/api/local-auth/logout"} and method == "POST":
                self.store.revoke(value)
                self.access.revoke(request.cookies.get(self.access.cookie_name, ""))
                response = JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})
                response.delete_cookie(self.cookie_name, path="/")
                response.delete_cookie(self.access.cookie_name, path="/")
                return await response(scope, receive, send)
            if scope["type"] == "http" and path == "/api/auth/set-password" and method == "POST":
                body = await self._body(scope, receive)
                await self._credentials(request, lambda: self.store.change_password(account["id"], body.get("old_password"), body.get("new_password")))
                account = self.store.get(account["id"])
                response = JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})
                self._cookie(response, request, self.store.new_session(account["id"], account["auth_version"]))
                return await response(scope, receive, send)
            if path.startswith("/api/auth/") or path.startswith("/api/local-auth/"):
                raise AccountError(404, "Unknown account operation")
            runtime = await self.runtime_manager.ensure(account)
            if scope["type"] == "websocket":
                return await self._websocket(scope, receive, send, runtime, value)
            # Every application route, including admin-like and file endpoints,
            # is confined to the verified session's own worker process.
            return await self._http(scope, receive, forward, runtime, value)
        except AccountError as exc:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 4401 if exc.status == 401 else 4403})
            return await JSONResponse({"detail": exc.detail}, status_code=exc.status,
                                      headers={"Cache-Control": "no-store"})(scope, receive, send)
        except (RuntimeError, httpx.HTTPError) as exc:
            logging.getLogger(__name__).exception("Local account runtime relay failed")
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1011})
            if response_started:
                return await send({"type": "http.response.body", "body": b"", "more_body": False})
            return await JSONResponse({"detail": "The account runtime is unavailable; no replacement task was submitted"},
                                      status_code=503, headers={"Cache-Control": "no-store"})(scope, receive, send)

    async def _http(self, scope, receive, send, runtime, session):
        request = Request(scope, receive)
        path = scope["path"]
        query = scope.get("query_string", b"").decode("ascii")
        url = runtime.web_url.rstrip("/") + path + ("?" + query if query else "")
        headers = {key: val for key, val in request.headers.items()
                   if key.lower() not in _HOP_HEADERS | {"host", "cookie", "authorization", "origin", "content-length"}}
        headers.update(runtime.access.headers())
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=None), trust_env=False) as client:
            upstream_request = client.build_request(request.method, url, content=await request.body(), headers=headers)
            async with client.stream(upstream_request.method, upstream_request.url,
                                     headers=upstream_request.headers, content=upstream_request.content) as response:
                outgoing = [(key.encode("latin-1"), val.encode("latin-1")) for key, val in response.headers.multi_items()
                            if key.lower() not in _HOP_HEADERS | {"set-cookie", "access-control-allow-origin"}]
                # Account-specific API responses must not be reused after a switch.
                outgoing = [(key, val) for key, val in outgoing if key.lower() != b"cache-control"]
                outgoing.append((b"cache-control", b"no-store"))
                await send({"type": "http.response.start", "status": response.status_code, "headers": outgoing})
                async for chunk in response.aiter_raw():
                    if not self.store.session_account(session):
                        break
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _websocket(self, scope, receive, send, runtime, session):
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed
        path = scope["path"]
        query = scope.get("query_string", b"").decode("ascii")
        target = runtime.web_url.replace("http://", "ws://", 1).rstrip("/") + path + ("?" + query if query else "")
        websocket = WebSocket(scope, receive=receive, send=send)
        async with connect(target, additional_headers=runtime.access.headers(), proxy=None) as upstream:
            await websocket.accept()
            async def inbound():
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect" or not self.store.session_account(session):
                        return
                    if message.get("text") is not None:
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send(message["bytes"])
            async def outbound():
                async for message in upstream:
                    if not self.store.session_account(session):
                        return
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)
            async def expired():
                while self.store.session_account(session):
                    await asyncio.sleep(1)
            tasks = [asyncio.create_task(fn()) for fn in (inbound, outbound, expired)]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                try:
                    await websocket.close(code=1000 if self.store.session_account(session) else 4401)
                except RuntimeError:
                    pass


def install_local_accounts(app, *, home: Path, source_root: Path, public_paths=(), runtime_manager=None):
    from .local_account_backends import create_local_runtime_manager
    access = ensure_local_access(home)
    store = LocalAccountStore(home)
    manager = runtime_manager or create_local_runtime_manager(home, source_root)
    broker = LocalAccountBroker(app, store=store, access=access, runtime_manager=manager, public_paths=public_paths)
    app.state.local_accounts = broker
    # Install a single already-constructed broker to keep limiter/session state.
    app.add_middleware(_BrokerMiddleware, broker=broker)
    return broker


class _BrokerMiddleware:
    def __init__(self, app, broker):
        self.broker, self.app = broker, app
        broker.app = app
    async def __call__(self, scope, receive, send):
        return await self.broker(scope, receive, send)
