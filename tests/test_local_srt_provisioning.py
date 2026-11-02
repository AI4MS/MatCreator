"""First-install contracts, using disposable paths and no actual provisioning."""
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "script/provision_local_srt.py"
spec = importlib.util.spec_from_file_location("srt_provisioning_test", SCRIPT)
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


@pytest.fixture
def installation(monkeypatch, tmp_path):
    source = tmp_path / "source"
    for name in ("src/matcreator/example.py", "web/main.py", "pyproject.toml", "README.md"):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# public source\n")
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(provision, "ROOT", runtime)
    monkeypatch.setattr(provision, "require_admin", lambda: None)
    # Windows cannot model Linux UID/mode ancestry; exercise these rules below
    # through stat fixtures instead of granting anything on the actual host.
    monkeypatch.setattr(provision, "trusted_path", lambda *args, **kwargs: None)
    monkeypatch.setattr(provision, "safe_entry", lambda info: None)
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if "venv" in argv:
            python = Path(argv[-1]) / "bin/python"
            python.parent.mkdir(parents=True)
            python.write_text("# fake interpreter, never executed\n")
        else:
            assert argv[-3:] == ["pip", "freeze", "--all"]
        return SimpleNamespace(stdout="example==1.0\n", returncode=0)
    monkeypatch.setattr(provision.subprocess, "run", run)
    return source, runtime, calls


def test_prepare_copies_only_public_allowlist_and_never_installs_packages(installation):
    source, runtime, calls = installation
    for name in ("src/matcreator/.env", "src/matcreator/session.sqlite", "src/matcreator/private/secret.py",
                 "src/matcreator/__pycache__/cache.pyc", "src/matcreator.egg-info/PKG-INFO",
                 "web/users.db", "web/nested/not-public.py", ".local-review-backup/real.db", "config.yaml"):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("must never be staged")
    result = provision.prepare(source)
    files = {path.relative_to(runtime / "app").as_posix() for path in (runtime / "app").rglob("*") if path.is_file()}
    assert files == {"src/matcreator/example.py", "web/main.py", "pyproject.toml", "README.md"}
    assert result["dependencies_installed"] is False
    assert len(calls) == 1 and calls[0][0][1:4] == ["-I", "-m", "venv"]
    assert (runtime / "bin/bwrap").read_bytes() == provision.WRAPPER
    assert not (runtime / "manifest.json").exists()


def test_existing_runtime_is_never_overwritten(installation):
    source, runtime, calls = installation
    runtime.mkdir()
    marker = runtime / "retain.txt"
    marker.write_text("existing data")
    with pytest.raises(RuntimeError, match="already exists"):
        provision.prepare(source)
    assert marker.read_text() == "existing data" and not calls


def test_finalize_pins_exact_bytes_and_only_known_public_metadata(installation):
    source, runtime, calls = installation
    provision.prepare(source)
    metadata = runtime / "app/src/matcreator.egg-info/PKG-INFO"
    metadata.parent.mkdir()
    metadata.write_text("Name: matcreator\n")
    result = provision.finalize()
    raw = (runtime / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    assert raw == provision.canonical(manifest)
    assert manifest["source_sha256"] == hashlib.sha256(provision.canonical(manifest["files"])).hexdigest()
    assert result["runtime_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["runtime_sha256"] != result["source_hash"]
    assert manifest["files"]["app/src/matcreator.egg-info/PKG-INFO"] == provision.sha(metadata)
    assert "dependencies.freeze.txt" in manifest["files"] and result["unit_started"] is False
    assert len(calls) == 2  # Only local venv creation and local pip freeze.
    with pytest.raises(RuntimeError, match="finalized runtime"):
        provision.finalize()
    assert (runtime / "manifest.json").read_bytes() == raw


@pytest.mark.parametrize("alteration", ["changed", "missing", "unexpected", "metadata-secret", "wrapper"])
def test_finalize_refuses_unsafe_or_changed_public_snapshot(installation, alteration):
    source, runtime, _calls = installation
    provision.prepare(source)
    path = runtime / "app/src/matcreator/example.py"
    if alteration == "changed":
        path.write_text("changed after prepare")
    elif alteration == "missing":
        path.unlink()
    elif alteration == "unexpected":
        (runtime / "app/src/private.db").write_text("private")
    elif alteration == "metadata-secret":
        path = runtime / "app/src/matcreator.egg-info/password.txt"
        path.parent.mkdir()
        path.write_text("private")
    else:
        (runtime / "bin/bwrap").write_text("#!/bin/sh\nexit 0\n")
    with pytest.raises(RuntimeError):
        provision.finalize()
    assert not (runtime / "manifest.json").exists()


@pytest.mark.parametrize("uid,mode", [(1000, stat.S_IFREG | 0o644), (0, stat.S_IFREG | 0o664),
                                      (0, stat.S_IFDIR | 0o777), (0, stat.S_IFIFO | 0o600)])
def test_linux_ownership_and_writability_rules(uid, mode):
    with pytest.raises(RuntimeError):
        # Use the original function, unaffected by the installation fixture.
        provision.safe_entry(SimpleNamespace(st_uid=uid, st_mode=mode))


def test_linux_admin_is_required(monkeypatch):
    monkeypatch.setattr(provision.sys, "platform", "win32")
    with pytest.raises(RuntimeError, match="Linux administrator"):
        provision.require_admin()
    monkeypatch.setattr(provision.sys, "platform", "linux")
    monkeypatch.setattr(provision.os, "geteuid", lambda: 1000, raising=False)
    with pytest.raises(RuntimeError, match="Linux administrator"):
        provision.require_admin()


def test_public_symlinks_are_refused_before_installation(installation):
    source, runtime, calls = installation
    link = source / "src/external.py"
    try:
        link.symlink_to(source / "README.md")
    except OSError:
        pytest.skip("Creating a symbolic link requires host permission")
    with pytest.raises(RuntimeError, match="symbolic links"):
        provision.prepare(source)
    assert not runtime.exists() and not calls
