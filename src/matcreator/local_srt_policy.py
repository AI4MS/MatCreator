"""Compile the administrator's account specification into a strict SRT policy.

Pure standard library; the root provisioner can execute this file directly and
send the specification on stdin. User settings never enter this compiler.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
import re
import sys
from urllib.parse import urlsplit
import uuid


SYSTEM_READ_PATHS = (
    "/usr", "/bin", "/lib", "/lib64", "/etc/ld.so.cache", "/etc/ssl",
    "/etc/nsswitch.conf", "/etc/hosts", "/etc/resolv.conf", "/etc/passwd",
    "/etc/group", "/etc/localtime", "/proc", "/dev",
)


class SrtPolicyError(ValueError):
    """An account policy cannot be constructed without widening its boundary."""


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def policy_digest(policy: dict) -> str:
    return hashlib.sha256(canonical_bytes(policy)).hexdigest()


def account_identity(spec: dict) -> str:
    account_id = spec.get("account_id")
    try:
        if not isinstance(account_id, str) or str(uuid.UUID(account_id)) != account_id:
            raise ValueError
    except (ValueError, AttributeError) as exc:
        raise SrtPolicyError("A canonical account UUID is required") from exc
    if spec.get("version") != 1:
        raise SrtPolicyError("Unsupported SRT account specification version")
    return account_id


def absolute_path(value: object, name: str) -> PurePosixPath:
    if (not isinstance(value, str) or not value.startswith("/") or value == "/"
            or any(char in value for char in "\x00\n\r\\*?[]") or ".." in value.split("/")):
        raise SrtPolicyError(f"{name} must be a specific absolute Linux path without patterns")
    path = PurePosixPath(value)
    if path.as_posix() != value.rstrip("/"):
        raise SrtPolicyError(f"{name} must use a normalized Linux path")
    return path


def _overlap(first: PurePosixPath, second: PurePosixPath) -> bool:
    return first.is_relative_to(second) or second.is_relative_to(first)


def _network_domains(values: object) -> list[str]:
    if not isinstance(values, list) or len(values) > 512:
        raise SrtPolicyError("Administrator network grants must be a finite list of host:port entries")
    result = []
    for entry in values:
        if not isinstance(entry, str):
            raise SrtPolicyError("Network grants must name a host and exact port")
        match = re.fullmatch(r"((?:\*\.)?[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?):([0-9]{1,5})", entry)
        if (not match or not 1 <= int(match[2]) <= 65535 or ".." in match[1]
                or any(not label or len(label) > 63 for label in match[1].removeprefix("*.").split("."))):
            raise SrtPolicyError("Network grants must name a hostname or IPv4 address and exact port")
        host = match[1].lower()
        result.append(host + ":" + str(int(match[2])))
    return sorted(set(result))


def _parent_proxy(value: object) -> dict | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        value = {"http": value, "https": value}
    if not isinstance(value, dict) or not value or set(value) - {"http", "https"}:
        raise SrtPolicyError("The administrator parent proxy must contain only HTTP/HTTPS proxy URLs")
    result = {}
    for key, url in value.items():
        if not isinstance(url, str) or any(char in url for char in "\x00\n\r"):
            raise SrtPolicyError("Invalid administrator parent proxy")
        try:
            parsed = urlsplit(url)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.port is None or not 1 <= parsed.port <= 65535
                    or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
                raise ValueError
        except ValueError as exc:
            raise SrtPolicyError("Invalid administrator parent proxy") from exc
        result[key] = url
    return result


def build_policy(spec: dict) -> dict:
    account_identity(spec)
    home = absolute_path(spec.get("home"), "home")
    runtime = absolute_path(spec.get("runtime_root"), "runtime_root")
    tools = absolute_path(spec.get("tools_root", "/opt/matcreator-srt-tools"), "tools_root")
    state = absolute_path(spec.get("state_dir"), "state_dir")
    policy = absolute_path(spec.get("policy_path"), "policy_path")
    source = absolute_path(spec.get("source_root"), "source_root")
    python = absolute_path(spec.get("python"), "python")
    node = absolute_path(spec.get("node"), "node")
    srt_cli = absolute_path(spec.get("srt_cli"), "srt_cli")
    bwrap = absolute_path(spec.get("bwrap_wrapper_dir"), "bwrap_wrapper_dir")
    if (not source.is_relative_to(runtime) or not python.is_relative_to(runtime)
            or not bwrap.is_relative_to(runtime) or not node.is_relative_to(tools)
            or not srt_cli.is_relative_to(tools)):
        raise SrtPolicyError("Execution tools must belong to the shared immutable installation")
    if any(_overlap(home, path) for path in (runtime, tools, state, policy.parent)):
        raise SrtPolicyError("Personal data and shared/control paths must be disjoint")
    if any(_overlap(private, public) for private in (state, policy.parent) for public in (runtime, tools)):
        raise SrtPolicyError("Control and state paths cannot be exposed by a shared installation grant")
    if any(private.is_relative_to(PurePosixPath(public)) for private in (state, policy.parent)
           for public in SYSTEM_READ_PATHS):
        raise SrtPolicyError("Control and state paths cannot be inside a public system read grant")
    if any(home.is_relative_to(PurePosixPath(path)) for path in SYSTEM_READ_PATHS):
        raise SrtPolicyError("A personal home cannot be inside a public system read grant")
    # A fixed path allowlist, never the original checkout or the broker root.
    filesystem = {
        "denyRead": ["/"],
        "allowRead": sorted(set(SYSTEM_READ_PATHS) | {str(home), str(runtime), str(tools)}),
        "allowWrite": [str(home)],
        "denyWrite": sorted({"/tmp/claude", "/private/tmp/claude", str(state), str(policy.parent)}),
    }
    network = {"allowedDomains": _network_domains(spec.get("allowed_domains", [])),
               "deniedDomains": _network_domains(spec.get("denied_domains", [])), "strictAllowlist": True}
    proxy = _parent_proxy(spec.get("parent_proxy"))
    if proxy:
        network["parentProxy"] = proxy
    # In particular, do not set allowAllUnixSockets or allowLocalBinding.
    return {"network": network, "filesystem": filesystem}


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise SrtPolicyError("Account specification is too large")
        spec = json.loads(raw)
        if not isinstance(spec, dict):
            raise SrtPolicyError("Account specification must be an object")
        policy = build_policy(spec)
        print(json.dumps({"policy": policy, "policy_sha256": policy_digest(policy)}))
        return 0
    except (ValueError, TypeError) as exc:
        # Error messages describe fields, never echo a token/proxy credential.
        print(f"SRT policy refused: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
