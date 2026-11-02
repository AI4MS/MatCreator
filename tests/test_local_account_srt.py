"""Account/controller contract checks independent of Docker and real user data."""
from __future__ import annotations

import asyncio
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import httpx
import pytest

from matcreator.local_account_runtime import LocalRuntimeConflict
import matcreator.local_account_srt as module
from matcreator.local_account_srt import CAPABILITIES, RUNTIME_ROOT, STATE_ROOT, SrtAccountRuntimeManager, SrtController


class FakeController:
    def __init__(self):
        self.calls, self.workers = [], {}
        self.source_hash, self.runtime_sha256 = "a" * 64, "b" * 64
        self.capabilities = {key: True for key in CAPABILITIES}
        self.created = self.stopped = 0
        self.mutation = None
        self.zero_port_start = False

    def request(self, payload, *, timeout=60):
        self.calls.append(copy.deepcopy(payload))
        action = payload["action"]
        if action == "preflight":
            return {"version": 1, "ok": True, "backend": "srt", "runtime_root": RUNTIME_ROOT,
                    "state_root": STATE_ROOT, "source_hash": self.source_hash,
                    "runtime_sha256": self.runtime_sha256, "capabilities": dict(self.capabilities)}
        aid = payload["account_id"]
        if action == "stop_test_owned":
            assert payload["ownership_receipt"] == self.workers[aid]["ownership_receipt"]
            self.workers[aid]["state"] = "stopped"
            self.stopped += 1
            return {"version": 1, "ok": True, "state": "stopped", "cleanup_confirmed": True, "data_retained": True}
        if action == "status":
            if aid not in self.workers:
                return {"version": 1, "ok": True, "state": "absent"}
            response = copy.deepcopy(self.workers[aid])
            response["created"] = False
            if self.mutation:
                response.update(self.mutation)
            return response
        assert action == "ensure"
        if aid in self.workers:
            pytest.fail("An active or stopped worker must not be re-created")
        self.created += 1
        account_root = STATE_ROOT + "/" + payload["installation_id"] + "/" + aid
        response = {"version": 1, "ok": True, "backend": "srt", "installation_id": payload["installation_id"],
                    "account_id": aid, "instance_id": payload["instance_id"], "state": "starting",
                    "unit_name": "mc-srt-" + payload["installation_id"] + "-" + aid + ".service",
                    "linux_user": "mcsrt" + str(self.created), "profile_home": account_root + "/home",
                    "control_dir": account_root + "/control", "web_port": 29000 + self.created,
                    "main_pid": 20000 + self.created, "main_starttime": str(123000 + self.created),
                    "boot_id": "99c6e34a-a4f9-4e9c-9f2b-7bf7a760defa", "source_hash": self.source_hash,
                    "runtime_sha256": self.runtime_sha256, "policy_hash": "c" * 64, "spec_hash": "d" * 64,
                    "ingress_kind": "stdio-mux-v1",
                    "uid": 21000 + self.created, "gid": 21000 + self.created, "gecos": "Synthetic MC SRT account",
                    "unit_invocation_id": str(self.created) * 32, "created": True}
        response["ownership_receipt"] = {key: response[key] for key in (
            "version", "installation_id", "account_id", "instance_id", "unit_name", "linux_user", "uid", "gid", "gecos",
            "profile_home", "main_pid", "main_starttime", "boot_id", "source_hash", "runtime_sha256", "policy_hash", "spec_hash", "unit_invocation_id")}
        response["ownership_receipt"]["ownership_id"] = "f" * 64
        self.workers[aid] = copy.deepcopy(response)
        if self.zero_port_start:
            response["web_port"] = 0
        return response


@pytest.fixture
def setup(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("MATCREATOR_LOCAL_SRT_"):
            monkeypatch.delenv(key, raising=False)
    controller = FakeController()
    manager = SrtAccountRuntimeManager(tmp_path / "broker", tmp_path / "source", controller=controller)
    async def ready(runtime):
        runtime.web_pid, runtime.adk_pid = 7, 8  # namespace PIDs, not host PIDs
        runtime.verified_at = time.monotonic()
        return True, True
    monkeypatch.setattr(manager, "_ready", ready)
    def account():
        aid = str(uuid.uuid4())
        profile = manager.profile_directory / aid
        profile.mkdir()
        return {"id": aid, "profile_home": str(profile), "legacy": False}
    return manager, controller, account, ready


def test_two_accounts_have_separate_identity_and_stable_restart_without_docker(setup, monkeypatch):
    manager, controller, account, ready = setup
    first, second = account(), account()
    async def run():
        a, b = await asyncio.gather(manager.ensure(first), manager.ensure(second))
        assert a.account_id != b.account_id and a.web_port != b.web_port
        assert a.access.instance_id != b.access.instance_id and a.access.token != b.access.token
        assert a.adk_port == 8000 and a.adk_url == a.web_url
        assert controller.created == 2
        await manager.shutdown_broker()
        restarted = SrtAccountRuntimeManager(manager.home, manager.source_root, controller=controller)
        monkeypatch.setattr(restarted, "_ready", ready)
        restored = await restarted.ensure(first)
        assert restored.access.instance_id == a.access.instance_id and restored.web_port == a.web_port
        assert controller.created == 2
        assert restarted._owned == {}
        with pytest.raises(LocalRuntimeConflict, match="did not create"):
            await restarted.stop_test_owned(first["id"])
        safe = json.dumps(manager.report_status())
        records = "".join(path.read_text() for path in manager.record_directory.glob("*.json"))
        for secret in (a.access.token, b.access.token):
            assert secret not in safe + records + repr(a) + repr(b)
        assert all("access_token" not in call for call in controller.calls if call["action"] != "ensure")
        await manager.stop_test_owned(first["id"])
        assert controller.stopped == 1 and manager._read_record(first["id"])["data_retained"] is True
        assert controller.workers[second["id"]]["state"] == "starting"
    asyncio.run(run())


def test_concurrent_ensure_creates_once(setup):
    manager, controller, account, _ = setup
    value = account()
    async def run():
        a, b = await asyncio.gather(manager.ensure(value), manager.ensure(value))
        assert a is b and controller.created == 1
    asyncio.run(run())


def test_startup_without_ingress_persists_identity_then_pins_ready_port(setup, monkeypatch):
    manager, controller, account, ready = setup
    target = account()
    controller.zero_port_start = True
    async def verified(runtime):
        assert runtime.web_port > 0
        assert target["id"] in manager._owned
        persisted = manager._read_record(target["id"])
        assert persisted["main_pid"] == controller.workers[target["id"]]["main_pid"]
        assert persisted["web_port"] == runtime.web_port
        return await ready(runtime)
    monkeypatch.setattr(manager, "_ready", verified)
    runtime = asyncio.run(manager.ensure(target))
    assert manager._read_record(target["id"])["web_port"] == runtime.web_port
    assert controller.created == 1


def test_startup_without_ingress_can_be_stopped_after_timeout(setup, monkeypatch):
    manager, controller, account, _ = setup
    target = account()
    controller.zero_port_start = True
    manager.startup_timeout = 0
    async def unexpected_probe(_):
        pytest.fail("Port zero must never be probed")
    monkeypatch.setattr(manager, "_ready", unexpected_probe)
    with pytest.raises(LocalRuntimeConflict, match="readiness"):
        asyncio.run(manager.ensure(target))
    assert target["id"] in manager._owned
    asyncio.run(manager.stop_test_owned(target["id"]))
    assert controller.stopped == 1


@pytest.mark.parametrize("key,value", [
    ("main_pid", 999999), ("main_starttime", "990"), ("boot_id", str(uuid.uuid4())),
    ("unit_name", "foreign.service"), ("linux_user", "foreign"), ("web_port", 29999),
    ("profile_home", "/etc"), ("control_dir", "/tmp/foreign"), ("instance_id", "1" * 32),
    ("account_id", str(uuid.uuid4())), ("installation_id", "0" * 32),
    ("source_hash", "e" * 64), ("runtime_sha256", "e" * 64),
    ("policy_hash", "e" * 64), ("spec_hash", "e" * 64),
    ("state", "stopped"), ("unit_invocation_id", "e" * 32), ("ingress_kind", "host-fd-legacy"),
])
def test_changed_runtime_identity_is_retained_without_replacement(setup, key, value):
    manager, controller, account, _ = setup
    target = account()
    async def run():
        original = await manager.ensure(target)
        original.verified_at = 0
        controller.mutation = {key: value}
        with pytest.raises(LocalRuntimeConflict):
            await manager.ensure(target)
        assert controller.created == 1 and controller.stopped == 0
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["legacy", "nonempty", "process", "container"])
def test_existing_data_and_other_backends_fail_closed(setup, kind):
    manager, controller, account, _ = setup
    target = account()
    if kind == "legacy":
        target.update(legacy=True, profile_home=str(manager.home))
    elif kind == "nonempty":
        (Path(target["profile_home"]) / "user-data.txt").write_text("preserve")
    else:
        manager._write_record(target["id"], {"version": 1, "account_id": target["id"], "backend": kind})
    with pytest.raises(LocalRuntimeConflict):
        asyncio.run(manager.ensure(target))
    assert not controller.calls and controller.created == 0


@pytest.mark.parametrize("capability", sorted(CAPABILITIES))
def test_missing_required_capability_fails_before_spawn(setup, capability):
    manager, controller, account, _ = setup
    controller.capabilities[capability] = False
    with pytest.raises(LocalRuntimeConflict, match="preflight"):
        asyncio.run(manager.ensure(account()))
    assert controller.created == 0


def test_changed_admin_configuration_cannot_move_existing_worker(setup, monkeypatch):
    manager, controller, account, ready = setup
    target = account()
    asyncio.run(manager.ensure(target))
    monkeypatch.setenv("MATCREATOR_LOCAL_SRT_CPUS", "2")
    changed = SrtAccountRuntimeManager(manager.home, manager.source_root, controller=controller)
    monkeypatch.setattr(changed, "_ready", ready)
    with pytest.raises(LocalRuntimeConflict, match="configuration"):
        asyncio.run(changed.ensure(target))
    assert controller.created == 1


def test_readiness_timeout_keeps_created_unit_for_exact_test_cleanup(setup, monkeypatch):
    manager, controller, account, _ = setup
    target = account()
    manager.startup_timeout = 0
    async def not_ready(_): return False, False
    monkeypatch.setattr(manager, "_ready", not_ready)
    with pytest.raises(LocalRuntimeConflict, match="readiness"):
        asyncio.run(manager.ensure(target))
    assert controller.created == 1 and target["id"] in manager._owned
    asyncio.run(manager.stop_test_owned(target["id"]))
    assert controller.stopped == 1


def test_namespace_adk_probe_uses_same_external_web_port_and_checks_role(setup):
    manager, _, account, _ = setup
    runtime = asyncio.run(manager.ensure(account()))
    seen = []
    def reply(request):
        seen.append(request)
        assert "authorization" not in request.headers and "cookie" not in request.headers
        role = "adk" if request.url.path.endswith("adk-status") else "web"
        return httpx.Response(200, json={"instance_id": runtime.access.instance_id, "pid": 23, "audience": role,
            "proof": runtime.access.server_proof(request.url.params["nonce"], role, 23)})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            assert await manager._probe(client, runtime, "web") == ("ready", 23)
            assert await manager._probe(client, runtime, "adk") == ("ready", 23)
    asyncio.run(run())
    assert [request.url.path for request in seen] == ["/api/local-auth/status", "/api/local-auth/adk-status"]
    assert all(request.url.port == runtime.web_port for request in seen)


def test_wrong_signature_is_not_retried_as_a_transport(setup):
    manager, _, account, _ = setup
    runtime = asyncio.run(manager.ensure(account()))
    def reply(_):
        return httpx.Response(200, json={"instance_id": runtime.access.instance_id, "pid": 23,
                                         "audience": "adk", "proof": "0" * 64})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            with pytest.raises(LocalRuntimeConflict, match="signature"):
                await manager._probe(client, runtime, "adk")
    asyncio.run(run())


@pytest.mark.parametrize("raw", ['[]', '{"not-uuid":"1"}', '{"'+str(uuid.uuid4())+'":"-1"}',
                               '{"'+str(uuid.uuid4())+'":"3-1"}', '{"'+str(uuid.uuid4())+'":"65536"}'])
def test_invalid_cpu_allocations_rejected(monkeypatch, tmp_path, raw):
    monkeypatch.setenv("MATCREATOR_LOCAL_SRT_CPUSETS", raw)
    with pytest.raises(LocalRuntimeConflict):
        SrtAccountRuntimeManager(tmp_path / "broker", tmp_path, controller=FakeController())


def test_cpu_ranges_nonoverlap_unassigned_and_normalization(monkeypatch, tmp_path):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    monkeypatch.setenv("MATCREATOR_LOCAL_SRT_CPUSETS", json.dumps({a: "2,2,3", b: "3"}))
    with pytest.raises(LocalRuntimeConflict, match="non-overlapping"):
        SrtAccountRuntimeManager(tmp_path / "bad", tmp_path, controller=FakeController())
    monkeypatch.setenv("MATCREATOR_LOCAL_SRT_CPUSETS", json.dumps({a: "2,2,3", b: "4"}))
    manager = SrtAccountRuntimeManager(tmp_path / "good", tmp_path, controller=FakeController())
    assert manager.cpu_sets[a] == "2-3"
    manager.preflight()
    with pytest.raises(LocalRuntimeConflict, match="no administrator CPU"):
        manager._spec(str(uuid.uuid4()), module.SrtCredential("a" * 32, "b" * 43), "status")


def test_controller_sends_secrets_only_on_stdin_and_filters_host_environment(monkeypatch):
    transport = SrtController(["wsl.exe", "-d", "Ubuntu-24.04", "--"])
    monkeypatch.setenv("BOHRIUM_PASSWORD", "host-password-must-not-pass")
    monkeypatch.setenv("OPENAI_API_KEY", "host-key-must-not-pass")
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda path: b"print('trusted helper')" if path.name == "local_srt_host.py" else original(path))
    seen = []
    def run(arguments, **kwargs):
        seen.append((arguments, kwargs))
        return subprocess.CompletedProcess(arguments, 0, json.dumps({"version": 1, "ok": True}), "")
    monkeypatch.setattr(module.subprocess, "run", run)
    secret = "synthetic-worker-secret-123"
    transport.request({"version": 1, "action": "ensure", "access_token": secret})
    args, options = seen[0]
    assert secret not in repr(args)
    assert secret in options["input"]
    assert options["env"]["WSLENV"] == ""
    assert "BOHRIUM_PASSWORD" not in options["env"] and "OPENAI_API_KEY" not in options["env"]
    assert not options.get("shell")
    with pytest.raises(LocalRuntimeConflict, match="non-sensitive"):
        transport._arguments({"action": "hold", "access_token": secret})


def test_controller_executes_fixed_immutable_helper_with_windows_sized_arguments():
    transport = SrtController(["wsl.exe", "-d", "Ubuntu-24.04", "--"])
    helper = RUNTIME_ROOT + "/app/src/matcreator/scripts/local_srt_host.py"
    assert transport._arguments() == ["wsl.exe", "-d", "Ubuntu-24.04", "--", "python3", "-I", helper]
    hold = transport._arguments({"version": 1, "action": "hold", "installation_id": "a" * 32,
                                 "runtime_root": RUNTIME_ROOT, "state_root": STATE_ROOT})
    assert hold[4:7] == ["python3", "-I", "-c"]
    assert helper in hold[-1] and "runpy.run_path" in hold[-1]
    assert len(subprocess.list2cmdline(hold)) < 30000


@pytest.mark.parametrize("response", ["not-json", '{"version":1,"ok":false,"code":"safe_rejection","error":"SECRET"}', '{"version":1,"ok":false,"code":"SECRET bad"}'])
def test_untrusted_controller_output_never_exposes_raw_error(monkeypatch, response):
    transport = SrtController(["wsl.exe", "-d", "Ubuntu-24.04", "--"])
    monkeypatch.setattr(transport, "_arguments", lambda: ["wsl.exe"])
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, response, "SECRET"))
    with pytest.raises(LocalRuntimeConflict) as error:
        transport.request({"version": 1, "action": "preflight"})
    assert "SECRET" not in str(error.value)


def test_controller_preserves_safe_prerequisite_code_without_detail(monkeypatch):
    transport = SrtController(["wsl.exe", "-d", "Ubuntu-24.04", "--"])
    response = json.dumps({"version": 1, "ok": False, "code": "prerequisite", "detail": "PRIVATE TOOL PATH"})
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, response, "SECRET"))
    with pytest.raises(LocalRuntimeConflict, match=r"\(prerequisite\)") as error:
        transport.request({"version": 1, "action": "preflight"})
    assert "PRIVATE TOOL PATH" not in str(error.value) and "SECRET" not in str(error.value)


def test_controller_static_refusal_codes_have_safe_host_diagnostics():
    tree = ast.parse((Path(module.__file__).parent / "scripts/local_srt_host.py").read_text(encoding="utf-8"))
    codes = {node.args[0].value for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Refusal"
             and node.args and isinstance(node.args[0], ast.Constant)}
    assert codes | {"controller_failed"} == module.ERROR_CODES


@pytest.mark.parametrize("name,value", [
    ("MEMORY_MB", "65537"), ("MEMORY_MB", "255"), ("PIDS", "63"), ("PIDS", "4097"),
])
def test_resource_limits_match_linux_controller_bounds(monkeypatch, tmp_path, name, value):
    monkeypatch.setenv("MATCREATOR_LOCAL_SRT_" + name, value)
    with pytest.raises(LocalRuntimeConflict, match="bounded"):
        SrtAccountRuntimeManager(tmp_path / "broker", tmp_path, controller=FakeController())


def test_network_maximum_and_hostname_constraints_match_controller(monkeypatch):
    for domains in (["example.org:443"] * 257, ["under_score.org:443"], ["invalid..org:443"], ["a" * 64 + ".org:443"]):
        monkeypatch.setenv("MATCREATOR_LOCAL_SRT_ALLOWED_DOMAINS", json.dumps(domains))
        with pytest.raises(LocalRuntimeConflict, match="hostname:port"):
            module._domains("MATCREATOR_LOCAL_SRT_ALLOWED_DOMAINS")


@pytest.mark.parametrize("proxy", ["socks5h://localhost:1234", "http://localhost", "http://localhost:0",
                                   "http://localhost:7897/path", "http://localhost:7897?query", "http://" + "a" * 2049])
def test_parent_proxy_bounds_and_url_shape_match_controller(monkeypatch, tmp_path, proxy):
    monkeypatch.setenv("MATCREATOR_LOCAL_SRT_PARENT_PROXY", proxy)
    with pytest.raises(LocalRuntimeConflict, match="parent proxy"):
        SrtAccountRuntimeManager(tmp_path / "broker", tmp_path, controller=FakeController())


def test_parent_proxy_is_not_persisted_in_safe_records(setup, monkeypatch):
    original, controller, account, ready = setup
    monkeypatch.setenv("MATCREATOR_LOCAL_SRT_PARENT_PROXY", "http://user:proxy-secret@127.0.0.1:7897")
    manager = SrtAccountRuntimeManager(original.home, original.source_root, controller=controller)
    monkeypatch.setattr(manager, "_ready", ready)
    target = account()
    runtime = asyncio.run(manager.ensure(target))
    record = manager._record_path(target["id"]).read_text()
    assert "proxy-secret" not in record + json.dumps(manager.report_status())
    ensure = next(value for value in controller.calls if value["action"] == "ensure")
    assert "proxy-secret" in ensure["network"]["parent_proxy"]
    assert runtime.access.token not in record


@pytest.mark.parametrize("domain", ["example.org", "example.org:*", "example.org:0", "example.org:65536", "[::1]:443"])
def test_network_policy_never_implicitly_grants_all_ports(monkeypatch, domain):
    monkeypatch.setenv("MATCREATOR_LOCAL_SRT_ALLOWED_DOMAINS", json.dumps([domain]))
    with pytest.raises(LocalRuntimeConflict, match="hostname:port"):
        module._domains("MATCREATOR_LOCAL_SRT_ALLOWED_DOMAINS")


def test_network_policy_has_explicit_normalized_ports(monkeypatch):
    monkeypatch.setenv("MATCREATOR_LOCAL_SRT_ALLOWED_DOMAINS", '["API.Example.org:0443","*.example.org:443","127.0.0.1:7897"]')
    assert module._domains("MATCREATOR_LOCAL_SRT_ALLOWED_DOMAINS") == ["*.example.org:443", "127.0.0.1:7897", "api.example.org:443"]


def test_neutral_factory_does_not_require_container_backend(monkeypatch, tmp_path):
    from matcreator.local_account_backends import create_local_runtime_manager
    monkeypatch.setenv("MATCREATOR_LOCAL_RUNTIME", "process")
    monkeypatch.setitem(sys.modules, "matcreator.local_account_containers", None)
    assert create_local_runtime_manager(tmp_path / "broker", tmp_path).runtime_backend == "process"
    monkeypatch.setenv("MATCREATOR_LOCAL_RUNTIME", "unknown")
    with pytest.raises(LocalRuntimeConflict, match="Unknown"):
        create_local_runtime_manager(tmp_path, tmp_path)


def test_broker_selects_srt_without_importing_or_running_docker(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from matcreator.local_accounts import install_local_accounts
    monkeypatch.setenv("MATCREATOR_LOCAL_RUNTIME", "srt")
    monkeypatch.setitem(sys.modules, "matcreator.local_account_containers", None)
    controller = FakeController()
    monkeypatch.setattr(module, "SrtController", lambda: controller)
    home = tmp_path / "broker"
    home.mkdir()
    (home / "config.yaml").write_text("# synthetic old installation data\n")
    app = FastAPI()
    broker = install_local_accounts(app, home=home, source_root=tmp_path)
    assert broker.runtime_manager.runtime_backend == "srt"
    origin = "http://" + broker.access.browser_host
    with TestClient(app, base_url=origin, headers={"Origin": origin}) as client:
        status = client.get("/api/auth/status").json()
        assert status["runtime_backend"] == "srt"
        assert status["execution_isolation"] == "srt_linux_namespaces"
        proof = client.post("/api/local-auth/login", json={"token": broker.access.token})
        assert proof.status_code == 200
        claimed = client.post("/api/auth/register", json={"username": "synthetic", "password": "synthetic-pass-123", "claim_existing_data": True})
        assert claimed.status_code == 409 and "isolated execution" in claimed.json()["detail"]
        assert broker.store.status()["account_count"] == 0
    assert not controller.calls  # Public setup did not provision any runtime.


def test_srt_launcher_uses_neutral_factory_and_rejects_direct_token_worker(tmp_path):
    import shutil
    root = Path(__file__).resolve().parents[1]
    powershell = (root / "script/start_local_review.ps1").read_text(encoding="utf-8")
    shell = (root / "script/start_matcreator.sh").read_text(encoding="utf-8")
    assert "[ValidateSet('process','srt')]" in powershell
    assert "[string]$SrtCommand" in powershell
    assert "from matcreator.local_account_backends import create_local_runtime_manager" in powershell
    assert "from matcreator.local_account_backends import create_local_runtime_manager" in shell
    assert "from matcreator.local_account_containers import create_local_runtime_manager" not in powershell + shell
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        pytest.skip("PowerShell unavailable")
    home = tmp_path / "never-started"
    result = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-File", str(root / "script/start_local_review.ps1"),
                             "-TokenWorker", "-RuntimeBackend", "srt", "-RuntimeHome", str(home)],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    assert result.returncode != 0
    assert "account broker" in result.stdout + result.stderr
    assert not home.exists()
