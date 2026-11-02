"""Application file APIs must not cross local-account profile boundaries."""
from __future__ import annotations

import ast
import os
from pathlib import Path
import uuid

import pytest

from matcreator.local_account_paths import INTERNAL_ACCOUNT_ENV_KEYS, assert_local_account_path


@pytest.fixture
def profile(monkeypatch, tmp_path):
    root = tmp_path / "installation"
    account_id = str(uuid.uuid4())
    home = root / "profiles" / account_id
    home.mkdir(parents=True)
    monkeypatch.setenv("MATCREATOR_LOCAL_PROFILE_WORKER", "1")
    monkeypatch.setenv("MATCREATOR_LOCAL_ACCOUNT_ROOT", str(root))
    monkeypatch.setenv("MATCREATOR_LOCAL_ACCOUNT_ID", account_id)
    monkeypatch.setenv("MATCREATOR_HOME", str(home))
    return root, account_id, home


def test_own_profile_remains_usable_but_external_projects_need_explicit_grants(profile, tmp_path):
    root, account_id, home = profile
    assert assert_local_account_path(home / "workspace" / "trajectory.extxyz") == home / "workspace" / "trajectory.extxyz"
    project = tmp_path / "research-project" / "input.vasp"
    with pytest.raises(PermissionError, match="outside this local account"):
        assert_local_account_path(project)


@pytest.mark.parametrize("selection", ["broker-auth", "broker-token", "other-profile", "other-parent", "profile-parent", "own-token", "root", "legacy-config", "legacy-db"])
def test_profile_cannot_select_other_accounts_or_management_state(profile, selection):
    root, account_id, home = profile
    paths = {
        "broker-auth": root / ".local-accounts" / "accounts.sqlite",
        "broker-token": root / ".local-access" / "identity.json",
        "other-profile": root / "profiles" / str(uuid.uuid4()) / "config.yaml",
        "other-parent": root.parent,
        "profile-parent": root / "profiles",
        "own-token": home / ".local-access" / "identity.json",
        "root": root,
        "legacy-config": root / "config.yaml",
        "legacy-db": root / ".adk" / "session.db",
    }
    with pytest.raises(PermissionError):
        assert_local_account_path(paths[selection])


def test_legacy_claimed_root_does_not_grant_access_to_nested_profiles(profile, monkeypatch):
    root, account_id, home = profile
    monkeypatch.setenv("MATCREATOR_HOME", str(root))
    assert assert_local_account_path(root / "workspace" / "own.txt") == root / "workspace" / "own.txt"
    with pytest.raises(PermissionError):
        assert_local_account_path(root / "profiles" / str(uuid.uuid4()) / "private.txt")


def test_new_profile_cannot_select_a_legacy_workspace_outside_broker_home(profile, tmp_path):
    root, account_id, home = profile
    legacy_workspace = tmp_path / "legacy-workspace"
    legacy_workspace.mkdir()
    private_file = legacy_workspace / "existing-record.txt"
    private_file.write_text("synthetic legacy owner data", encoding="utf-8")
    for candidate in (legacy_workspace, private_file, legacy_workspace / ".." / "legacy-workspace"):
        with pytest.raises(PermissionError, match="outside this local account"):
            assert_local_account_path(candidate)


def test_legacy_account_retains_its_preexisting_external_workspace(profile, monkeypatch, tmp_path):
    root, account_id, home = profile
    monkeypatch.setenv("MATCREATOR_HOME", str(root))
    legacy_workspace = tmp_path / "legacy-workspace"
    assert assert_local_account_path(legacy_workspace / "existing.txt") == legacy_workspace / "existing.txt"


def test_symlink_to_foreign_profile_cannot_bypass_path_guard(profile):
    root, account_id, home = profile
    foreign = root / "profiles" / str(uuid.uuid4())
    foreign.mkdir()
    link = home / "workspace-link"
    try:
        link.symlink_to(foreign, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("This OS user cannot create symbolic links")
    with pytest.raises(PermissionError):
        assert_local_account_path(link / "private.txt")


@pytest.mark.parametrize("bad_id", ["", "../other", "admin", "USER", "1234"])
def test_profile_marker_requires_a_verified_stable_identity(profile, monkeypatch, bad_id):
    root, account_id, home = profile
    monkeypatch.setenv("MATCREATOR_LOCAL_ACCOUNT_ID", bad_id)
    with pytest.raises(PermissionError, match="verified account identity"):
        assert_local_account_path(home / "workspace")


def test_single_user_legacy_mode_does_not_change_arbitrary_path_resolution(monkeypatch, tmp_path):
    monkeypatch.delenv("MATCREATOR_LOCAL_PROFILE_WORKER", raising=False)
    assert assert_local_account_path(tmp_path) == tmp_path


def test_configuration_cannot_override_internal_account_runtime_markers():
    root = Path(__file__).resolve().parents[1]
    for file in (root / "src/matcreator/config.py", root / "src/matcreator/constants.py", root / "web/main.py"):
        tree = ast.parse(file.read_text(encoding="utf-8"))
        assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == "_PROTECTED_USER_ENV_KEYS" for target in node.targets))
        keys = ast.literal_eval(assignment.value.args[0])
        assert INTERNAL_ACCOUNT_ENV_KEYS <= keys, str(file)
