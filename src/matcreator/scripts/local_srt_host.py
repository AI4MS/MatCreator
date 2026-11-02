"""Root-owned Linux control plane for account-scoped SRT services.

This file intentionally uses only the standard library.  The administrator
stages it in the immutable shared runtime; the Windows broker invokes that
fixed path with ``wsl ... python3 -I`` without importing the MatCreator package.
Only the trusted controller provisions users and systemd units; account code
runs as a distinct non-root UID inside SRT.  Requests and replies are one JSON
object, and secrets are never command-line arguments or error messages.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlsplit
import uuid


VERSION = 1
RUNTIME_ROOT = "/opt/matcreator-srt-runtime"
STATE_ROOT = "/var/lib/matcreator-srt"
TOOLS_ROOT = "/opt/matcreator-srt-tools"
NODE = TOOLS_ROOT + "/node-v24.19.0-linux-x64/bin/node"
SRT_CLI = TOOLS_ROOT + "/srt-0.0.81/node_modules/@anthropic-ai/sandbox-runtime/dist/cli.js"
SAFE_ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "LANG": "C.UTF-8", "HOME": "/root", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1"}
HEX32 = re.compile(r"[a-f0-9]{32}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
TOKEN = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")
MAX_JSON = 262144


class Refusal(RuntimeError):
    """A deliberately non-sensitive failure safe to return to the broker."""

    def __init__(self, code: str, detail: str = "The requested runtime could not be verified"):
        super().__init__(detail)
        self.code = code
        self.detail = detail


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            hasher.update(block)
    return hasher.hexdigest()


def _hex(value: Any, expression=HEX32) -> str:
    if not isinstance(value, str) or not expression.fullmatch(value):
        raise Refusal("invalid_request", "A canonical identity or digest is required")
    return value


def account_identity(request: dict) -> dict:
    installation = _hex(request.get("installation_id"))
    account = request.get("account_id")
    try:
        if not isinstance(account, str) or str(uuid.UUID(account)) != account:
            raise ValueError
    except (ValueError, AttributeError):
        raise Refusal("invalid_request", "A canonical account UUID is required") from None
    instance = _hex(request.get("instance_id"))
    name = "mc-" + installation[:8] + "-" + account.replace("-", "")[:12]
    return {"installation_id": installation, "account_id": account, "instance_id": instance,
            "linux_user": name, "unit_name": "mc-srt-" + installation + "-" + account + ".service",
            "gecos": "MatCreator SRT " + installation + " " + account}


def parse_cpu_set(value: str) -> set[int]:
    if not isinstance(value, str) or len(value) > 4096:
        raise Refusal("invalid_request", "The CPU selection is invalid")
    result: set[int] = set()
    if not value:
        return result
    for part in value.split(","):
        if not re.fullmatch(r"\d+(?:-\d+)?", part):
            raise Refusal("invalid_request", "The CPU selection is invalid")
        bounds = [int(piece) for piece in part.split("-")]
        first, last = bounds[0], bounds[-1]
        if first > last or last > 1048575 or last - first > 65536:
            raise Refusal("invalid_request", "The CPU selection is invalid")
        result.update(range(first, last + 1))
    return result


def format_cpu_set(values: set[int]) -> str:
    # A comma-separated list also works with systemd AllowedCPUs and is canonical.
    return ",".join(str(number) for number in sorted(values))


def limits_for(request: dict, available: set[int] | None = None) -> dict:
    value = request.get("limits", {})
    if not isinstance(value, dict) or set(value) - {"memory_mb", "cpus", "pids", "cpuset_cpus"}:
        raise Refusal("invalid_request", "Resource limits are invalid")
    memory, pids, cpus = value.get("memory_mb", 1536), value.get("pids", 256), value.get("cpus", 1)
    if (isinstance(memory, bool) or not isinstance(memory, int) or not 256 <= memory <= 65536
            or isinstance(pids, bool) or not isinstance(pids, int) or not 64 <= pids <= 4096
            or isinstance(cpus, bool) or not isinstance(cpus, (int, float))
            or not math.isfinite(cpus) or not 0.1 <= cpus <= 256):
        raise Refusal("invalid_request", "Resource limits are outside the supported range")
    selected = parse_cpu_set(value.get("cpuset_cpus", ""))
    if available is not None and (not selected.issubset(available) or cpus > len(selected or available)):
        raise Refusal("invalid_request", "Requested CPUs are unavailable")
    return {"memory_mb": memory, "pids": pids, "cpus": float(cpus),
            "cpuset_cpus": format_cpu_set(selected)}


def network_for(request: dict) -> dict:
    value = request.get("network", {})
    if not isinstance(value, dict) or set(value) - {"allowed_domains", "denied_domains", "parent_proxy"}:
        raise Refusal("invalid_request", "Network policy is invalid")
    result: dict[str, Any] = {}
    for key in ("allowed_domains", "denied_domains"):
        domains = value.get(key, [])
        if not isinstance(domains, list) or len(domains) > 256:
            raise Refusal("invalid_request", "Network domains are invalid")
        canonical = set()
        for domain in domains:
            match = re.fullmatch(r"((?:\*\.)?[A-Za-z0-9](?:[A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?):([0-9]{1,5})",
                                 domain) if isinstance(domain, str) else None
            if (not match or not 1 <= int(match[2]) <= 65535 or ".." in match[1]
                    or any(not label or len(label) > 63 for label in match[1].removeprefix("*.").split("."))):
                raise Refusal("invalid_request", "Network grants require a hostname and exact port")
            canonical.add(match[1].lower() + ":" + str(int(match[2])))
        result[key] = sorted(canonical)
    proxy = value.get("parent_proxy", "")
    if not isinstance(proxy, str) or len(proxy) > 2048 or any(ord(c) < 32 for c in proxy):
        raise Refusal("invalid_request", "The administrator proxy is invalid")
    if proxy:
        try:
            parsed = urlsplit(proxy)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.port:
                raise ValueError
            if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise ValueError
        except ValueError:
            raise Refusal("invalid_request", "The administrator proxy is invalid") from None
    result["parent_proxy"] = proxy
    return result


def proc_starttime(pid: int, proc_root: Path = Path("/proc")) -> str:
    try:
        value = (proc_root / str(pid) / "stat").read_text(encoding="ascii")
        # comm can contain spaces and parentheses; starttime is field 22.
        fields = value[value.rindex(")") + 2:].split()
        return str(int(fields[19]))
    except (OSError, ValueError, IndexError):
        raise Refusal("identity_conflict", "The service process identity is unavailable") from None


def boot_id() -> str:
    try:
        return str(uuid.UUID(Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()))
    except (OSError, ValueError):
        raise Refusal("prerequisite", "The Linux boot identity is unavailable") from None


def _read_json(path: Path, *, owner: int, mode: int | None = None) -> dict:
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != owner
                or (mode is not None and stat.S_IMODE(info.st_mode) != mode)
                or info.st_size > MAX_JSON):
            raise Refusal("identity_conflict", "A runtime control file has unsafe ownership")
        with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "r", encoding="utf-8") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_ino != info.st_ino or opened.st_dev != info.st_dev
                    or opened.st_uid != owner or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1):
                raise Refusal("identity_conflict", "A runtime control file changed while opening")
            value = json.load(stream)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (OSError, ValueError, TypeError):
        raise Refusal("identity_conflict", "A runtime control file cannot be verified") from None


def _immutable(path: Path, *, directory: bool | None = None, symlink: bool = False) -> None:
    """Reject writable/foreign path components, following only explicit tool links."""
    try:
        target = path.resolve(strict=True) if symlink else path
        for current in (target, *target.parents):
            info = current.lstat()
            if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
                raise Refusal("prerequisite", "The shared runtime is not administrator controlled")
        info = target.stat()
        if directory is True and not stat.S_ISDIR(info.st_mode):
            raise Refusal("prerequisite", "A required runtime directory is unavailable")
        if directory is False and not stat.S_ISREG(info.st_mode):
            raise Refusal("prerequisite", "A required runtime file is unavailable")
        if symlink:
            # Also protect the link itself and every component of its original path.
            for current in (path, *path.parents):
                info = current.lstat()
                if info.st_uid != 0 or info.st_mode & 0o022 and not stat.S_ISLNK(info.st_mode):
                    raise Refusal("prerequisite", "A runtime tool path is writable")
    except OSError:
        raise Refusal("prerequisite", "A required immutable runtime path is unavailable") from None


def _directory(path: Path, owner: int, group: int, mode: int, *, create: bool) -> None:
    if create:
        try:
            path.mkdir(mode=mode)
            os.chown(path, owner, group)
            path.chmod(mode)
        except FileExistsError:
            pass
    try:
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_gid != group
                or stat.S_IMODE(info.st_mode) != mode):
            raise Refusal("identity_conflict", "An account directory has unsafe ownership or permissions")
    except OSError:
        raise Refusal("identity_conflict", "An account directory is unavailable") from None


def _immutable_tree(tree: Path) -> None:
    """Check every entry once, retaining full-chain validation for symlinks.

    The tree root and its ancestors are verified first. Descendant directories
    are then checked before os.walk descends into them, so repeating the same
    ancestor lstat for every ordinary dependency adds no security. No result is
    cached between status requests.
    """
    _immutable(tree, directory=True)

    def failed(_error):
        raise Refusal("prerequisite", "A shared dependency directory cannot be inspected")

    try:
        for directory, names, files in os.walk(tree, followlinks=False, onerror=failed):
            for name in [*names, *files]:
                entry = Path(directory) / name
                info = entry.lstat()
                if stat.S_ISLNK(info.st_mode):
                    _immutable(entry, symlink=True)
                elif (info.st_uid != 0 or info.st_mode & 0o022
                      or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))):
                    raise Refusal("prerequisite", "A shared dependency entry is not administrator controlled")
    except OSError:
        raise Refusal("prerequisite", "A shared dependency entry cannot be inspected") from None


def _atomic_json(path: Path, value: dict, gid: int) -> None:
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(12))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o640)
    try:
        os.fchown(fd, 0, gid)
        os.fchmod(fd, 0o640)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        temporary.unlink(missing_ok=True)


class Controller:
    """Root helper. Paths and subprocess runner are injectable only for tests."""

    def __init__(self, *, runtime_root: str = RUNTIME_ROOT, state_root: str = STATE_ROOT,
                 runner=None, cgroup_root: str = "/sys/fs/cgroup"):
        self.runtime = Path(runtime_root)
        self.root = Path(state_root)
        self.cgroup_root = Path(cgroup_root)
        self.runner = runner or subprocess.run

    def run(self, argv: list[str], *, timeout: int = 20, input: str | None = None):
        try:
            return self.runner(argv, input=input, text=True, capture_output=True, check=False,
                               timeout=timeout, env=dict(SAFE_ENV), cwd="/")
        except (OSError, subprocess.SubprocessError):
            raise Refusal("prerequisite", "A required Linux control operation failed") from None

    def _roots(self, request: dict) -> None:
        if request.get("runtime_root", str(self.runtime)) != str(self.runtime) or request.get("state_root", str(self.root)) != str(self.root):
            raise Refusal("invalid_request", "Only administrator provisioned runtime paths are supported")

    def runtime_info(self, request: dict) -> dict:
        self._roots(request)
        _immutable(self.runtime, directory=True)
        manifest_path = self.runtime / "manifest.json"
        _immutable(manifest_path, directory=False)
        manifest = _read_json(manifest_path, owner=0)
        files = manifest.get("files")
        if manifest.get("version") != VERSION or not isinstance(files, dict) or not files:
            raise Refusal("prerequisite", "The runtime manifest is invalid")
        source_hash = _hex(manifest.get("source_sha256"), HASH)
        if digest(files) != source_hash:
            raise Refusal("prerequisite", "The runtime manifest source digest differs")
        for relative, expected in files.items():
            if not isinstance(relative, str) or "\\" in relative:
                raise Refusal("prerequisite", "The runtime manifest contains an unsafe path")
            parts = PurePosixPath(relative)
            if (parts.is_absolute() or str(parts) != relative or not parts.parts
                    or ".." in parts.parts or "." in parts.parts):
                raise Refusal("prerequisite", "The runtime manifest contains an unsafe path")
            _hex(expected, HASH)
            path = self.runtime / relative
            _immutable(path, directory=False)
            if file_digest(path) != expected:
                raise Refusal("runtime_changed", "A shared runtime file differs from its manifest")
        source = self.runtime / "app"
        _immutable(source, directory=True)
        required = ["app/src/matcreator/local_srt_policy.py",
                    "app/src/matcreator/scripts/local_srt_supervisor.py",
                    "app/src/matcreator/scripts/local_srt_worker.py",
                    "app/src/matcreator/scripts/local_srt_channel.py", "bin/bwrap"]
        if any(relative not in files for relative in required):
            raise Refusal("prerequisite", "The shared runtime manifest lacks isolation components")
        for path in source.rglob("*"):
            if path.is_symlink():
                raise Refusal("prerequisite", "Public application source cannot contain symbolic links")
            if path.is_file() and path.relative_to(self.runtime).as_posix() not in files:
                raise Refusal("runtime_changed", "An application file is absent from the trusted manifest")
        python = self.runtime / "venv/bin/python"
        _immutable(python, directory=False, symlink=True)
        _immutable(Path(NODE), directory=False, symlink=True)
        _immutable(Path(SRT_CLI), directory=False)
        # Dependencies are shared without per-account copies. Their content is
        # administrator managed; no account may edit any imported dependency.
        for tree in (self.runtime / "venv", Path(TOOLS_ROOT)):
            _immutable_tree(tree)
        for path in (python, Path(NODE), self.runtime / "bin/bwrap"):
            if not os.access(path, os.X_OK):
                raise Refusal("prerequisite", "A required runtime program cannot be executed")
        runtime_sha = file_digest(manifest_path)
        for key, actual in (("source_hash", source_hash), ("runtime_sha256", runtime_sha)):
            if request.get(key) is not None and _hex(request[key], HASH) != actual:
                raise Refusal("runtime_changed", "The requested runtime revision differs")
        return {"source_hash": source_hash, "runtime_sha256": runtime_sha,
                "source_root": str(source), "python": str(python)}

    def preflight(self, request: dict) -> dict:
        info = self.runtime_info(request)
        try:
            controllers = set(Path("/sys/fs/cgroup/cgroup.controllers").read_text(encoding="ascii").split())
        except OSError:
            raise Refusal("prerequisite", "A cgroup v2 host is required") from None
        if not {"cpu", "memory", "pids", "cpuset"}.issubset(controllers):
            raise Refusal("prerequisite", "Required cgroup v2 resource controllers are unavailable")
        for executable in ("systemctl", "systemd-run", "useradd", "bwrap", "socat", "rg"):
            path = shutil.which(executable, path=SAFE_ENV["PATH"])
            if not path:
                raise Refusal("prerequisite", "A required Linux isolation program is unavailable")
            _immutable(Path(path), directory=False, symlink=True)
        result = self.run(["systemctl", "show", "--property=Version", "--value"])
        if result.returncode or not result.stdout.strip():
            raise Refusal("prerequisite", "The Linux systemd manager is unavailable")
        # These commands do not start a service or execute account code.
        if self.run(["bwrap", "--version"]).returncode:
            raise Refusal("prerequisite", "bubblewrap is unavailable")
        version = self.run([NODE, "-p", "require('" + SRT_CLI.rsplit("/dist/", 1)[0] + "/package.json').version"])
        if version.returncode or version.stdout.strip() != "0.0.81":
            raise Refusal("prerequisite", "The administrator provisioned SRT revision is unavailable")
        return {**self.base(), **{k: info[k] for k in ("source_hash", "runtime_sha256")},
                "runtime_root": str(self.runtime), "state_root": str(self.root),
                "capabilities": {"root": True, "systemd": True, "cgroup_v2": True,
                                 "bubblewrap": True, "srt": True, "immutable_runtime": True,
                                 "prebound_ingress": True}}

    @staticmethod
    def base() -> dict:
        return {"version": VERSION, "ok": True, "backend": "srt"}

    def paths(self, identity: dict) -> dict[str, Path]:
        installation = self.root / identity["installation_id"]
        account = installation / identity["account_id"]
        return {"installation": installation, "account": account, "home": account / "home",
                "control": account / "control", "state": account / "state"}

    def _user(self, identity: dict, home: Path, *, create: bool) -> tuple[int, int]:
        import pwd
        import grp
        try:
            user = pwd.getpwnam(identity["linux_user"])
        except KeyError:
            if not create:
                raise Refusal("identity_conflict", "The account execution identity is unavailable") from None
            try:
                grp.getgrnam(identity["linux_user"])
            except KeyError:
                pass
            else:
                raise Refusal("identity_conflict", "The execution group belongs to another identity")
            result = self.run(["useradd", "--system", "--user-group", "--no-create-home",
                               "--home-dir", str(home), "--shell", "/usr/sbin/nologin",
                               "--comment", identity["gecos"], identity["linux_user"]])
            if result.returncode:
                raise Refusal("provision_failed", "A private Linux execution identity could not be provisioned")
            try:
                user = pwd.getpwnam(identity["linux_user"])
            except KeyError:
                raise Refusal("provision_failed", "The provisioned Linux identity cannot be verified") from None
        try:
            group = grp.getgrgid(user.pw_gid)
            members = group.gr_mem
        except KeyError:
            raise Refusal("identity_conflict", "The execution group cannot be verified") from None
        if (user.pw_uid == 0 or user.pw_gid == 0 or user.pw_gecos != identity["gecos"]
                or user.pw_dir != str(home) or user.pw_shell != "/usr/sbin/nologin"
                or group.gr_name != identity["linux_user"] or members):
            raise Refusal("identity_conflict", "The Linux account belongs to another runtime identity")
        # A different account must not share this primary group, either.
        if (any(other.pw_name != user.pw_name and
                (other.pw_uid == user.pw_uid or other.pw_gid == user.pw_gid) for other in pwd.getpwall())
                or any(user.pw_name in other.gr_mem for other in grp.getgrall())):
            raise Refusal("identity_conflict", "The execution group is not private to this account")
        return user.pw_uid, user.pw_gid

    def _directories(self, paths: dict, uid: int, gid: int, *, create: bool) -> None:
        _immutable(self.root.parent, directory=True)
        _directory(self.root, 0, 0, 0o711, create=create)
        _directory(paths["installation"], 0, 0, 0o711, create=create)
        _directory(paths["account"], 0, gid, 0o750, create=create)
        _directory(paths["control"], 0, gid, 0o750, create=create)
        _directory(paths["home"], uid, gid, 0o700, create=create)
        _directory(paths["state"], uid, gid, 0o700, create=create)

    @contextmanager
    def _lock(self, installation: str, *, name: str = ".controller.lock"):
        import fcntl
        # One lock serializes all first provisioning in an installation, including UID creation.
        _immutable(self.root.parent, directory=True)
        _directory(self.root, 0, 0, 0o711, create=True)
        directory = self.root / installation
        _directory(directory, 0, 0, 0o711, create=True)
        fd = os.open(directory / name, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(fd)
            if info.st_uid != 0 or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
                raise Refusal("identity_conflict", "The controller lock is not private")
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield fd
        finally:
            os.close(fd)

    def _show(self, unit: str) -> dict:
        properties = ["LoadState", "ActiveState", "SubState", "MainPID", "User", "Group", "ExecStart",
                      "WorkingDirectory", "NoNewPrivileges", "KillMode", "Restart", "ControlGroup",
                      "InvocationID", "Delegate", "ProtectControlGroups", "Environment"]
        result = self.run(["systemctl", "show", unit, "--no-pager", "--property=" + ",".join(properties)])
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if values.get("LoadState") == "not-found":
            return {"LoadState": "not-found", "MainPID": "0", "ActiveState": "inactive"}
        if result.returncode or not values or values.get("LoadState") != "loaded":
            raise Refusal("unit_unknown", "The systemd service state cannot be verified")
        return values

    @staticmethod
    def command(spec: dict, paths: dict) -> list[str]:
        return [spec["python"], "-I", spec["source_root"] + "/src/matcreator/scripts/local_srt_supervisor.py",
                "--spec", str(paths["control"] / "spec.json")]

    def _check_unit(self, unit: dict, spec: dict, paths: dict) -> None:
        expected = {"User": spec["linux_user"], "Group": spec["linux_user"],
                    "WorkingDirectory": spec["source_root"], "NoNewPrivileges": "yes",
                    "KillMode": "control-group", "Restart": "no", "Delegate": "no",
                    "ProtectControlGroups": "yes", "ControlGroup": "/system.slice/" + spec["unit_name"]}
        if any(unit.get(key) != value for key, value in expected.items()):
            raise Refusal("unit_mismatch", "The service identity or containment settings differ")
        command = self.command(spec, paths)
        found = re.findall(r"(?:^|[ ;{])argv\[\]=(.*?)\s*;", unit.get("ExecStart", ""))
        executables = re.findall(r"(?:^|[ ;{])path=([^ ;]+)", unit.get("ExecStart", ""))
        if len(found) != 1 or found[0].split() != command or executables != [command[0]]:
            raise Refusal("unit_mismatch", "The service executable differs")
        expected_env = {"PYTHONNOUSERSITE=1", "PYTHONDONTWRITEBYTECODE=1", "PYTHONUNBUFFERED=1"}
        if set(unit.get("Environment", "").split()) != expected_env:
            raise Refusal("unit_mismatch", "The service environment differs")
        self._check_cgroup(unit["ControlGroup"], spec["limits"])

    def _check_cgroup(self, group: str, limits: dict) -> None:
        path = self.cgroup_root / group.lstrip("/")
        try:
            memory = (path / "memory.max").read_text(encoding="ascii").strip()
            swap = (path / "memory.swap.max").read_text(encoding="ascii").strip()
            pids = (path / "pids.max").read_text(encoding="ascii").strip()
            quota, period = (path / "cpu.max").read_text(encoding="ascii").split()
            effective = parse_cpu_set((path / "cpuset.cpus.effective").read_text(encoding="ascii").strip())
            requested = parse_cpu_set(limits["cpuset_cpus"])
            if (int(memory) != limits["memory_mb"] * 1048576 or int(swap) != 0 or int(pids) != limits["pids"]
                    or quota == "max" or not math.isclose(int(quota) / int(period), limits["cpus"], rel_tol=0.001)
                    or not effective or requested and effective != requested):
                raise ValueError
        except (OSError, ValueError, ZeroDivisionError):
            raise Refusal("limit_mismatch", "The service cgroup resource limits differ") from None

    def _spec(self, identity: dict, paths: dict, info: dict, uid: int, gid: int,
              token: str, limits: dict, network: dict) -> dict:
        return {"version": VERSION, **identity, "access_token": token, "uid": uid, "gid": gid,
                "home": str(paths["home"]), "source_root": info["source_root"], "python": info["python"],
                "node": NODE, "srt_cli": SRT_CLI, "tools_root": TOOLS_ROOT,
                "runtime_root": str(self.runtime), "bwrap_wrapper_dir": str(self.runtime / "bin"),
                "state_dir": str(paths["state"]), "runtime_sha256": info["runtime_sha256"],
                "ingress_kind": "stdio-mux-v1",
                "policy_path": str(paths["control"] / "policy.json"),
                "allowed_domains": network["allowed_domains"], "denied_domains": network["denied_domains"],
                "parent_proxy": network["parent_proxy"], "limits": limits}

    def _policy(self, spec: dict) -> tuple[dict, str]:
        result = self.run([spec["python"], "-I", spec["source_root"] + "/src/matcreator/local_srt_policy.py"],
                          input=json.dumps(spec), timeout=30)
        try:
            value = json.loads(result.stdout)
            policy = value["policy"]
            policy_hash = value["policy_sha256"]
            if result.returncode or not isinstance(policy, dict) or digest(policy) != _hex(policy_hash, HASH):
                raise ValueError
            return policy, policy_hash
        except (ValueError, KeyError, TypeError):
            raise Refusal("policy_failed", "The administrator isolation policy could not be generated") from None

    def _existing(self, identity: dict, paths: dict, info: dict, request: dict) -> tuple[dict, dict]:
        uid, gid = self._user(identity, paths["home"], create=False)
        self._directories(paths, uid, gid, create=False)
        record = _read_json(paths["control"] / "record.json", owner=0, mode=0o640)
        spec = _read_json(paths["control"] / "spec.json", owner=0, mode=0o640)
        policy = _read_json(paths["control"] / "policy.json", owner=0, mode=0o640)
        expected = {**identity, "uid": uid, "gid": gid, "home": str(paths["home"]),
                    "source_root": info["source_root"], "python": info["python"],
                    "node": NODE, "srt_cli": SRT_CLI, "tools_root": TOOLS_ROOT,
                    "runtime_root": str(self.runtime), "runtime_sha256": info["runtime_sha256"],
                    "state_dir": str(paths["state"]), "bwrap_wrapper_dir": str(self.runtime / "bin"),
                    "ingress_kind": "stdio-mux-v1",
                    "policy_path": str(paths["control"] / "policy.json")}
        if (spec.get("version") != VERSION or any(spec.get(key) != value for key, value in expected.items())
                or record.get("spec_hash") != digest(spec) or record.get("policy_hash") != digest(policy)
                or spec.get("policy_sha256") != digest(policy) or record.get("source_hash") != info["source_hash"]):
            raise Refusal("identity_conflict", "The retained runtime identity or policy differs")
        token = spec.get("access_token")
        token_hash = _hex(request.get("access_token_sha256"), HASH)
        if (not isinstance(token, str) or not TOKEN.fullmatch(token)
                or not secrets.compare_digest(hashlib.sha256(token.encode()).hexdigest(), token_hash)
                or record.get("access_token_sha256") != token_hash):
            raise Refusal("identity_conflict", "The retained runtime credential differs")
        if "access_token" in request and not secrets.compare_digest(str(request["access_token"]), token):
            raise Refusal("identity_conflict", "The retained runtime credential differs")
        limits = limits_for(request, set(os.sched_getaffinity(0)))
        network = network_for(request)
        if limits != spec.get("limits") or any(spec.get(key) != network[key] for key in network):
            raise Refusal("configuration_conflict", "The retained runtime configuration differs")
        rebuilt, rebuilt_hash = self._policy(spec)
        if rebuilt != policy or rebuilt_hash != spec["policy_sha256"]:
            raise Refusal("policy_mismatch", "The retained isolation policy differs")
        return spec, record

    def _start(self, spec: dict, paths: dict) -> None:
        limits = spec["limits"]
        properties = {"User": spec["linux_user"], "Group": spec["linux_user"],
                      "WorkingDirectory": spec["source_root"], "MemoryMax": str(limits["memory_mb"]) + "M",
                      "MemorySwapMax": "0",
                      "TasksMax": str(limits["pids"]), "CPUQuota": format(limits["cpus"] * 100, ".6g") + "%",
                      "NoNewPrivileges": "yes", "KillMode": "control-group", "Restart": "no",
                      "Delegate": "no", "ProtectControlGroups": "yes",
                      "LogRateLimitIntervalSec": "30s", "LogRateLimitBurst": "100"}
        if limits["cpuset_cpus"]:
            properties["AllowedCPUs"] = limits["cpuset_cpus"]
        command = ["systemd-run", "--unit=" + spec["unit_name"], "--quiet"]
        command += ["--property=" + key + "=" + value for key, value in properties.items()]
        command += ["--setenv=PYTHONNOUSERSITE=1", "--setenv=PYTHONDONTWRITEBYTECODE=1",
                    "--setenv=PYTHONUNBUFFERED=1", "--", *self.command(spec, paths)]
        result = self.run(command, timeout=30)
        if result.returncode:
            raise Refusal("launch_failed", "The private service did not start; its existing state was preserved")

    def _process(self, pid: int, expected_uid: int, group: str) -> str:
        starttime = proc_starttime(pid)
        try:
            lines = Path("/proc", str(pid), "status").read_text(encoding="ascii").splitlines()
            uids = next(line.split()[1:] for line in lines if line.startswith("Uid:"))
            cgroup = Path("/proc", str(pid), "cgroup").read_text(encoding="ascii").strip()
            if any(int(value) != expected_uid for value in uids) or cgroup != "0::" + group:
                raise ValueError
        except (OSError, ValueError, StopIteration):
            raise Refusal("identity_conflict", "The service process ownership differs") from None
        return starttime

    def _listener(self, pid: int, port: int) -> None:
        try:
            inodes = set()
            for descriptor in Path("/proc", str(pid), "fd").iterdir():
                try:
                    target = os.readlink(descriptor)
                except OSError:
                    continue
                match = re.fullmatch(r"socket:\[(\d+)\]", target)
                if match:
                    inodes.add(match.group(1))
            rows = Path("/proc", str(pid), "net/tcp").read_text(encoding="ascii").splitlines()[1:]
            if not any((fields := row.split()) and fields[1] == "0100007F:" + format(port, "04X")
                       and fields[3] == "0A" and fields[9] in inodes for row in rows):
                raise ValueError
        except (OSError, ValueError, IndexError):
            raise Refusal("identity_conflict", "The private ingress listener cannot be verified") from None

    def _state(self, spec: dict, paths: dict, record: dict, unit: dict, *, created: bool,
               inspect_health: bool = True) -> dict:
        result = {**self.base(), **{key: spec[key] for key in ("installation_id", "account_id", "instance_id",
                  "unit_name", "linux_user", "uid", "gid")}, "profile_home": spec["home"],
                  "control_dir": str(paths["control"]), "source_hash": record["source_hash"],
                  "runtime_sha256": spec["runtime_sha256"], "policy_hash": spec["policy_sha256"],
                  "ingress_kind": spec["ingress_kind"],
                  "spec_hash": record["spec_hash"], "created": created, "data_retained": True,
                  "web_port": 0, "main_pid": 0, "main_starttime": "", "boot_id": boot_id()}
        if record.get("stopped"):
            if unit.get("ActiveState") in {"active", "activating", "deactivating"} or int(unit.get("MainPID", "0")):
                raise Refusal("unit_mismatch", "A retained stopped service is unexpectedly active")
            return {**result, "state": "stopped", "cleanup_confirmed": True}
        if unit.get("LoadState") == "not-found" or unit.get("ActiveState") not in {"active", "activating"}:
            raise Refusal("unit_unknown", "The retained service is not active; its data was preserved")
        self._check_unit(unit, spec, paths)
        try:
            pid = int(unit.get("MainPID", "0"))
        except ValueError:
            raise Refusal("identity_conflict") from None
        if pid <= 0:
            return {**result, "state": "starting"}
        start = self._process(pid, spec["uid"], unit["ControlGroup"])
        invocation = _hex(unit.get("InvocationID"))
        pinned = record.get("launch_identity")
        actual = {"main_pid": pid, "main_starttime": start, "boot_id": boot_id(), "unit_invocation_id": invocation}
        if pinned is not None and pinned != actual:
            raise Refusal("identity_conflict", "The retained service process was replaced")
        result.update(actual)
        receipt = {"version": VERSION, **{key: spec[key] for key in ("installation_id", "account_id", "instance_id",
                   "unit_name", "linux_user", "uid", "gid", "gecos")}, "profile_home": spec["home"],
                   "source_hash": record["source_hash"], "runtime_sha256": spec["runtime_sha256"],
                   "policy_hash": spec["policy_sha256"], "spec_hash": record["spec_hash"],
                   "ingress_kind": spec["ingress_kind"],
                   "ownership_id": record["ownership_id"], **actual}
        result["ownership_receipt"] = receipt
        if not inspect_health:
            return {**result, "state": "starting"}
        state_path = paths["state"] / "state.json"
        if not state_path.exists():
            return {**result, "state": "starting"}
        state = _read_json(state_path, owner=spec["uid"])
        expected = {key: spec[key] for key in ("installation_id", "account_id", "instance_id", "uid", "gid",
                                              "home", "unit_name", "runtime_sha256", "policy_sha256", "ingress_kind")}
        expected.update(main_pid=pid, main_starttime=start, boot_id=actual["boot_id"])
        if any(state.get(key) != value for key, value in expected.items()):
            raise Refusal("identity_conflict", "The supervisor state belongs to another process")
        if state.get("status") not in {"starting", "ready"}:
            raise Refusal("service_failed", "The supervisor did not establish a healthy service")
        if state["status"] == "ready":
            port = state.get("web_port")
            if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                raise Refusal("identity_conflict", "The ingress port is invalid")
            self._listener(pid, port)
            result.update(web_port=port)
        return {**result, "state": state["status"]}

    def ensure(self, request: dict) -> dict:
        identity = account_identity(request)
        info = self.runtime_info(request)
        paths = self.paths(identity)
        token_hash = _hex(request.get("access_token_sha256"), HASH)
        limits = limits_for(request, set(os.sched_getaffinity(0)))
        network = network_for(request)
        with self._lock(identity["installation_id"]):
            unit = self._show(identity["unit_name"])
            if paths["account"].exists() or paths["account"].is_symlink():
                spec, record = self._existing(identity, paths, info, request)
                return self._state(spec, paths, record, unit, created=False)
            if unit.get("LoadState") != "not-found":
                raise Refusal("identity_conflict", "A service with this identity already exists")
            token = request.get("access_token")
            if not isinstance(token, str) or not TOKEN.fullmatch(token) or not secrets.compare_digest(hashlib.sha256(token.encode()).hexdigest(), token_hash):
                raise Refusal("invalid_request", "An initial internal access credential is required")
            uid, gid = self._user(identity, paths["home"], create=True)
            self._directories(paths, uid, gid, create=True)
            spec = self._spec(identity, paths, info, uid, gid, token, limits, network)
            policy, policy_hash = self._policy(spec)
            spec["policy_sha256"] = policy_hash
            record = {"version": VERSION, "source_hash": info["source_hash"], "spec_hash": digest(spec),
                      "policy_hash": policy_hash, "access_token_sha256": token_hash,
                      "ownership_id": secrets.token_hex(32), "stopped": False}
            _atomic_json(paths["control"] / "policy.json", policy, gid)
            _atomic_json(paths["control"] / "spec.json", spec, gid)
            _atomic_json(paths["control"] / "record.json", record, gid)
            self._start(spec, paths)
            # systemd-run synchronously starts the service; poll briefly if MainPID is not yet assigned.
            for attempt in range(20):
                unit = self._show(identity["unit_name"])
                # Persist/return launch ownership before checking Web health.
                # A subsequent startup failure must remain exactly cleanable
                # by the manager that launched it, with no replacement unit.
                result = self._state(spec, paths, record, unit, created=True, inspect_health=False)
                if result["main_pid"]:
                    record["launch_identity"] = {key: result[key] for key in (
                        "main_pid", "main_starttime", "boot_id", "unit_invocation_id")}
                    _atomic_json(paths["control"] / "record.json", record, gid)
                    return result
                time.sleep(0.1)
            raise Refusal("launch_uncertain", "The launched service identity is pending; it was preserved")

    def status(self, request: dict) -> dict:
        identity = account_identity(request)
        info = self.runtime_info(request)
        paths = self.paths(identity)
        # A status query never creates an account directory, UID, or unit.
        unit = self._show(identity["unit_name"])
        if not paths["account"].exists() and not paths["account"].is_symlink():
            if unit.get("LoadState") != "not-found":
                raise Refusal("identity_conflict", "A service has no matching controller record")
            return {**self.base(), **{key: identity[key] for key in ("installation_id", "account_id", "instance_id")},
                    "state": "absent", "created": False, "source_hash": info["source_hash"],
                    "runtime_sha256": info["runtime_sha256"], "ingress_kind": "stdio-mux-v1"}
        spec, record = self._existing(identity, paths, info, request)
        return self._state(spec, paths, record, unit, created=False)

    def stop_test_owned(self, request: dict) -> dict:
        identity = account_identity(request)
        info = self.runtime_info(request)
        paths = self.paths(identity)
        with self._lock(identity["installation_id"]):
            spec, record = self._existing(identity, paths, info, request)
            unit = self._show(identity["unit_name"])
            receipt = request.get("ownership_receipt")
            launch = record.get("launch_identity")
            if not isinstance(launch, dict):
                raise Refusal("ownership_mismatch", "The original service launch identity is unavailable")
            expected_receipt = {"version": VERSION, **{key: spec[key] for key in (
                "installation_id", "account_id", "instance_id", "unit_name", "linux_user", "uid", "gid", "gecos")},
                "profile_home": spec["home"], "source_hash": record["source_hash"],
                "runtime_sha256": spec["runtime_sha256"], "policy_hash": spec["policy_sha256"],
                "ingress_kind": spec["ingress_kind"],
                "spec_hash": record["spec_hash"], "ownership_id": record["ownership_id"], **launch}
            if not isinstance(receipt, dict) or receipt != expected_receipt:
                raise Refusal("ownership_mismatch", "Only the exact service created by this test may be stopped")
            if unit.get("ActiveState") in {"active", "activating"}:
                # A failed Web/ADK health check must not prevent exact cleanup.
                current = self._state(spec, paths, record, unit, created=False, inspect_health=False)
                if current.get("ownership_receipt") != receipt:
                    raise Refusal("ownership_mismatch", "The service process identity differs")
            else:
                # Already-failed or collected units can be marked stopped only
                # if the recorded host process no longer exists in this boot.
                current = {**self.base(), **{key: spec[key] for key in (
                    "installation_id", "account_id", "instance_id", "unit_name", "linux_user", "uid", "gid")},
                    "profile_home": spec["home"], "control_dir": str(paths["control"]),
                    "source_hash": record["source_hash"], "runtime_sha256": spec["runtime_sha256"],
                    "policy_hash": spec["policy_sha256"], "spec_hash": record["spec_hash"],
                    "ingress_kind": spec["ingress_kind"],
                    "ownership_receipt": receipt, **launch}
                if unit.get("MainPID") not in {None, "0"}:
                    raise Refusal("ownership_mismatch", "An inactive service still has a process")
            old_pid, old_start = launch["main_pid"], launch["main_starttime"]
            if launch["boot_id"] == boot_id():
                try:
                    original_alive = proc_starttime(old_pid) == old_start
                except Refusal:
                    original_alive = False
                if original_alive and unit.get("ActiveState") not in {"active", "activating"}:
                    raise Refusal("ownership_mismatch", "The original process is outside its active service")
            if unit.get("LoadState") != "not-found" and self.run(["systemctl", "stop", identity["unit_name"]], timeout=35).returncode:
                raise Refusal("stop_unconfirmed", "The exact private service could not be stopped")
            unit = self._show(identity["unit_name"])
            if unit.get("ActiveState") in {"active", "activating", "deactivating"} or int(unit.get("MainPID", "0")):
                raise Refusal("stop_unconfirmed", "The private service is still active")
            still_original = False
            if launch["boot_id"] == boot_id():
                try:
                    still_original = proc_starttime(old_pid) == old_start
                except Refusal:
                    pass
            if still_original:
                raise Refusal("stop_unconfirmed", "The original service process is still present")
            record["stopped"] = True
            _atomic_json(paths["control"] / "record.json", record, spec["gid"])
            return {**current, "state": "stopped", "web_port": 0, "main_pid": 0,
                    "cleanup_confirmed": True, "data_retained": True, "created": False}

    def _running_units(self, installation: str) -> bool:
        prefix = "mc-srt-" + _hex(installation) + "-"
        result = self.run(["systemctl", "list-units", "--all", "--type=service", "--state=running,activating",
                           "--plain", "--no-legend", "--no-pager", prefix + "*.service"])
        if result.returncode:
            raise Refusal("unit_unknown", "Private service liveness cannot be checked")
        units = [line.split()[0] for line in result.stdout.splitlines() if line.strip()]
        for unit in units:
            tail = unit.removeprefix(prefix).removesuffix(".service")
            try:
                valid = unit.startswith(prefix) and unit.endswith(".service") and str(uuid.UUID(tail)) == tail
            except ValueError:
                valid = False
            if not valid:
                raise Refusal("unit_unknown", "Unexpected private service identity")
        return bool(units)

    def _hold_fd(self, installation: str, *, create: bool) -> int:
        _hex(installation)
        if create:
            _immutable(self.root.parent, directory=True)
            _directory(self.root, 0, 0, 0o711, create=True)
            _directory(self.root / installation, 0, 0, 0o711, create=True)
        path = self.root / installation / ".hold.lock"
        if not create:
            # Ancestors are root-only writable, including on status-only paths.
            if not self.root.exists() or not (self.root / installation).exists():
                raise FileNotFoundError
            _directory(self.root, 0, 0, 0o711, create=False)
            _directory(self.root / installation, 0, 0, 0o711, create=False)
        fd = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | (os.O_CREAT if create else 0), 0o600)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            os.close(fd)
            raise Refusal("identity_conflict", "The installation keeper lock is unsafe")
        return fd

    def status_hold(self, request: dict) -> dict:
        import fcntl
        self._roots(request)
        installation = _hex(request.get("installation_id"))
        result = {**self.base(), "installation_id": installation, "active": False}
        try:
            fd = self._hold_fd(installation, create=False)
        except FileNotFoundError:
            return result
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return result
            except BlockingIOError:
                pass
            try:
                value = json.loads(os.read(fd, 4096))
                if (value.get("version") == VERSION and value.get("installation_id") == installation
                        and value.get("boot_id") == boot_id() and isinstance(value.get("pid"), int)
                        and value["pid"] > 0 and proc_starttime(value["pid"]) == value.get("starttime")):
                    result.update(active=True, pid=value["pid"], boot_id=value["boot_id"])
            except (ValueError, AttributeError, Refusal):
                pass
            return result
        finally:
            os.close(fd)

    def hold(self, request: dict) -> dict:
        import fcntl
        self._roots(request)
        installation = _hex(request.get("installation_id"))
        fd = self._hold_fd(installation, create=True)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {**self.base(), "installation_id": installation, "active": True}
            if not self._running_units(installation):
                return {**self.base(), "installation_id": installation, "active": False}
            value = {"version": VERSION, "installation_id": installation, "boot_id": boot_id(),
                     "pid": os.getpid(), "starttime": proc_starttime(os.getpid())}
            os.ftruncate(fd, 0)
            os.write(fd, json.dumps(value).encode("ascii"))
            os.fsync(fd)
            while self._running_units(installation):
                time.sleep(3)
            return {**self.base(), "installation_id": installation, "active": False}
        finally:
            os.close(fd)

    def request(self, request: dict) -> dict:
        if not isinstance(request, dict) or request.get("version") != VERSION:
            raise Refusal("invalid_request", "The controller protocol version is invalid")
        if sys.platform != "linux" or not hasattr(os, "geteuid") or os.geteuid() != 0:
            raise Refusal("prerequisite", "The Linux controller requires administrator provisioning")
        self._roots(request)
        action = request.get("action")
        if action not in {"preflight", "ensure", "status", "stop_test_owned", "hold", "status_hold"}:
            raise Refusal("invalid_request", "The controller action is invalid")
        return getattr(self, action)(request)


def main() -> int:
    try:
        data = sys.stdin.read(MAX_JSON + 1)
        if len(data) > MAX_JSON:
            raise Refusal("invalid_request", "The controller request is too large")
        request = json.loads(data)
        result = Controller().request(request)
    except Refusal as exc:
        result = {"version": VERSION, "ok": False, "code": exc.code, "detail": exc.detail}
    except (ValueError, TypeError):
        result = {"version": VERSION, "ok": False, "code": "invalid_request"}
    except Exception:
        # Never echo raw exception text, argv, spec, environment, or proxy secrets.
        result = {"version": VERSION, "ok": False, "code": "controller_failed"}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
