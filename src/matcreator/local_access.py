"""Local single-owner instance authentication, shared by Web and ADK.

The home directory is the isolation unit. This is not a sandbox against code
running under the same OS account, nor an authentication system for server mode.
"""
from __future__ import annotations

from collections import defaultdict, deque
from contextlib import closing
from functools import lru_cache
import csv
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

from starlette.requests import HTTPConnection, Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

SESSION_SECONDS = 12 * 60 * 60


@lru_cache(maxsize=1)
def _windows_user_sid() -> str:
    result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                            capture_output=True, text=True, check=True,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    sid = next(csv.reader(io.StringIO(result.stdout)))[-1].strip()
    if not re.fullmatch(r"S-\d+(?:-\d+)+", sid):
        raise PermissionError("Cannot resolve current Windows user SID")
    return sid


def _windows_private_acl(path: Path, *, directory: bool = False) -> None:
    """Replace explicit and inherited access rules for one local state path."""
    import ctypes
    from ctypes import wintypes

    sid = _windows_user_sid()
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    convert.restype = wintypes.BOOL
    apply = advapi.SetFileSecurityW
    apply.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    apply.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    descriptor = ctypes.c_void_p()
    inheritance = "OICI" if directory else ""
    sddl = f"D:P(A;{inheritance};FA;;;SY)(A;{inheritance};FA;;;{sid})"
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not apply(str(path), 0x80000004, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.LocalFree(descriptor)


def private_directory(path: Path) -> Path:
    path = path.expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        if path.stat().st_uid != os.geteuid():
            raise PermissionError(f"Local instance directory is owned by another OS user: {path}")
        path.chmod(0o700)
    elif os.name == "nt":
        _windows_private_acl(path, directory=True)
    return path


def _check_private_file(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise PermissionError(f"Instance identity must be a regular private file: {path}")
    if os.name == "posix":
        if info.st_uid != os.geteuid():
            raise PermissionError("Instance identity belongs to another OS user")
        path.chmod(0o600)
    elif os.name == "nt":
        _windows_private_acl(path)


class LocalAccess:
    def __init__(self, home: Path):
        self.home = private_directory(home)
        self.directory = private_directory(self.home / ".local-access")
        principal = _windows_user_sid() if os.name == "nt" else str(os.geteuid())
        home_identity = os.path.normcase(str(self.home)) + "|" + principal
        fingerprint = hashlib.sha256(home_identity.encode("utf-8")).hexdigest()
        self.token_path = self.directory / "identity.json"
        if not self.token_path.exists():
            identity = {"version": 2, "home_fingerprint": fingerprint,
                        "instance_id": secrets.token_hex(16),
                        "access_token": secrets.token_urlsafe(32)}
            # Hard-link a completed file: concurrent Web/ADK starts must never
            # observe partial JSON or overwrite another process's identity.
            fd, name = tempfile.mkstemp(prefix="identity-", dir=self.directory)
            temporary = Path(name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(identity, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, self.token_path)
                except FileExistsError:
                    pass
            finally:
                temporary.unlink(missing_ok=True)
        _check_private_file(self.token_path)
        identity = json.loads(self.token_path.read_text(encoding="utf-8"))
        if identity.get("version") != 2 or identity.get("home_fingerprint") != fingerprint:
            raise ValueError(
                "Local identity is not bound to this data directory and OS user. "
                "Use its original MATCREATOR_HOME or create a fresh private home; "
                "do not copy .local-access between instances."
            )
        if (not re.fullmatch(r"[a-f0-9]{32}", identity.get("instance_id", ""))
                or not re.fullmatch(r"[A-Za-z0-9_-]{43,}", identity.get("access_token", ""))):
            raise ValueError("Invalid local instance identity; restore its private identity file")
        self.instance_id = identity["instance_id"]
        self.browser_host = "mc-" + self.instance_id + ".localhost"
        self.token = identity["access_token"]
        self.cookie_name = "mc_local_" + self.instance_id[:16]
        self.session_path = self.directory / "sessions.sqlite"
        if os.path.lexists(self.session_path):
            _check_private_file(self.session_path)
        with closing(sqlite3.connect(self.session_path)) as db:
            db.execute("CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, expires REAL NOT NULL)")
            db.commit()
        _check_private_file(self.session_path)

    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.token}

    def server_proof(self, nonce: str, audience: str, pid: int) -> str:
        message = f"mc-local-server-v1|{self.instance_id}|{audience}|{pid}|{nonce}"
        return hmac.new(self.token.encode(), message.encode(), hashlib.sha256).hexdigest()

    def metadata(self, authenticated: bool = False) -> dict:
        result = {"status": "ok", "mode": "local", "instance_id": self.instance_id,
                  "browser_host": self.browser_host,
                  "authentication_required": True, "authenticated": authenticated}
        if authenticated:
            result["pid"] = os.getpid()
        return result

    def valid_token(self, value: str) -> bool:
        if not isinstance(value, str) or len(value) != len(self.token):
            return False
        try:
            return secrets.compare_digest(value.encode("utf-8"), self.token.encode("utf-8"))
        except UnicodeError:
            return False

    def new_session(self) -> str:
        value = secrets.token_urlsafe(32)
        with closing(sqlite3.connect(self.session_path)) as db:
            db.execute("DELETE FROM sessions WHERE expires <= ?", (time.time(),))
            db.execute("INSERT INTO sessions VALUES (?, ?)",
                       (hashlib.sha256(value.encode()).hexdigest(), time.time() + SESSION_SECONDS))
            db.commit()
        return value

    def valid_session(self, value: str) -> bool:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value):
            return False
        with closing(sqlite3.connect(self.session_path)) as db:
            row = db.execute("SELECT expires FROM sessions WHERE digest = ?",
                             (hashlib.sha256(value.encode()).hexdigest(),)).fetchone()
        return bool(row and row[0] > time.time())

    def revoke(self, value: str) -> None:
        with closing(sqlite3.connect(self.session_path)) as db:
            db.execute("DELETE FROM sessions WHERE digest = ?", (hashlib.sha256(value.encode()).hexdigest(),))
            db.commit()


def ensure_local_access(home: Path) -> LocalAccess:
    return LocalAccess(home)


_GATE = """<!doctype html><html><head><meta charset="utf-8"><title>MatCreator local access</title>
<meta name="referrer" content="no-referrer"><style>body{font:16px system-ui;max-width:440px;margin:12vh auto;padding:24px}input,button{font:inherit;padding:10px;box-sizing:border-box}input{width:100%;margin:16px 0}p{line-height:1.6}#error{color:#b22}</style></head><body>
<h1>MatCreator local access</h1><p>Enter the access token from this instance's private identity file, or run <code>matcreator access</code> to obtain its launch link.</p>
<form><label>Instance access token<input type="password" autocomplete="off" required></label><button>Unlock this instance</button></form><p id="error"></p>
<script>const fragment=new URLSearchParams(location.hash.slice(1));const initial=fragment.get('token');history.replaceState(null,'',location.pathname+location.search);async function unlock(token){const r=await fetch('/api/local-auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})});if(r.ok){location.replace('/')}else{document.querySelector('#error').textContent='Invalid access token for this instance.'}}document.querySelector('form').onsubmit=e=>{e.preventDefault();unlock(document.querySelector('input').value)};if(initial)unlock(initial);</script></body></html>"""


class LocalAccessMiddleware:
    def __init__(self, app, access: LocalAccess, audience: str = "web", public_paths=()):
        self.app, self.access, self.audience = app, access, audience
        self.public_paths = frozenset(public_paths)
        self.failures = defaultdict(deque)

    def _hosts(self) -> set[str]:
        hosts = {"localhost", "127.0.0.1", "::1", self.access.browser_host}
        for item in os.environ.get("MATCREATOR_LOCAL_ALLOWED_HOSTS", "").split(","):
            if item.strip():
                hosts.add(item.strip().lower())
        for key in ("MATCREATOR_FRONTEND_HOST", "MATCREATOR_WEB_HOST", "MATCREATOR_ADK_HOST"):
            value = os.environ.get(key, "").strip().lower()
            if value and value not in {"0.0.0.0", "::"}:
                hosts.add(value)
        return hosts

    def _origin_allowed(self, request: HTTPConnection) -> bool:
        origin = request.headers.get("origin")
        if not origin:
            return True
        try:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or parsed.hostname != self.access.browser_host:
                return False
            if parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
                return False
            from matcreator.ports import get_frontend_port, get_web_port, get_adk_port
            ports = {get_frontend_port(), get_web_port(), get_adk_port(), request.url.port or (443 if request.url.scheme == "https" else 80)}
            return (parsed.port or (443 if parsed.scheme == "https" else 80)) in ports
        except ValueError:
            return False

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        request = HTTPConnection(scope)
        path = scope.get("path", "/")
        try:
            valid_host = request.url.hostname in self._hosts()
        except ValueError:
            valid_host = False
        async def deny(status: int, detail: str):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4403 if status == 403 else 4401})
            else:
                await JSONResponse({"detail": detail}, status_code=status,
                                   headers={"Cache-Control": "no-store"})(scope, receive, send)
        if not valid_host:
            return await deny(403, "Host is not allowed for this local instance")
        if not self._origin_allowed(request):
            return await deny(403, "Origin is not allowed for this local instance")
        authorization = request.headers.get("authorization", "")
        bearer = authorization.startswith("Bearer ") and self.access.valid_token(authorization[7:])
        cookie = request.cookies.get(self.access.cookie_name, "")
        canonical_host = request.url.hostname == self.access.browser_host
        authenticated = bearer if authorization else canonical_host and self.access.valid_session(cookie)
        method = scope.get("method", "GET")
        if scope["type"] == "http":
            if path in {"/api/health", "/api/local-auth/status"} and method == "GET":
                metadata = self.access.metadata(authenticated)
                nonce = request.query_params.get("nonce", "")
                if path == "/api/local-auth/status" and re.fullmatch(r"[a-f0-9]{32}", nonce):
                    metadata.update(audience=self.audience, pid=os.getpid(),
                                    proof=self.access.server_proof(nonce, self.audience, os.getpid()))
                return await JSONResponse(metadata, headers={"Cache-Control": "no-store"})(scope, receive, send)
            if path == "/local-access" and method == "GET":
                if not canonical_host:
                    target = request.url.replace(hostname=self.access.browser_host)
                    return await RedirectResponse(str(target), status_code=303,
                                                  headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})(scope, receive, send)
                return await HTMLResponse(_GATE, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"})(scope, receive, send)
            if path == "/api/local-auth/login" and method == "POST":
                if not canonical_host:
                    metadata = self.access.metadata(False)
                    metadata["detail"] = "Use this instance's canonical browser host before entering its token"
                    return await JSONResponse(metadata, status_code=409,
                                              headers={"Cache-Control": "no-store"})(scope, receive, send)
                peer = scope.get("client") or ("unknown", 0)
                failures = self.failures[peer[0]]
                now = time.monotonic()
                while failures and failures[0] < now - 60:
                    failures.popleft()
                if len(failures) >= 20:
                    return await deny(429, "Too many invalid access attempts; retry later")
                if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                    return await deny(415, "Expected application/json")
                raw = bytearray()
                async for chunk in Request(scope, receive).stream():
                    raw.extend(chunk)
                    if len(raw) > 8192:
                        return await deny(413, "Access request too large")
                try:
                    body = json.loads(raw)
                    token = body.get("token", "") if isinstance(body, dict) else ""
                except (ValueError, UnicodeError):
                    token = ""
                if not isinstance(token, str) or not self.access.valid_token(token):
                    failures.append(now)
                    return await deny(401, "Invalid access token for this instance")
                response = JSONResponse(self.access.metadata(True), headers={"Cache-Control": "no-store"})
                response.set_cookie(self.access.cookie_name, self.access.new_session(), max_age=SESSION_SECONDS,
                                    httponly=True, samesite="strict", secure=request.url.scheme == "https", path="/")
                return await response(scope, receive, send)
            if self.audience == "web" and method in {"GET", "HEAD"} and path in self.public_paths:
                return await self.app(scope, receive, send)
        if not authenticated:
            if scope["type"] == "http" and self.audience == "adk" and method == "GET" and "text/html" in request.headers.get("accept", ""):
                return await RedirectResponse("/local-access", status_code=303)(scope, receive, send)
            return await deny(401, "Local instance access token required")
        if not authorization and ((scope["type"] == "websocket") or method not in {"GET", "HEAD", "OPTIONS"}):
            if not request.headers.get("origin"):
                return await deny(403, "Browser session requests require an Origin header")
        if path == "/api/local-auth/logout" and method == "POST":
            self.access.revoke(cookie)
            response = JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})
            response.delete_cookie(self.access.cookie_name, path="/")
            return await response(scope, receive, send)
        scope.setdefault("state", {})["local_instance_id"] = self.access.instance_id
        return await self.app(scope, receive, send)


def install_local_access(app, *, home: Path, audience: str = "web", public_paths=()) -> LocalAccess | None:
    if os.environ.get("MATCREATOR_MODE", "local") == "server":
        return None
    access = ensure_local_access(home)
    app.state.local_access = access
    app.add_middleware(LocalAccessMiddleware, access=access, audience=audience, public_paths=public_paths)
    return access


class InstanceLease:
    """Process-lifetime ownership of one service role for a local home."""
    def __init__(self, home: Path, role: str):
        if role not in {"web", "adk", "broker"}:
            raise ValueError("Unknown local service role")
        self.path = private_directory(home / ".local-access") / (role + ".lock")
        self.file = None

    def acquire(self):
        if self.path.exists() or self.path.is_symlink():
            _check_private_file(self.path)
        stream = self.path.open("a+b")
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                if self.path.stat().st_size == 0:
                    stream.write(b"0")
                    stream.flush()
                    stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise RuntimeError("This local home already has an active service of the same role; use a separate home") from exc
        self.file = stream
        return self

    def close(self):
        if self.file is not None:
            self.file.close()
            self.file = None
