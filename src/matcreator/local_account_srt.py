"""Authenticated full-account workers using Linux sandbox-runtime, without Docker.

The broker retains account credentials and ownership records. A narrow, trusted
Linux controller owns system accounts, sandbox policy, cgroups and systemd
units. Secrets cross that boundary on standard input, never command arguments.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import subprocess
import tempfile
import time
from typing import Any
from urllib.parse import urlsplit
import uuid

import httpx

from .local_access import _check_private_file, ensure_local_access, private_directory
from .local_account_runtime import LocalAccountRuntime, LocalAccountRuntimeManager, LocalRuntimeConflict, _exclusive_file


RUNTIME_ROOT = "/opt/matcreator-srt-runtime"
STATE_ROOT = "/var/lib/matcreator-srt"
CAPABILITIES = frozenset({"root", "systemd", "cgroup_v2", "bubblewrap", "srt", "immutable_runtime", "prebound_ingress"})
HASH = re.compile(r"[a-f0-9]{64}")
ERROR_CODES = frozenset({"configuration_conflict", "controller_failed", "identity_conflict", "invalid_request",
                         "launch_failed", "launch_uncertain", "limit_mismatch", "ownership_mismatch", "policy_failed",
                         "policy_mismatch", "prerequisite", "provision_failed", "runtime_changed", "service_failed",
                         "stop_unconfirmed", "unit_mismatch", "unit_unknown"})


@dataclass(frozen=True)
class SrtCredential:
    instance_id: str
    token: str = field(repr=False)

    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.token}

    def server_proof(self, nonce: str, audience: str, pid: int) -> str:
        message = f"mc-local-server-v1|{self.instance_id}|{audience}|{pid}|{nonce}"
        return hmac.new(self.token.encode(), message.encode(), hashlib.sha256).hexdigest()


class SrtController:
    """Fixed argument-array transport to the standalone root-owned controller."""

    def __init__(self, command: list[str] | tuple[str, ...] | None = None):
        if command is None:
            default = '["wsl.exe","-d","Ubuntu-24.04","--"]' if os.name == "nt" else '["python3"]'
            try:
                command = json.loads(os.environ.get("MATCREATOR_LOCAL_SRT_COMMAND", default))
            except ValueError:
                raise LocalRuntimeConflict("SRT command must be a JSON argument array") from None
        if (not isinstance(command, (list, tuple)) or not command
                or any(not isinstance(value, str) or not value or "\x00" in value for value in command)):
            raise LocalRuntimeConflict("SRT command must be a JSON argument array")
        executable = Path(command[0]).name.lower()
        if executable in {"wsl", "wsl.exe"}:
            if len(command) != 4 or command[1] not in {"-d", "--distribution"} or command[3] != "--":
                raise LocalRuntimeConflict("SRT WSL command must name one distribution directly, without a shell")
            self.is_wsl = True
        elif len(command) == 1 and re.fullmatch(r"python(?:3(?:\.\d+)?)?(?:\.exe)?", executable):
            if os.name == "nt":
                raise LocalRuntimeConflict("Native Windows SRT does not provide the required account read boundary; select a Linux/WSL controller")
            self.is_wsl = False
        else:
            raise LocalRuntimeConflict("SRT controller must invoke Linux Python directly or through one WSL distribution")
        self.command = tuple(command)

    @staticmethod
    def _environment() -> dict[str, str]:
        allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOME"}
        result = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        result["WSLENV"] = ""
        return result

    def _arguments(self, inline: dict | None = None) -> list[str]:
        # The controller belongs to the administrator-staged immutable runtime.
        # Encoding its source into argv exceeds Windows CreateProcess's 32 KiB
        # limit and would make the broker checkout an executable authority.
        helper = RUNTIME_ROOT + "/app/src/matcreator/scripts/local_srt_host.py"
        prefix = [*self.command, "python3"] if self.is_wsl else list(self.command)
        if inline is None:
            return [*prefix, "-I", helper]
        if inline is not None:
            if inline.get("action") not in {"hold", "status_hold"} or set(inline) - {"version", "action", "installation_id", "runtime_root", "state_root"}:
                raise LocalRuntimeConflict("Only non-sensitive SRT hold identity may be passed to a launcher")
            payload = base64.b64encode(json.dumps(inline).encode()).decode("ascii")
            bootstrap = ("import base64,sys,io,runpy;sys.stdin=io.StringIO(base64.b64decode('" + payload
                         + "').decode());runpy.run_path(" + repr(helper) + ",run_name='__main__')")
        return [*prefix, "-I", "-c", bootstrap]

    def request(self, payload: dict, *, timeout: float = 60) -> dict:
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        try:
            result = subprocess.run(self._arguments(), input=json.dumps(payload), capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=timeout, check=False,
                                    env=self._environment(), **options)
            response = json.loads(result.stdout)
            if result.returncode or not isinstance(response, dict) or response.get("version") != 1:
                raise ValueError
            if response.get("ok") is not True:
                code = response.get("code", "controller_error")
                suffix = code if isinstance(code, str) and code in ERROR_CODES else "controller_error"
                raise LocalRuntimeConflict("SRT controller rejected this operation (" + suffix + "); existing work was preserved")
            return response
        except (OSError, subprocess.TimeoutExpired, ValueError):
            raise LocalRuntimeConflict("The SRT controller is unavailable or returned unverified state; no process fallback was started") from None

    def ensure_hold(self, installation_id: str) -> dict | None:
        if not self.is_wsl or os.name != "nt":
            return None
        query = {"version": 1, "action": "status_hold", "installation_id": installation_id,
                 "runtime_root": RUNTIME_ROOT, "state_root": STATE_ROOT}
        def verified():
            value = self.request(query, timeout=20)
            if value.get("installation_id") != installation_id or not isinstance(value.get("active"), bool):
                raise LocalRuntimeConflict("SRT WSL hold does not belong to this installation")
            if value["active"]:
                try:
                    if (not isinstance(value.get("pid"), int) or value["pid"] <= 0
                            or str(uuid.UUID(value.get("boot_id", ""))) != value["boot_id"]):
                        raise ValueError
                except (ValueError, TypeError, AttributeError, KeyError):
                    raise LocalRuntimeConflict("SRT WSL hold process identity is unverified") from None
            return value
        status = verified()
        if status.get("active") is True:
            return status
        arguments = self._arguments({**query, "action": "hold"})
        payload = base64.b64encode(json.dumps({"executable": arguments[0],
                                              "arguments": subprocess.list2cmdline(arguments[1:])}).encode()).decode("ascii")
        program = ("$ErrorActionPreference='Stop'; $k=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"
                   + payload + "')) | ConvertFrom-Json; Start-Process -FilePath $k.executable -ArgumentList $k.arguments -WindowStyle Hidden")
        shell = str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe")
        try:
            launched = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", program], capture_output=True,
                                      text=True, encoding="utf-8", errors="replace", timeout=20, check=False, env=self._environment(),
                                      creationflags=subprocess.CREATE_NO_WINDOW)
            if launched.returncode:
                raise OSError
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                status = verified()
                if status.get("active") is True:
                    return status
                time.sleep(0.2)
        except (OSError, subprocess.TimeoutExpired):
            raise LocalRuntimeConflict("Independent SRT WSL hold could not be started; existing workers were retained") from None
        raise LocalRuntimeConflict("SRT WSL hold readiness is unconfirmed; no worker replacement was started")


def _cpuset(value: str, *, empty=False) -> tuple[str, frozenset[int]]:
    if not isinstance(value, str) or len(value) > 4096:
        raise LocalRuntimeConflict("SRT CPU affinity must be a Linux CPU range string")
    if not value.strip() and empty:
        return "", frozenset()
    members = set()
    for part in value.split(","):
        found = re.fullmatch(r"\s*([0-9]{1,5})(?:-([0-9]{1,5}))?\s*", part)
        if not found:
            raise LocalRuntimeConflict("SRT CPU affinity requires nonnegative CPU numbers or ascending ranges")
        first, last = int(found[1]), int(found[2] or found[1])
        if not first <= last <= 65535:
            raise LocalRuntimeConflict("SRT CPU affinity is outside the supported range")
        members.update(range(first, last + 1))
    ranges = []
    ordered = sorted(members)
    for number in ordered:
        if ranges and ranges[-1][-1] + 1 == number:
            ranges[-1].append(number)
        else:
            ranges.append([number])
    return ",".join(str(row[0]) if len(row) == 1 else f"{row[0]}-{row[-1]}" for row in ranges), frozenset(members)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate configuration key")
        result[key] = value
    return result


def _cpu_map(raw: str) -> dict[str, str]:
    try:
        values = json.loads(raw, object_pairs_hook=_unique_object) if raw.strip() else {}
        if not isinstance(values, dict):
            raise ValueError
        result, assigned = {}, set()
        for key, value in values.items():
            if str(uuid.UUID(key)) != key:
                raise ValueError
            normalized, members = _cpuset(value)
            if assigned.intersection(members):
                raise ValueError
            result[key] = normalized
            assigned.update(members)
        return result
    except (ValueError, TypeError, AttributeError):
        raise LocalRuntimeConflict("SRT CPU allocations require canonical account UUIDs and non-overlapping CPU sets") from None


def _domains(name: str) -> list[str]:
    try:
        values = json.loads(os.environ.get(name, "[]"))
        if (not isinstance(values, list) or len(values) > 256
                or any(not isinstance(value, str) or not value or len(value) > 512
                       or any(char.isspace() or ord(char) < 32 for char in value) for value in values)):
            raise ValueError
        normalized = set()
        for value in values:
            match = re.fullmatch(r"((?:\*\.)?[A-Za-z0-9](?:[A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?):([0-9]{1,5})", value)
            if (not match or not 1 <= int(match[2]) <= 65535 or ".." in match[1]
                    or any(not label or len(label) > 63 for label in match[1].removeprefix("*.").split("."))):
                raise ValueError
            normalized.add(match[1].lower() + ":" + str(int(match[2])))
        return sorted(normalized)
    except ValueError:
        raise LocalRuntimeConflict("SRT network domains require an explicit JSON array of hostname:port pairs") from None


class SrtAccountRuntimeManager(LocalAccountRuntimeManager):
    runtime_backend = "srt"
    execution_isolation = "srt_linux_namespaces"
    supports_legacy = False

    def __init__(self, home: Path, source_root: Path, *, controller: SrtController | None = None,
                 startup_timeout: float = 180.0):
        super().__init__(home, source_root, startup_timeout=startup_timeout)
        self.controller = controller or SrtController()
        self.installation_id = ensure_local_access(self.home).instance_id
        self.runtime_root = os.environ.get("MATCREATOR_LOCAL_SRT_RUNTIME_ROOT", RUNTIME_ROOT)
        self.state_root = os.environ.get("MATCREATOR_LOCAL_SRT_STATE_ROOT", STATE_ROOT)
        if self.runtime_root != RUNTIME_ROOT or self.state_root != STATE_ROOT:
            raise LocalRuntimeConflict("SRT execution requires the dedicated immutable runtime and private state roots")
        secret_root = self.home / ".local-accounts" / "srt-secrets"
        if secret_root.is_symlink():
            raise LocalRuntimeConflict("SRT credential storage cannot be redirected")
        self.secret_directory = private_directory(secret_root)
        try:
            self.memory_mb = int(os.environ.get("MATCREATOR_LOCAL_SRT_MEMORY_MB", "1536"))
            self.cpus = float(os.environ.get("MATCREATOR_LOCAL_SRT_CPUS", "1"))
            self.pids = int(os.environ.get("MATCREATOR_LOCAL_SRT_PIDS", "256"))
            if (not 256 <= self.memory_mb <= 65536 or not math.isfinite(self.cpus)
                    or not 0.1 <= self.cpus <= 256 or not 64 <= self.pids <= 4096):
                raise ValueError
        except ValueError:
            raise LocalRuntimeConflict("SRT memory, CPU and process limits must be positive bounded values") from None
        self.cpu_sets = _cpu_map(os.environ.get("MATCREATOR_LOCAL_SRT_CPUSETS", ""))
        self.network = {"allowed_domains": _domains("MATCREATOR_LOCAL_SRT_ALLOWED_DOMAINS"),
                        "denied_domains": _domains("MATCREATOR_LOCAL_SRT_DENIED_DOMAINS")}
        parent_proxy = os.environ.get("MATCREATOR_LOCAL_SRT_PARENT_PROXY", "")
        if parent_proxy:
            try:
                parsed = urlsplit(parent_proxy)
                if (len(parent_proxy) > 2048 or any(ord(char) < 32 for char in parent_proxy)
                        or parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.port
                        or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
                    raise ValueError
            except ValueError:
                raise LocalRuntimeConflict("Invalid SRT parent proxy configuration") from None
            self.network["parent_proxy"] = parent_proxy
        self._preflight_state = None
        self._owned: dict[str, dict] = {}

    def _request(self, payload: dict, *, timeout=60) -> dict:
        response = self.controller.request(payload, timeout=timeout)
        if not isinstance(response, dict) or response.get("version") != 1 or response.get("ok") is not True:
            raise LocalRuntimeConflict("SRT returned unverified controller state")
        return response

    def _base(self, action: str) -> dict:
        return {"version": 1, "action": action, "installation_id": self.installation_id,
                "runtime_root": self.runtime_root, "state_root": self.state_root}

    def preflight(self) -> dict:
        value = self._request(self._base("preflight"), timeout=30)
        caps = value.get("capabilities") or {}
        if (value.get("backend") != "srt" or value.get("runtime_root") != self.runtime_root
                or value.get("state_root") != self.state_root
                or not HASH.fullmatch(str(value.get("source_hash", "")))
                or not HASH.fullmatch(str(value.get("runtime_sha256", "")))
                or any(caps.get(name) is not True for name in CAPABILITIES)):
            raise LocalRuntimeConflict("SRT preflight did not prove the required process, filesystem, network and resource boundaries")
        self._preflight_state = value
        return {key: value[key] for key in ("backend", "runtime_root", "state_root", "source_hash", "runtime_sha256", "capabilities")}

    def _credential(self, account_id: str, *, create: bool) -> SrtCredential:
        path = self.secret_directory / (account_id + ".json")
        if not os.path.lexists(path):
            if not create:
                raise LocalRuntimeConflict("The existing SRT worker credential is missing; restore it without replacing the worker")
            value = {"version": 1, "installation_id": self.installation_id, "account_id": account_id,
                     "instance_id": secrets.token_hex(16), "access_token": secrets.token_urlsafe(32)}
            descriptor, filename = tempfile.mkstemp(prefix=account_id + "-", dir=self.secret_directory)
            temporary = Path(filename)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(value, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    pass
            finally:
                temporary.unlink(missing_ok=True)
        _check_private_file(path)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if (value.get("version") != 1 or value.get("installation_id") != self.installation_id
                    or value.get("account_id") != account_id
                    or not re.fullmatch(r"[a-f0-9]{32}", value.get("instance_id", ""))
                    or not re.fullmatch(r"[A-Za-z0-9_-]{43}", value.get("access_token", ""))):
                raise ValueError
            return SrtCredential(value["instance_id"], value["access_token"])
        except (ValueError, TypeError, AttributeError):
            raise LocalRuntimeConflict("The SRT account credential cannot be verified; its existing state was preserved") from None

    def _spec(self, account_id: str, access: SrtCredential, action: str) -> dict:
        if self.cpu_sets and account_id not in self.cpu_sets:
            raise LocalRuntimeConflict("This SRT account has no administrator CPU allocation")
        if account_id in self.cpu_sets and self.cpus > len(_cpuset(self.cpu_sets[account_id])[1]):
            raise LocalRuntimeConflict("SRT CPU quota exceeds this account's administrator CPU allocation")
        return {**self._base(action), "account_id": account_id, "instance_id": access.instance_id,
                "access_token_sha256": hashlib.sha256(access.token.encode()).hexdigest(),
                "source_hash": self._preflight_state["source_hash"], "runtime_sha256": self._preflight_state["runtime_sha256"],
                "limits": {"memory_mb": self.memory_mb, "cpus": self.cpus, "pids": self.pids,
                           "cpuset_cpus": self.cpu_sets.get(account_id, "")}, "network": self.network}

    @staticmethod
    def _config_hash(spec: dict) -> str:
        return hashlib.sha256(json.dumps({key: value for key, value in spec.items() if key != "action"},
                                         sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def _validated_state(self, value: dict, account_id: str, access: SrtCredential, record: dict) -> dict:
        account_root = PurePosixPath(self.state_root) / self.installation_id / account_id
        if (value.get("backend") != "srt" or value.get("installation_id") != self.installation_id
                or value.get("account_id") != account_id or value.get("instance_id") != access.instance_id
                or value.get("state") not in {"starting", "ready"}
                or value.get("ingress_kind") != "stdio-mux-v1"
                or value.get("profile_home") != str(account_root / "home")
                or value.get("control_dir") != str(account_root / "control")
                or not re.fullmatch(r"[A-Za-z0-9_.@:-]{1,200}\.service", str(value.get("unit_name", "")))
                or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", str(value.get("linux_user", "")))
                or isinstance(value.get("web_port"), bool) or not isinstance(value.get("web_port"), int)
                or not (1 <= value["web_port"] <= 65535 or value["web_port"] == 0 and value.get("state") == "starting")
                or not isinstance(value.get("main_pid"), int) or value["main_pid"] <= 0
                or not str(value.get("main_starttime", "")).isdigit()
                or value.get("source_hash") != self._preflight_state["source_hash"]
                or value.get("runtime_sha256") != self._preflight_state["runtime_sha256"]
                or not HASH.fullmatch(str(value.get("policy_hash", "")))
                or not HASH.fullmatch(str(value.get("spec_hash", "")))):
            raise LocalRuntimeConflict("SRT runtime identity, policy or endpoint is unverified; no credential was forwarded")
        try:
            if str(uuid.UUID(value.get("boot_id", ""))) != value["boot_id"]:
                raise ValueError
        except (ValueError, TypeError, AttributeError, KeyError):
            raise LocalRuntimeConflict("SRT runtime boot identity is invalid") from None
        pinned = ("unit_name", "linux_user", "main_pid", "main_starttime", "boot_id", "web_port", "policy_hash", "spec_hash", "unit_invocation_id", "ingress_kind")
        receipt = value.get("ownership_receipt") if isinstance(value.get("ownership_receipt"), dict) else {}
        if any(key in record and not (key == "web_port" and record[key] == 0)
               and str(record[key]) != str(value.get(key, receipt.get(key))) for key in pinned):
            raise LocalRuntimeConflict("An existing SRT runtime identity changed; it was retained without replacement")
        if receipt:
            self._ownership(value, account_id)
        return value

    def _ownership(self, value: dict, account_id: str) -> dict:
        receipt = value.get("ownership_receipt")
        if not isinstance(receipt, dict) or any(key in receipt for key in ("access_token", "token", "network")):
            raise LocalRuntimeConflict("SRT test ownership receipt is invalid")
        if (receipt.get("version") != 1 or receipt.get("installation_id") != self.installation_id
                or receipt.get("account_id") != account_id
                or any(str(receipt.get(key)) != str(value.get(key)) for key in
                       ("instance_id", "unit_name", "linux_user", "profile_home", "main_pid", "main_starttime", "boot_id",
                        "source_hash", "runtime_sha256", "policy_hash", "spec_hash"))
                or any(not isinstance(receipt.get(key), int) or receipt[key] <= 0 for key in ("uid", "gid"))
                or not isinstance(receipt.get("gecos"), str) or not 1 <= len(receipt["gecos"]) <= 512
                or any(ord(char) < 32 for char in receipt["gecos"])
                or not re.fullmatch(r"[a-f0-9]{32}", str(receipt.get("unit_invocation_id", "")))
                or not HASH.fullmatch(str(receipt.get("ownership_id", "")))):
            raise LocalRuntimeConflict("SRT test ownership receipt does not match its verified runtime")
        return dict(receipt)

    def _apply_state(self, account_id: str, record: dict, value: dict) -> None:
        for key in ("unit_name", "linux_user", "uid", "gid", "gecos", "control_dir", "web_port", "main_pid",
                    "main_starttime", "boot_id", "source_hash", "runtime_sha256", "policy_hash", "spec_hash", "unit_invocation_id", "ingress_kind"):
            if key in value:
                record[key] = value[key]
            elif isinstance(value.get("ownership_receipt"), dict) and key in value["ownership_receipt"]:
                record[key] = value["ownership_receipt"][key]
        record["linux_profile_home"] = value["profile_home"]
        record["state"] = value["state"]
        self._write_record(account_id, record)

    async def _probe(self, client: httpx.AsyncClient, runtime: LocalAccountRuntime, role: str):
        nonce = secrets.token_hex(16)
        path = "/api/local-auth/status" if role == "web" else "/api/local-auth/adk-status"
        try:
            response = await client.get(runtime.web_url + path, params={"nonce": nonce}, timeout=3)
        except httpx.TransportError:
            return "absent", None
        try:
            value = response.json()
            pid = value.get("pid")
            if (response.status_code != 200 or value.get("instance_id") != runtime.access.instance_id
                    or value.get("audience") != role or not isinstance(pid, int) or pid <= 0
                    or not secrets.compare_digest(str(value.get("proof", "")), runtime.access.server_proof(nonce, role, pid))):
                raise ValueError
            return "ready", pid
        except (ValueError, TypeError, AttributeError):
            raise LocalRuntimeConflict("SRT worker role identity or signature does not match; no credential was forwarded") from None

    async def ensure(self, account: dict) -> LocalAccountRuntime:
        account_id, home, workspace = self._paths(account)
        if account.get("legacy"):
            raise LocalRuntimeConflict("Existing installation data requires an offline migration before SRT execution; no data was moved")
        async with self._locks.setdefault(account_id, asyncio.Lock()):
            cached = self._runtimes.get(account_id)
            if cached and time.monotonic() - cached.verified_at < 1:
                return cached
            with _exclusive_file(self.record_directory / (account_id + ".lock")):
                record = self._read_record(account_id)
                if record and record.get("backend") != "srt":
                    raise LocalRuntimeConflict("This account already uses another runtime backend; an offline migration is required")
                if record is None and home.exists() and any(home.iterdir()):
                    raise LocalRuntimeConflict("An existing nonempty profile requires an offline migration before SRT execution")
                if self._preflight_state is None:
                    await asyncio.to_thread(self.preflight)
                access = self._credential(account_id, create=record is None)
                spec = self._spec(account_id, access, "status")
                config_hash = self._config_hash(spec)
                if record and (record.get("home") != str(home) or record.get("workspace") != str(workspace)
                               or record.get("instance_id") != access.instance_id or record.get("config_hash") != config_hash):
                    raise LocalRuntimeConflict("SRT account configuration or credential identity changed; no replacement was started")
                state = await asyncio.to_thread(self._request, spec)
                if record is None:
                    if state.get("state") != "absent":
                        raise LocalRuntimeConflict("An unrecorded SRT runtime already exists; it was not adopted or replaced")
                    record = {"version": 1, "backend": "srt", "account_id": account_id, "installation_id": self.installation_id,
                              "instance_id": access.instance_id, "home": str(home), "workspace": str(workspace),
                              "source_root": str(self.source_root), "config_hash": config_hash, "state": "provisioning",
                              "source_hash": spec["source_hash"], "runtime_sha256": spec["runtime_sha256"], "created_at": time.time()}
                    self._write_record(account_id, record)
                try:
                    if state.get("state") == "absent":
                        if record.get("unit_name") or record.get("state") not in {"provisioning", "attention"}:
                            raise LocalRuntimeConflict("An existing SRT runtime is missing; no empty replacement was started")
                        state = await asyncio.to_thread(self._request, {**spec, "action": "ensure", "access_token": access.token}, timeout=self.startup_timeout)
                    state = self._validated_state(state, account_id, access, record)
                    if state.get("created") is True:
                        self._owned[account_id] = self._ownership(state, account_id)
                    self._apply_state(account_id, record, state)
                    if hasattr(self.controller, "ensure_hold"):
                        hold = await asyncio.to_thread(self.controller.ensure_hold, self.installation_id)
                        if hold:
                            record["wsl_hold"] = {key: hold.get(key) for key in ("active", "pid", "boot_id", "installation_id")}
                            self._write_record(account_id, record)
                    deadline = time.monotonic() + self.startup_timeout
                    while True:
                        port = state["web_port"]
                        if port > 0:
                            runtime = LocalAccountRuntime(account_id, home, workspace, f"http://127.0.0.1:{port}",
                                                          f"http://127.0.0.1:{port}", port, 8000, access)
                            web, adk = await self._ready(runtime)
                            if web and adk:
                                record.update(state="ready", web_pid=runtime.web_pid, adk_pid=runtime.adk_pid,
                                              ready_at=record.get("ready_at", time.time()))
                                self._write_record(account_id, record)
                                self._runtimes[account_id] = runtime
                                return runtime
                        if time.monotonic() >= deadline:
                            raise LocalRuntimeConflict("SRT readiness is unconfirmed; no process fallback or replacement was started")
                        await asyncio.sleep(0.25)
                        state = self._validated_state(await asyncio.to_thread(self._request, spec), account_id, access, record)
                        self._apply_state(account_id, record, state)
                except LocalRuntimeConflict:
                    record["state"] = "attention"
                    self._write_record(account_id, record)
                    raise

    def report_status(self) -> list[dict[str, Any]]:
        results = super().report_status()
        for value in results:
            record = self._read_record(value["account_id"])
            if record and record.get("backend") == "srt":
                value.update({key: record.get(key) for key in ("backend", "linux_profile_home", "unit_name", "linux_user",
                                                              "main_pid", "main_starttime", "boot_id", "source_hash", "policy_hash")})
        return results

    async def stop_test_owned(self, account_id: str) -> None:
        receipt = self._owned.get(account_id)
        if receipt is None:
            raise LocalRuntimeConflict("This manager did not create that SRT runtime; refusing to stop it")
        record = self._read_record(account_id)
        if not record or record.get("backend") != "srt":
            raise LocalRuntimeConflict("SRT test ownership record is missing or belongs to another backend")
        for key in ("unit_name", "linux_user", "main_pid", "main_starttime", "boot_id", "source_hash", "policy_hash"):
            if key in receipt and str(receipt[key]) != str(record.get(key)):
                raise LocalRuntimeConflict("SRT test ownership changed; cleanup refused")
        access = self._credential(account_id, create=False)
        result = await asyncio.to_thread(self._request, {**self._spec(account_id, access, "stop_test_owned"),
                                                        "ownership_receipt": receipt}, timeout=45)
        if result.get("state") != "stopped" or result.get("cleanup_confirmed") is not True:
            raise LocalRuntimeConflict("SRT test runtime stop was not confirmed")
        status = await asyncio.to_thread(self._request, self._spec(account_id, access, "status"))
        if status.get("state") not in {"stopped", "absent"}:
            raise LocalRuntimeConflict("SRT test runtime remains active after cleanup")
        record.update(state="stopped", stopped_at=time.time(), data_retained=True)
        self._write_record(account_id, record)
        self._owned.pop(account_id, None)
        self._runtimes.pop(account_id, None)
