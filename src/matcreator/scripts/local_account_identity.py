"""Bind a private Linux account profile to its broker-provisioned identity."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import uuid


class AccountBootstrapError(RuntimeError):
    """A private runtime cannot establish its broker-provisioned identity."""


def _check_owned_directory(path: Path) -> None:
    if path.is_symlink():
        raise AccountBootstrapError("Account profile directories cannot be symbolic links")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise AccountBootstrapError("Account profile is not a directory")
    if os.name == "posix":
        if info.st_uid != os.geteuid():
            raise AccountBootstrapError("Account profile is not owned by its execution user")
        path.chmod(0o700)


def prepare_identity(home: Path, *, account_id: str, instance_id: str,
                     access_token: str, principal: str | None = None) -> None:
    """Atomically bind a fresh profile to this account and its internal secret.

    ``principal`` exists for portable identity-format tests. Production always
    derives it from the actual Linux execution UID.
    """
    try:
        if str(uuid.UUID(account_id)) != account_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise AccountBootstrapError("A canonical account UUID is required") from exc
    if (not isinstance(instance_id, str) or not re.fullmatch(r"[a-f0-9]{32}", instance_id)
            or not isinstance(access_token, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", access_token)):
        raise AccountBootstrapError("The broker did not provide a valid internal runtime identity")
    home = Path(home).absolute()
    _check_owned_directory(home)
    directory = home / ".local-access"
    _check_owned_directory(directory)
    if principal is None:
        if os.name != "posix":
            raise AccountBootstrapError("Production account identity requires a Linux execution user")
        principal = str(os.geteuid())
    fingerprint = hashlib.sha256(
        (os.path.normcase(str(home.resolve())) + "|" + principal).encode("utf-8")
    ).hexdigest()
    identity = {"version": 2, "account_id": account_id,
                "home_fingerprint": fingerprint, "instance_id": instance_id,
                "access_token": access_token}
    path = directory / "identity.json"
    if not os.path.lexists(path):
        descriptor, temporary_name = tempfile.mkstemp(prefix="bootstrap-", dir=directory)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(identity, stream)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise AccountBootstrapError("Account identity must be a regular private file")
        if os.name == "posix" and info.st_uid != os.geteuid():
            raise AccountBootstrapError("Account identity is owned by another execution user")
        existing = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(existing, dict)
                or any(existing.get(key) != identity[key]
                       for key in ("version", "account_id", "home_fingerprint", "instance_id"))
                or not isinstance(existing.get("access_token"), str)
                or not secrets.compare_digest(existing["access_token"], access_token)):
            raise AccountBootstrapError("Existing profile identity differs; it was preserved without replacement")
        if os.name == "posix":
            path.chmod(0o600)
    except (OSError, ValueError, TypeError) as exc:
        raise AccountBootstrapError("Existing profile identity cannot be verified; it was preserved") from exc
