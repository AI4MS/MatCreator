"""Deny account-management state and other local profiles in MC file APIs.

This is an application path boundary, not an operating-system code sandbox.
The runtime manager supplies these environment markers; user configuration
cannot override them.
"""
from __future__ import annotations

import os
from pathlib import Path
import uuid


INTERNAL_ACCOUNT_ENV_KEYS = frozenset({
    "MATCREATOR_LOCAL_PROFILE_WORKER",
    "MATCREATOR_LOCAL_RUNTIME",
    "MATCREATOR_LOCAL_SRT_WORKER",
    "MATCREATOR_LOCAL_SRT_COMMAND",
    "MATCREATOR_LOCAL_SRT_RUNTIME_ROOT",
    "MATCREATOR_LOCAL_SRT_STATE_ROOT",
    "MATCREATOR_LOCAL_SRT_MEMORY_MB",
    "MATCREATOR_LOCAL_SRT_CPUS",
    "MATCREATOR_LOCAL_SRT_CPUSETS",
    "MATCREATOR_LOCAL_SRT_PIDS",
    "MATCREATOR_LOCAL_SRT_ALLOWED_DOMAINS",
    "MATCREATOR_LOCAL_SRT_DENIED_DOMAINS",
    "MATCREATOR_LOCAL_SRT_PARENT_PROXY",
    "MATCREATOR_SRT_BOOT_ACCOUNT_ID",
    "MATCREATOR_SRT_BOOT_INSTANCE_ID",
    "MATCREATOR_SRT_BOOT_ACCESS_TOKEN",
    "MATCREATOR_LOCAL_CONTAINER_COMMAND",
    "MATCREATOR_LOCAL_CONTAINER_IMAGE",
    "MATCREATOR_LOCAL_CONTAINER_MEMORY_MB",
    "MATCREATOR_LOCAL_CONTAINER_CPUS",
    "MATCREATOR_LOCAL_CONTAINER_CPUSETS",
    "MATCREATOR_LOCAL_CONTAINER_PIDS",
    "MATCREATOR_CONTAINER_BOOT_ACCOUNT_ID",
    "MATCREATOR_CONTAINER_BOOT_INSTANCE_ID",
    "MATCREATOR_CONTAINER_BOOT_ACCESS_TOKEN",
    "MATCREATOR_LOCAL_ACCOUNT_ROOT",
    "MATCREATOR_LOCAL_ACCOUNT_ID",
    "MATCREATOR_CONFIG_PATH",
    "MATCREATOR_USERS_DB",
    "MATCREATOR_CONTROL_PLANE_HOME",
    "MATCREATOR_DATA_ROOT",
    "MATCREATOR_HOST_DATA_ROOT",
    "KDG_DB_PATH",
    "OAW_XRD_RUN_ROOT",
    "MATCREATOR_XRD_HOME",
    "MATCREATOR_LOCAL_ALLOWED_HOSTS",
    "MATCLAW_WORKSPACE",
    "MATCLAW_SESSION_DIR",
})


def assert_local_account_path(path: str | Path) -> Path:
    """Resolve a path and reject protected local-account storage crossings."""
    candidate = Path(path).expanduser().resolve()
    if os.environ.get("MATCREATOR_LOCAL_PROFILE_WORKER") != "1":
        return candidate
    root_value = os.environ.get("MATCREATOR_LOCAL_ACCOUNT_ROOT", "")
    account_id = os.environ.get("MATCREATOR_LOCAL_ACCOUNT_ID", "")
    try:
        if str(uuid.UUID(account_id)) != account_id:
            raise ValueError("Invalid account identity")
    except (ValueError, AttributeError) as exc:
        raise PermissionError("Local profile runtime has no verified account identity") from exc
    if not root_value:
        raise PermissionError("Local profile runtime has no account storage root")
    root = Path(root_value).expanduser().resolve()
    home = Path(os.environ.get("MATCREATOR_HOME", str(Path.home() / ".matcreator"))).expanduser().resolve()
    profiles = root / "profiles"
    for private_root in (root / ".local-accounts", root / ".local-access", home / ".local-access"):
        if candidate.is_relative_to(private_root):
            raise PermissionError("Account-management credentials are not workspace files")
    # Browsing the broker root or one of its ancestors could enumerate other
    # profiles through directory listings even if single-file reads are gated.
    if root.is_relative_to(candidate) or candidate == profiles:
        raise PermissionError("Account-management storage is not a workspace")
    # A new profile has no implicit ownership of external project directories.
    # In particular, old installation workspaces may live outside the broker
    # home. Supporting external projects requires an explicit ownership grant.
    if home != root and not candidate.is_relative_to(home):
        raise PermissionError("Access outside this local account's profile is denied")
    if candidate.is_relative_to(profiles):
        relative = candidate.relative_to(profiles)
        if home == root or not relative.parts or relative.parts[0] != account_id:
            raise PermissionError("Access to another local account's profile is denied")
    return candidate
