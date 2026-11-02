"""Durable private Web/ADK runtimes for local application accounts.

The broker owns authentication; these processes own only one account's home.
Logging out or stopping the broker never terminates an account's running work.
This process separation is not a sandbox against the shared operating-system
user; deployments with mutually untrusted users need OS/container isolation.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any
import uuid

import httpx

from .local_access import LocalAccess, ensure_local_access, private_directory


class LocalRuntimeConflict(RuntimeError):
    """Existing process/storage identity is uncertain; never replace blindly."""


@dataclass
class LocalAccountRuntime:
    account_id: str
    home: Path
    workspace: Path
    web_url: str
    adk_url: str
    web_port: int
    adk_port: int
    access: LocalAccess = field(repr=False)
    web_pid: int | None = None
    adk_pid: int | None = None
    verified_at: float = 0

    def safe_status(self) -> dict[str, Any]:
        return {"account_id": self.account_id, "profile_home": str(self.home),
                "workspace": str(self.workspace), "instance_id": self.access.instance_id,
                "web_port": self.web_port, "adk_port": self.adk_port,
                "web_pid": self.web_pid, "adk_pid": self.adk_pid}


def _pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied still means occupied.
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextmanager
def _exclusive_file(path: Path):
    """Short-lived cross-process launch ownership, separate from worker locks."""
    if path.is_symlink():
        raise LocalRuntimeConflict("Runtime launch lock cannot be a symbolic link")
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    stream = os.fdopen(descriptor, "r+b")
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        stream.close()
        raise LocalRuntimeConflict("Another process owns this account runtime; retry without creating a replacement") from exc
    try:
        yield
    finally:
        stream.close()


class LocalAccountRuntimeManager:
    execution_isolation = "separate_processes_same_os_user"
    runtime_backend = "process"
    supports_legacy = True

    def __init__(self, home: Path, source_root: Path, python: str = sys.executable,
                 *, startup_timeout: float = 75.0):
        self.home = private_directory(Path(home))
        self.source_root = Path(source_root).expanduser().resolve()
        self.python = str(python)
        if ((self.home / "profiles").is_symlink() or (self.home / ".local-accounts").is_symlink()
                or (self.home / ".local-accounts" / "runtimes").is_symlink()):
            raise LocalRuntimeConflict("Account storage root cannot be a symbolic link")
        self.record_directory = private_directory(self.home / ".local-accounts" / "runtimes")
        self.profile_directory = private_directory(self.home / "profiles")
        self.startup_timeout = startup_timeout
        self._locks: dict[str, asyncio.Lock] = {}
        self._runtimes: dict[str, LocalAccountRuntime] = {}
        self._spawned: dict[str, list[subprocess.Popen]] = {}

    def _account_id(self, account: dict) -> str:
        value = str(account.get("id", ""))
        try:
            if str(uuid.UUID(value)) != value:
                raise ValueError
        except (ValueError, AttributeError) as exc:
            raise LocalRuntimeConflict("A stable account UUID is required") from exc
        return value

    def _paths(self, account: dict) -> tuple[str, Path, Path]:
        account_id = self._account_id(account)
        expected = self.home if account.get("legacy") else self.profile_directory / account_id
        candidate = Path(account.get("profile_home") or expected).expanduser()
        if candidate.is_symlink() or (self.home / "profiles").is_symlink():
            raise LocalRuntimeConflict("Account data directory cannot be a symbolic link")
        profile_home = candidate.resolve()
        if profile_home != expected.resolve():
            raise LocalRuntimeConflict("Account data directory does not match its stable account identity")
        if account.get("legacy"):
            workspace = Path(account.get("workspace") or profile_home / "workspace").expanduser().resolve()
        else:
            workspace = profile_home / "workspace"
        return account_id, profile_home, workspace

    def _record_path(self, account_id: str) -> Path:
        return self.record_directory / (account_id + ".json")

    def _read_record(self, account_id: str) -> dict | None:
        path = self._record_path(account_id)
        if path.is_symlink():
            raise LocalRuntimeConflict("Runtime record cannot be a symbolic link")
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("version") != 1 or value.get("account_id") != account_id:
                raise ValueError
            return value
        except (OSError, ValueError) as exc:
            raise LocalRuntimeConflict("Runtime record is unreadable; inspect it before starting another process") from exc

    def _write_record(self, account_id: str, record: dict) -> None:
        fd, filename = tempfile.mkstemp(prefix=account_id + "-", suffix=".tmp", dir=self.record_directory)
        temporary = Path(filename)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(record, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            # Windows readers/indexers may briefly open the old JSON without
            # delete sharing. Preserve atomic replacement, and retry only the
            # bounded sharing/access race instead of truncating a live record.
            for attempt in range(6):
                try:
                    temporary.replace(self._record_path(account_id))
                    break
                except PermissionError as exc:
                    if os.name != "nt" or getattr(exc, "winerror", None) not in {5, 32} or attempt == 5:
                        raise
                    time.sleep(0.01 * 2 ** attempt)
        finally:
            temporary.unlink(missing_ok=True)

    def _from_record(self, account_id: str, home: Path, workspace: Path,
                     access: LocalAccess, record: dict) -> LocalAccountRuntime:
        if (Path(record.get("home", "")).resolve() != home
                or Path(record.get("workspace", "")).resolve() != workspace
                or record.get("instance_id") != access.instance_id):
            raise LocalRuntimeConflict("Stored runtime identity does not match this account home")
        web_port, adk_port = record.get("web_port"), record.get("adk_port")
        if (not isinstance(web_port, int) or not isinstance(adk_port, int)
                or not 1 <= web_port <= 65535 or not 1 <= adk_port <= 65535 or web_port == adk_port):
            raise LocalRuntimeConflict("Invalid stored private runtime endpoints")
        return LocalAccountRuntime(account_id, home, workspace,
                                   f"http://127.0.0.1:{web_port}", f"http://127.0.0.1:{adk_port}",
                                   web_port, adk_port, access, record.get("web_pid"), record.get("adk_pid"))

    async def _probe(self, client: httpx.AsyncClient, runtime: LocalAccountRuntime,
                     role: str) -> tuple[str, int | None]:
        nonce = secrets.token_hex(16)
        url = runtime.web_url if role == "web" else runtime.adk_url
        try:
            response = await client.get(url + "/api/local-auth/status", params={"nonce": nonce}, timeout=2.0)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return "absent", None
        except httpx.HTTPError as exc:
            raise LocalRuntimeConflict("Private runtime endpoint state is uncertain; no replacement was started") from exc
        try:
            value = response.json()
            pid = value.get("pid")
            proof = value.get("proof", "")
            if (response.status_code != 200 or value.get("instance_id") != runtime.access.instance_id
                    or value.get("audience") != role or not isinstance(pid, int) or pid <= 0
                    or not isinstance(proof, str)
                    or not secrets.compare_digest(proof, runtime.access.server_proof(nonce, role, pid))):
                raise ValueError
            return "ready", pid
        except (ValueError, TypeError, AttributeError) as exc:
            raise LocalRuntimeConflict("An occupied private endpoint belongs to a different instance; no credential was forwarded") from exc

    async def _ready(self, runtime: LocalAccountRuntime) -> tuple[bool, bool]:
        async with httpx.AsyncClient(trust_env=False) as client:
            web, adk = await asyncio.gather(self._probe(client, runtime, "web"), self._probe(client, runtime, "adk"))
        if web[0] == "ready":
            runtime.web_pid = web[1]
        if adk[0] == "ready":
            runtime.adk_pid = adk[1]
        if web[0] == adk[0] == "ready":
            runtime.verified_at = time.monotonic()
        return web[0] == "ready", adk[0] == "ready"

    def _child_environment(self, runtime: LocalAccountRuntime) -> dict[str, str]:
        # Never inherit another account's provider credentials, configuration,
        # workspace, XRD output root, or in-memory model selection.
        sensitive_prefixes = ("LLM_", "EMBEDDING_", "BOHRIUM_", "MINIMAX_", "OPENAI_", "ANTHROPIC_",
                              "GEMINI_", "KDG_", "MATCREATOR_", "MATCLAW_", "MAT_BENCH_", "OAW_",
                              "AWS_", "AZURE_", "GCP_", "GOOGLE_", "HF_", "WANDB_", "E2B_", "TAVILY_", "DOCKER_")
        exact = {"GRAPH_AGENT_MODEL", "REVIEW_AGENT_MODEL", "SMALL_MODEL", "MP_API_KEY", "GOOGLE_API_KEY",
                 "DEEPMD_MODEL_PATH", "SERVER_ONLY_FLAG", "SSH_AUTH_SOCK", "SSH_AGENT_PID"}
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(sensitive_prefixes) and key not in exact
               and not key.endswith(("_API_KEY", "_TOKEN", "_PASSWORD", "_SECRET", "_ACCESS_KEY"))}
        for executable_path in ("MATCREATOR_XRD_PYTHON",):
            if os.environ.get(executable_path):
                env[executable_path] = os.environ[executable_path]
        env.update({"PYTHONPATH": str(self.source_root / "src") + os.pathsep + str(self.source_root),
                    "PYTHONUNBUFFERED": "1", "MATCREATOR_MODE": "local",
                    "MATCREATOR_LOCAL_PROFILE_WORKER": "1", "MATCREATOR_LOCAL_ACCOUNT_ID": runtime.account_id,
                    "MATCREATOR_LOCAL_ACCOUNT_ROOT": str(self.home),
                    "MATCREATOR_HOME": str(runtime.home), "MATCREATOR_CONFIG_PATH": str(runtime.home / "config.yaml"),
                    "MATCLAW_WORKSPACE": str(runtime.workspace), "MATCREATOR_ADK_HOST": "127.0.0.1",
                    "MATCREATOR_WEB_HOST": "127.0.0.1", "MATCREATOR_ADK_PORT": str(runtime.adk_port),
                    "ADK_LOCAL_PORT": str(runtime.adk_port), "MATCREATOR_WEB_PORT": str(runtime.web_port),
                    "MATCREATOR_FRONTEND_PORT": str(runtime.web_port)})
        return env

    def _reserve_ports(self) -> tuple[int, int]:
        reservations: list[socket.socket] = []
        try:
            for _ in range(2):
                listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                if os.name == "nt":
                    listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                listener.bind(("127.0.0.1", 0))
                reservations.append(listener)
            return tuple(listener.getsockname()[1] for listener in reservations)
        finally:
            for listener in reservations:
                listener.close()

    def _spawn(self, runtime: LocalAccountRuntime, role: str) -> subprocess.Popen:
        logs = private_directory(runtime.home / "logs" / "account-runtime")
        if role == "adk":
            args = [self.python, "-m", "matcreator.scripts.start_agent", "api-server", "--host", "127.0.0.1",
                    "--port", str(runtime.adk_port), "--workspace", str(runtime.workspace), "--log-level", "info"]
        else:
            args = [self.python, "-m", "uvicorn", "web.main:app", "--host", "127.0.0.1", "--port", str(runtime.web_port)]
        kwargs: dict[str, Any] = {"cwd": str(self.source_root), "env": self._child_environment(runtime),
                                  "stdin": subprocess.DEVNULL, "close_fds": True}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        with (logs / (role + ".out.log")).open("ab", buffering=0) as stdout, (logs / (role + ".err.log")).open("ab", buffering=0) as stderr:
            return subprocess.Popen(args, stdout=stdout, stderr=stderr, **kwargs)

    def _assert_no_unrecorded_owner(self, home: Path) -> None:
        adk = private_directory(home / ".adk") / "server.lock"
        web = private_directory(home / ".local-access") / "web.lock"
        with _exclusive_file(adk), _exclusive_file(web):
            pass

    async def _wait_ready(self, runtime: LocalAccountRuntime, record: dict) -> LocalAccountRuntime:
        deadline = time.monotonic() + self.startup_timeout
        while True:
            web, adk = await self._ready(runtime)
            if web and adk:
                record.update(state="ready", web_pid=runtime.web_pid, adk_pid=runtime.adk_pid,
                              ready_at=time.time())
                self._write_record(runtime.account_id, record)
                self._runtimes[runtime.account_id] = runtime
                return runtime
            owned = self._spawned.get(runtime.account_id, [])
            if any(process.poll() is not None for process in owned):
                record.update(state="attention", detail="A runtime component exited during startup")
                self._write_record(runtime.account_id, record)
                raise LocalRuntimeConflict("An account runtime component exited; inspect its private logs. Remaining components were retained, not replaced.")
            if time.monotonic() >= deadline:
                record.update(state="attention", detail="Runtime readiness was not confirmed before timeout")
                self._write_record(runtime.account_id, record)
                raise LocalRuntimeConflict("Account runtime startup is not confirmed; inspect its stable record and logs before retrying")
            await asyncio.sleep(0.2)

    async def ensure(self, account: dict) -> LocalAccountRuntime:
        account_id, home, workspace = self._paths(account)
        async with self._locks.setdefault(account_id, asyncio.Lock()):
            cached = self._runtimes.get(account_id)
            if cached and cached.home == home and cached.workspace == workspace and time.monotonic() - cached.verified_at < 1:
                return cached
            with _exclusive_file(self.record_directory / (account_id + ".lock")):
                record = self._read_record(account_id)
                if record and record.get("backend", "process") != "process":
                    raise LocalRuntimeConflict("This account already uses another runtime backend; an offline migration is required")
                access = ensure_local_access(home)
                if record:
                    runtime = self._from_record(account_id, home, workspace, access, record)
                    web, adk = await self._ready(runtime)
                    if web and adk:
                        self._runtimes[account_id] = runtime
                        return runtime
                    if record.get("state") == "starting" and any(_pid_alive(record.get(key)) for key in ("web_pid", "adk_pid", "web_launcher_pid", "adk_launcher_pid")):
                        return await self._wait_ready(runtime, record)
                    if web or adk or any(_pid_alive(record.get(key)) for key in ("web_pid", "adk_pid", "web_launcher_pid", "adk_launcher_pid")):
                        raise LocalRuntimeConflict("An existing account runtime is only partially available; preserve it and inspect its stable record")
                self._assert_no_unrecorded_owner(home)
                private_directory(workspace)
                adk_port, web_port = self._reserve_ports()
                runtime = LocalAccountRuntime(account_id, home, workspace,
                                              f"http://127.0.0.1:{web_port}", f"http://127.0.0.1:{adk_port}",
                                              web_port, adk_port, access)
                record = {"version": 1, "account_id": account_id, "home": str(home), "workspace": str(workspace),
                          "instance_id": access.instance_id, "source_root": str(self.source_root),
                          "web_port": web_port, "adk_port": adk_port, "state": "starting", "created_at": time.time()}
                self._write_record(account_id, record)
                self._spawned[account_id] = []
                try:
                    for role in ("adk", "web"):
                        process = self._spawn(runtime, role)
                        self._spawned[account_id].append(process)
                        record[role + "_launcher_pid"] = process.pid
                        self._write_record(account_id, record)
                except OSError as exc:
                    record.update(state="attention", detail="Could not launch all account runtime components")
                    self._write_record(account_id, record)
                    raise LocalRuntimeConflict("Could not launch all runtime components; existing children were retained for inspection") from exc
                return await self._wait_ready(runtime, record)

    def report_status(self) -> list[dict[str, Any]]:
        """Safe administrative snapshots; never contains access credentials."""
        results = []
        for path in sorted(self.record_directory.glob("*.json")):
            try:
                value = self._read_record(path.stem)
                if value:
                    results.append({key: value.get(key) for key in ("account_id", "home", "workspace", "instance_id", "source_root",
                                                                  "state", "web_port", "adk_port", "web_pid", "adk_pid")})
            except LocalRuntimeConflict:
                results.append({"account_id": path.stem, "state": "attention"})
        return results

    async def shutdown_broker(self) -> None:
        """Intentionally retain user workers; broker logout/restart is not cancellation."""
        return None

    async def stop_test_owned(self, account_id: str) -> None:
        """Explicit cleanup for synthetic tests only; never exposed as an API."""
        processes = self._spawned.get(account_id)
        if processes is None:
            raise LocalRuntimeConflict("This manager did not create that runtime; refusing to terminate it")
        for process in reversed(processes):
            if process.poll() is not None:
                continue
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW, check=False)
            else:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for process in processes:
            try:
                await asyncio.to_thread(process.wait, timeout=5)
            except subprocess.TimeoutExpired:
                raise LocalRuntimeConflict("Test worker did not stop; exact cleanup remains required")
        self._spawned.pop(account_id, None)
        self._runtimes.pop(account_id, None)
        record = self._read_record(account_id)
        if record:
            record.update(state="stopped", web_pid=None, adk_pid=None,
                          web_launcher_pid=None, adk_launcher_pid=None, stopped_at=time.time())
            self._write_record(account_id, record)


RuntimeManager = LocalAccountRuntimeManager
