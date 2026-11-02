"""Private profile identity survives restarts and refuses foreign ownership."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import uuid

import pytest

from matcreator.scripts import local_account_identity as entry


def identity():
    return {"account_id": str(uuid.uuid4()), "instance_id": secrets.token_hex(16),
            "access_token": secrets.token_urlsafe(32)}


def principal():
    if os.name == "posix":
        return str(os.geteuid())
    from matcreator.local_access import _windows_user_sid
    return _windows_user_sid()


def test_bootstrap_identity_is_accepted_by_real_worker_and_survives_restart(tmp_path):
    from matcreator.local_access import ensure_local_access
    value = identity()
    home = tmp_path / "profile"
    entry.prepare_identity(home, **value, principal=principal())
    user_file = home / "workspace" / "retained.txt"
    user_file.parent.mkdir()
    user_file.write_text("retained personal input")
    first = ensure_local_access(home)
    entry.prepare_identity(home, **value, principal=principal())
    second = ensure_local_access(home)
    assert first.instance_id == second.instance_id == value["instance_id"]
    assert second.valid_token(value["access_token"])
    assert user_file.read_text() == "retained personal input"
    if os.name == "posix":
        assert (home / ".local-access" / "identity.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("changed", ["account_id", "instance_id", "access_token"])
def test_another_account_or_secret_cannot_take_over_an_existing_volume(tmp_path, changed):
    original, other = identity(), identity()
    home = tmp_path / "profile"
    entry.prepare_identity(home, **original, principal=principal())
    path = home / ".local-access" / "identity.json"
    before = path.read_bytes()
    altered = {**original, changed: other[changed]}
    with pytest.raises(entry.AccountBootstrapError, match="preserved"):
        entry.prepare_identity(home, **altered, principal=principal())
    assert path.read_bytes() == before


def test_corrupt_identity_is_never_overwritten(tmp_path):
    home = tmp_path / "profile"
    directory = home / ".local-access"
    directory.mkdir(parents=True)
    path = directory / "identity.json"
    path.write_text("not a recoverable identity")
    with pytest.raises(entry.AccountBootstrapError, match="preserved"):
        entry.prepare_identity(home, **identity(), principal=principal())
    assert path.read_text() == "not a recoverable identity"


@pytest.mark.parametrize("changed,value", [
    ("account_id", "../../another-account"), ("account_id", ""),
    ("instance_id", "wrong-instance"), ("access_token", "short"),
])
def test_invalid_provisioning_fails_before_creating_storage(tmp_path, changed, value):
    home = tmp_path / "profile"
    supplied = {**identity(), changed: value}
    with pytest.raises(entry.AccountBootstrapError):
        entry.prepare_identity(home, **supplied, principal=principal())
    assert not home.exists()


@pytest.mark.parametrize("redirect_profile", [False, True])
def test_profile_and_identity_directory_symlinks_are_rejected(tmp_path, redirect_profile):
    home, outside = tmp_path / "profile", tmp_path / "outside"
    outside.mkdir()
    try:
        if redirect_profile:
            home.symlink_to(outside, target_is_directory=True)
        else:
            home.mkdir()
            (home / ".local-access").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires local OS permission")
    with pytest.raises(entry.AccountBootstrapError, match="symbolic"):
        entry.prepare_identity(home, **identity(), principal=principal())
    assert list(outside.iterdir()) == []


def test_concurrent_bootstrap_cannot_publish_partial_or_different_credentials(tmp_path):
    home, supplied = tmp_path / "profile", identity()
    owner = principal()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [pool.submit(entry.prepare_identity, home, **supplied, principal=owner) for _ in range(8)]
        for result in results:
            result.result()
    value = json.loads((home / ".local-access" / "identity.json").read_text())
    assert all(value[key] == supplied[key] for key in supplied)
    assert not list((home / ".local-access").glob("bootstrap-*"))
