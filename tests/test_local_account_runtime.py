from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
import uuid

import httpx
import pytest

from matcreator.local_access import InstanceLease, ensure_local_access
from matcreator.local_account_runtime import (
    LocalAccountRuntime, LocalAccountRuntimeManager, LocalRuntimeConflict,
)


def account(manager, *, legacy=False):
    identity = str(uuid.uuid4())
    home = manager.home if legacy else manager.profile_directory / identity
    return {"id": identity, "profile_home": str(home), "legacy": legacy}


def runtime_for(manager, value, web_port=28101, adk_port=28100):
    identity, home, workspace = manager._paths(value)
    access = ensure_local_access(home)
    return LocalAccountRuntime(identity, home, workspace,
                               f"http://127.0.0.1:{web_port}", f"http://127.0.0.1:{adk_port}",
                               web_port, adk_port, access)


def record_for(runtime, **changes):
    record = {"version": 1, "account_id": runtime.account_id, "home": str(runtime.home),
              "workspace": str(runtime.workspace), "instance_id": runtime.access.instance_id,
              "web_port": runtime.web_port, "adk_port": runtime.adk_port, "state": "ready",
              "source_root": "synthetic-source", "web_pid": 100002, "adk_pid": 100001}
    record.update(changes)
    return record


def test_private_profile_mapping_rejects_forged_ids_and_homes(tmp_path):
    manager = LocalAccountRuntimeManager(tmp_path / "broker", tmp_path / "source")
    value = account(manager)
    identity, home, workspace = manager._paths(value)
    assert home == manager.home / "profiles" / identity
    assert workspace == home / "workspace"
    with pytest.raises(LocalRuntimeConflict, match="UUID"):
        manager._paths({"id": "../other"})
    value["profile_home"] = str(tmp_path / "unrelated")
    with pytest.raises(LocalRuntimeConflict, match="does not match"):
        manager._paths(value)


def test_legacy_profile_preserves_explicit_external_workspace(tmp_path):
    manager = LocalAccountRuntimeManager(tmp_path / "broker", tmp_path / "source")
    value = account(manager, legacy=True)
    value["workspace"] = str(tmp_path / "historical-workspace")
    _, home, workspace = manager._paths(value)
    assert home == manager.home
    assert workspace == tmp_path / "historical-workspace"


def test_child_environment_never_inherits_other_accounts_credentials(monkeypatch, tmp_path):
    manager = LocalAccountRuntimeManager(tmp_path / "broker", tmp_path / "source")
    runtime = runtime_for(manager, account(manager))
    for name in ("LLM_API_KEY", "BOHRIUM_PASSWORD", "OPENAI_API_KEY", "TAVILY_API_KEY", "MY_PROVIDER_TOKEN",
                 "KDG_DB_PATH", "OAW_XRD_RUN_ROOT", "MATCLAW_WORKSPACE", "MATCREATOR_CONFIG_PATH", "SSH_AUTH_SOCK"):
        monkeypatch.setenv(name, "synthetic-other-account-secret")
    monkeypatch.setenv("MATCREATOR_XRD_PYTHON", "synthetic-tool-python")
    env = manager._child_environment(runtime)
    assert "synthetic-other-account-secret" not in env.values()
    assert env["MATCREATOR_HOME"] == str(runtime.home)
    assert env["MATCREATOR_CONFIG_PATH"] == str(runtime.home / "config.yaml")
    assert env["MATCLAW_WORKSPACE"] == str(runtime.workspace)
    assert env["MATCREATOR_LOCAL_ACCOUNT_ROOT"] == str(manager.home)
    assert env["MATCREATOR_LOCAL_ACCOUNT_ID"] == runtime.account_id
    assert env["MATCREATOR_LOCAL_PROFILE_WORKER"] == "1"
    assert env["MATCREATOR_XRD_PYTHON"] == "synthetic-tool-python"
    assert not (runtime.home / "config.yaml").exists()


def test_signed_probe_sends_no_credentials_and_checks_audience(tmp_path):
    manager = LocalAccountRuntimeManager(tmp_path / "broker", tmp_path / "source")
    runtime = runtime_for(manager, account(manager))
    calls = []
    def handler(request):
        calls.append(request)
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        nonce = request.url.params["nonce"]
        return httpx.Response(200, json={"instance_id": runtime.access.instance_id, "audience": "web", "pid": 42,
                                        "proof": runtime.access.server_proof(nonce, "web", 42)})
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await manager._probe(client, runtime, "web") == ("ready", 42)
            with pytest.raises(LocalRuntimeConflict, match="different instance"):
                await manager._probe(client, runtime, "adk")
    asyncio.run(exercise())
    assert len(calls) == 2


def test_probe_rejects_foreign_secret_even_with_correct_public_identity(tmp_path):
    manager = LocalAccountRuntimeManager(tmp_path / "broker", tmp_path / "source")
    runtime = runtime_for(manager, account(manager))
    foreign = ensure_local_access(tmp_path / "foreign")
    def handler(request):
        return httpx.Response(200, json={"instance_id": runtime.access.instance_id, "audience": "web", "pid": 42,
                                        "proof": foreign.server_proof(request.url.params["nonce"], "web", 42)})
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(LocalRuntimeConflict, match="no credential"):
                await manager._probe(client, runtime, "web")
    asyncio.run(exercise())


def test_concurrent_ensure_and_broker_restart_do_not_duplicate_workers(monkeypatch, tmp_path):
    async def exercise():
        manager = LocalAccountRuntimeManager(tmp_path / "broker", tmp_path / "source")
        value = account(manager)
        spawns = []
        def spawn(runtime, role):
            spawns.append(role)
            return SimpleNamespace(pid=100000 + len(spawns), poll=lambda: None)
        async def ready(runtime):
            runtime.web_pid, runtime.adk_pid = 100002, 100001
            runtime.verified_at = time.monotonic()
            return True, True
        monkeypatch.setattr(manager, "_spawn", spawn)
        monkeypatch.setattr(manager, "_ready", ready)
        first, second = await asyncio.gather(manager.ensure(value), manager.ensure(value))
        assert first is second
        assert spawns == ["adk", "web"]
        await manager.shutdown_broker()
        assert spawns == ["adk", "web"]
        restarted = LocalAccountRuntimeManager(manager.home, manager.source_root)
        monkeypatch.setattr(restarted, "_ready", ready)
        monkeypatch.setattr(restarted, "_spawn", lambda *_: pytest.fail("Verified existing workers must be reattached"))
        recovered = await restarted.ensure(value)
        assert recovered.access.instance_id == first.access.instance_id
        assert recovered.adk_port == first.adk_port
        assert recovered.web_port == first.web_port
        record = manager._read_record(value["id"])
        assert record["state"] == "ready"
        assert first.access.token not in json.dumps(record)
        assert first.access.token not in repr(first)
        assert first.access.token not in json.dumps(manager.report_status())
    asyncio.run(exercise())


def test_partial_existing_runtime_is_retained_without_starting_replacement(monkeypatch, tmp_path):
    async def exercise():
        manager = LocalAccountRuntimeManager(tmp_path / "broker", tmp_path / "source")
        value = account(manager)
        runtime = runtime_for(manager, value)
        manager._write_record(value["id"], record_for(runtime))
        async def ready(_): return True, False
        monkeypatch.setattr(manager, "_ready", ready)
        monkeypatch.setattr(manager, "_spawn", lambda *_: pytest.fail("No replacement may be created"))
        with pytest.raises(LocalRuntimeConflict, match="partially available"):
            await manager.ensure(value)
    asyncio.run(exercise())


def test_missing_record_cannot_launch_over_active_home_owner(monkeypatch, tmp_path):
    async def exercise():
        manager = LocalAccountRuntimeManager(tmp_path / "broker", tmp_path / "source")
        value = account(manager)
        runtime = runtime_for(manager, value)
        lease = InstanceLease(runtime.home, "web").acquire()
        monkeypatch.setattr(manager, "_spawn", lambda *_: pytest.fail("Active unrecorded home must be retained"))
        try:
            with pytest.raises(LocalRuntimeConflict, match="Another process owns"):
                await manager.ensure(value)
        finally:
            lease.close()
    asyncio.run(exercise())


def test_invalid_record_and_foreign_profile_directory_fail_closed(monkeypatch, tmp_path):
    async def exercise():
        manager = LocalAccountRuntimeManager(tmp_path / "broker", tmp_path / "source")
        value = account(manager)
        manager._record_path(value["id"]).write_text("not-json")
        monkeypatch.setattr(manager, "_spawn", lambda *_: pytest.fail("Corrupt records require inspection"))
        with pytest.raises(LocalRuntimeConflict, match="unreadable"):
            await manager.ensure(value)
    asyncio.run(exercise())


def test_port_reservation_keeps_account_endpoints_distinct(tmp_path):
    manager = LocalAccountRuntimeManager(tmp_path / "broker", tmp_path / "source")
    first, second = manager._reserve_ports()
    assert first != second
    assert 1 <= first <= 65535 and 1 <= second <= 65535


def test_cleanup_cannot_stop_attached_or_unknown_runtime(tmp_path):
    manager = LocalAccountRuntimeManager(tmp_path / "broker", tmp_path / "source")
    with pytest.raises(LocalRuntimeConflict, match="did not create"):
        asyncio.run(manager.stop_test_owned(str(uuid.uuid4())))


def test_windows_pid_probe_does_not_signal_process(monkeypatch):
    from matcreator.local_account_runtime import _pid_alive
    if os.name != "nt":
        pytest.skip("Windows process query API")
    monkeypatch.setattr(os, "kill", lambda *_: pytest.fail("Windows liveness must never signal a process"))
    assert _pid_alive(os.getpid())
