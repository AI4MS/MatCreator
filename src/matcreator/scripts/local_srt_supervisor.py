"""Trusted per-account SRT supervisor, launched by a root-managed systemd unit.

This program is outside the sandbox. It relays host loopback bytes over
bounded anonymous-pipe frames to the account's private Web server. The
sandbox never receives a socket belonging to the host IP network namespace.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time


class SrtSupervisorError(RuntimeError):
    pass


def _trusted(path: Path, *, secret: bool = False) -> None:
    """Check administrator ownership without trusting an account-writable cwd."""
    absolute = path.absolute()
    resolved = absolute.resolve(strict=True)
    for part in set((absolute, *absolute.parents, resolved, *resolved.parents)):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode):
            if info.st_uid != 0:
                raise SrtSupervisorError("Shared installation symlink is not administrator-owned")
            continue
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise SrtSupervisorError("Shared installation and control paths must be administrator-owned and immutable")
    info = absolute.stat()
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise SrtSupervisorError("Shared/control paths must be regular files or directories")
    if secret and (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o007):
        raise SrtSupervisorError("The account specification must not be accessible to other users")


def _private_directory(path: Path) -> None:
    if path.is_symlink():
        raise SrtSupervisorError("Personal runtime directories cannot be symbolic links")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise SrtSupervisorError("Personal runtime ownership differs from its system account")
    path.chmod(0o700)


def _read_json(path: Path, limit: int = 1024 * 1024) -> dict:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise SrtSupervisorError("Runtime control document is too large")
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, UnicodeError) as exc:
        raise SrtSupervisorError("Runtime control document is invalid") from exc


def _manifest(spec: dict) -> None:
    root = Path(spec["runtime_root"])
    path = root / "manifest.json"
    _trusted(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if not hmac.compare_digest(digest, spec.get("runtime_sha256", "")):
        raise SrtSupervisorError("Shared runtime manifest differs from the provisioned version")
    manifest = _read_json(path, 16 * 1024 * 1024)
    files = manifest.get("files")
    if manifest.get("version") != 1 or not isinstance(files, dict) or not files:
        raise SrtSupervisorError("Shared runtime manifest format is invalid")
    for relative, expected in files.items():
        candidate = Path(relative)
        if (candidate.is_absolute() or ".." in candidate.parts or not isinstance(expected, str)
                or not re.fullmatch(r"[a-f0-9]{64}", expected)):
            raise SrtSupervisorError("Shared runtime manifest contains an invalid entry")
        source = root / candidate
        _trusted(source)
        if not hmac.compare_digest(hashlib.sha256(source.read_bytes()).hexdigest(), expected):
            raise SrtSupervisorError("A shared runtime source file differs from its immutable manifest")


def load_spec(path: Path) -> tuple[dict, dict]:
    if os.name != "posix" or os.geteuid() == 0:
        raise SrtSupervisorError("SRT supervisors must run as an unprivileged Linux service account")
    _trusted(path, secret=True)
    spec = _read_json(path)
    if spec.get("version") != 1 or spec.get("uid") != os.geteuid() or spec.get("gid") != os.getegid():
        raise SrtSupervisorError("Provisioned runtime identity does not match the systemd account")
    for key in ("installation_id", "instance_id"):
        if not re.fullmatch(r"[a-f0-9]{32}", str(spec.get(key, ""))):
            raise SrtSupervisorError("Provisioned runtime identity is invalid")
    token = spec.get("access_token")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", token):
        raise SrtSupervisorError("Provisioned runtime credential is invalid")
    if spec.get("ingress_kind") != "stdio-mux-v1":
        raise SrtSupervisorError("Unsupported account ingress protocol")
    source = Path(spec.get("source_root", ""))
    if not source.is_absolute():
        raise SrtSupervisorError("Shared application path must be absolute")
    for key in ("runtime_root", "source_root", "python", "node", "srt_cli", "bwrap_wrapper_dir", "policy_path"):
        candidate = Path(spec.get(key, ""))
        if not candidate.is_absolute():
            raise SrtSupervisorError("Shared runtime paths must be absolute")
        _trusted(candidate)
    _manifest(spec)
    # -I starts with no account-controlled import paths. Bootstrap only from
    # this root-owned installation after every source hash was verified.
    for item in (str(source), str(source / "src")):
        if item not in sys.path:
            sys.path.insert(0, item)
    from matcreator.local_srt_policy import build_policy, policy_digest
    expected = build_policy(spec)
    policy = _read_json(Path(spec["policy_path"]))
    if (policy != expected or not hmac.compare_digest(policy_digest(policy), spec.get("policy_sha256", ""))):
        raise SrtSupervisorError("The administrator's immutable SRT policy differs from its account specification")
    for key in ("home", "state_dir"):
        candidate = Path(spec[key])
        _private_directory(candidate)
    for relative in ("workspace", "tmp", ".cache"):
        _private_directory(Path(spec["home"]) / relative)
    return spec, policy


def clean_environment(spec: dict) -> dict[str, str]:
    home, source = PurePosixPath(spec["home"]), PurePosixPath(spec["source_root"])
    return {
        "PATH": ":".join((str(PurePosixPath(spec["bwrap_wrapper_dir"])),
                           str(PurePosixPath(spec["node"]).parent),
                           str(PurePosixPath(spec["python"]).parent), "/usr/local/bin", "/usr/bin", "/bin")),
        "HOME": str(home), "TMPDIR": str(home / "tmp"), "CLAUDE_CODE_TMPDIR": str(home / "tmp"),
        "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": str(source / "src") + ":" + str(source),
        "MATCREATOR_MODE": "local", "MATCREATOR_LOCAL_RUNTIME": "process",
        "MATCREATOR_LOCAL_PROFILE_WORKER": "1", "MATCREATOR_LOCAL_SRT_WORKER": "1",
        "MATCREATOR_LOCAL_ACCOUNT_ID": spec["account_id"], "MATCREATOR_LOCAL_ACCOUNT_ROOT": "/mc-broker-boundary",
        "MATCREATOR_HOME": str(home), "MATCREATOR_CONFIG_PATH": str(home / "config.yaml"),
        "MATCLAW_WORKSPACE": str(home / "workspace"), "XDG_CACHE_HOME": str(home / ".cache"),
        "MPLCONFIGDIR": str(home / ".cache" / "matplotlib"),
        "MATCREATOR_ADK_HOST": "127.0.0.1", "MATCREATOR_ADK_PORT": "8000", "ADK_LOCAL_PORT": "8000",
        "MATCREATOR_WEB_HOST": "127.0.0.1", "MATCREATOR_WEB_PORT": "8001", "MATCREATOR_FRONTEND_PORT": "8001",
    }


def _starttime(pid: int) -> str:
    value = Path(f"/proc/{pid}/stat").read_text()
    return value[value.rfind(")") + 2:].split()[19]


def _atomic_state(path: Path, value: dict) -> None:
    if path.is_symlink():
        raise SrtSupervisorError("Runtime state cannot be a symbolic link")
    descriptor, temporary_name = tempfile.mkstemp(prefix="state-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class HostIngress:
    """Host listener; only pipe frames cross into the sandbox."""
    def __init__(self):
        self.channel = None
        self.stop_event = threading.Event()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(64)
        self.listener.settimeout(0.5)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self._accept, name="mc-srt-ingress", daemon=True)

    def start(self, channel):
        self.channel = channel
        channel.start()
        self.thread.start()

    def _accept(self):
        while not self.stop_event.is_set() and not self.channel.stop_event.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                self.channel.open(connection)
            except Exception:
                connection.close()
                self.channel.fail("Host ingress could not open an account flow")
                return

    def close(self):
        self.stop_event.set()
        self.listener.close()
        if self.channel is not None:
            self.channel.close()
        if self.thread.ident is not None:
            self.thread.join(2)


def _log_output(stream, logger, token: str):
    secret = token.encode()
    pending = b""
    while True:
        chunk = stream.read1(8192)
        pending = (pending + chunk).replace(secret, b"[redacted]")
        hold = 0
        if chunk:
            for count in range(1, min(len(secret), len(pending) + 1)):
                if pending.endswith(secret[:count]):
                    hold = count
        emit = pending[:-hold] if hold else pending
        pending = pending[-hold:] if hold else b""
        if emit:
            logger.info(emit.decode("utf-8", errors="replace").rstrip())
        if not chunk:
            return


def _ready(port: int, spec: dict, audience: str) -> int | None:
    nonce = secrets.token_hex(16)
    endpoint = "/api/local-auth/status" if audience == "web" else "/api/local-auth/adk-status"
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=0.5)
    try:
        connection.request("GET", endpoint + "?nonce=" + nonce)
        response = connection.getresponse()
        value = json.loads(response.read(16384))
        if not isinstance(value, dict):
            return None
        pid = value.get("pid")
        if response.status != 200 or value.get("instance_id") != spec["instance_id"] or value.get("audience") != audience or not isinstance(pid, int) or pid <= 0:
            return None
        message = f"mc-local-server-v1|{spec['instance_id']}|{audience}|{pid}|{nonce}"
        proof = hmac.new(spec["access_token"].encode(), message.encode(), hashlib.sha256).hexdigest()
        return pid if hmac.compare_digest(proof, str(value.get("proof", ""))) else None
    except (OSError, ValueError, http.client.HTTPException):
        return None
    finally:
        connection.close()


def supervise(spec: dict) -> int:
    from matcreator.scripts.local_account_identity import prepare_identity
    from matcreator.scripts.local_srt_channel import MuxChannel
    home, state_dir = Path(spec["home"]), Path(spec["state_dir"])
    prepare_identity(home, account_id=spec["account_id"], instance_id=spec["instance_id"], access_token=spec["access_token"])
    relay = HostIngress()
    log_path = state_dir / "runtime.log"
    for item in (log_path, *(state_dir / f"runtime.log.{index}" for index in range(1, 4))):
        if os.path.lexists(item) and (item.is_symlink() or not item.is_file() or item.stat().st_uid != os.geteuid()):
            raise SrtSupervisorError("Private runtime log has an unexpected owner or type")
    logger = logging.getLogger("matcreator.srt." + spec["account_id"])
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    logger.addHandler(handler)
    stop = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGTERM, signal.SIGINT)}
    process = None
    state = {key: spec[key] for key in ("installation_id", "account_id", "instance_id", "home", "unit_name", "uid", "gid", "runtime_sha256", "policy_sha256")}
    state.update(version=1, status="starting", main_pid=os.getpid(), main_starttime=_starttime(os.getpid()),
                 boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(), web_port=relay.port,
                 ingress_kind="stdio-mux-v1", started_at=time.time())
    state_path = state_dir / "state.json"
    try:
        command = [spec["node"], spec["srt_cli"], "--settings", spec["policy_path"], "--", spec["python"],
                   "-m", "matcreator.scripts.local_srt_worker"]
        process = subprocess.Popen(command, cwd=spec["source_root"], env=clean_environment(spec), stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True, start_new_session=True)
        state.update(srt_pid=process.pid, srt_starttime=_starttime(process.pid))
        _atomic_state(state_path, state)
        output = threading.Thread(target=_log_output, args=(process.stderr, logger, spec["access_token"]), daemon=True)
        output.start()
        channel = MuxChannel(process.stdout, process.stdin)
        relay.start(channel)
        deadline = time.monotonic() + 150
        while not stop.wait(0.25):
            if channel.stop_event.is_set():
                state.update(status="failed", detail="The account ingress protocol ended")
                return 1
            if process.poll() is not None:
                state.update(status="failed", detail="The account sandbox exited", exit_code=process.returncode)
                return process.returncode if process.returncode and process.returncode > 0 else 1
            if state["status"] != "ready":
                web_pid, adk_pid = _ready(relay.port, spec, "web"), _ready(relay.port, spec, "adk")
                if web_pid and adk_pid:
                    state.update(status="ready", web_pid=web_pid, adk_pid=adk_pid, ready_at=time.time())
                    _atomic_state(state_path, state)
                elif time.monotonic() >= deadline:
                    state.update(status="failed", detail="Signed account service readiness was not confirmed")
                    return 1
        state.update(status="stopped", detail="The account service was explicitly stopped")
        return 0
    except Exception:
        state.update(status="failed", detail="The account service failed during startup or supervision")
        raise
    finally:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        relay.close()
        # Reader/writer daemon threads can be blocked in pipe IO. Do not close
        # their buffered objects concurrently (that can deadlock on a lock).
        # This standalone service exits immediately; the OS closes its FDs.
        state["stopped_at"] = time.time()
        _atomic_state(state_path, state)
        for sig, callback in previous.items():
            signal.signal(sig, callback)
        handler.close()
        logger.removeHandler(handler)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec", required=True)
    args = parser.parse_args()
    try:
        spec, _ = load_spec(Path(args.spec))
        return supervise(spec)
    except (SrtSupervisorError, ValueError, OSError, KeyError, TypeError) as exc:
        # Specs contain credentials: do not dump the input, environment or argv.
        print(f"SRT account supervisor refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
