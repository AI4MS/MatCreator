"""Fail-closed contracts for the standalone Linux SRT root controller.

These tests do not install packages, create Linux accounts, or start services.
The real kernel boundary is exercised separately by the bounded WSL probe.
"""
from __future__ import annotations

from contextlib import nullcontext
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest

from matcreator.scripts import local_srt_host as host


INSTALLATION = "a" * 32
ACCOUNT = "b6fed3ce-81ac-426c-9136-4feb06cd9016"
INSTANCE = "b" * 32
TOKEN = "c" * 43
BOOT = "916a636e-825e-45c9-86a4-25f59a042183"


def request(action="ensure", **changes):
    return {"version": 1, "action": action, "installation_id": INSTALLATION,
            "account_id": ACCOUNT, "instance_id": INSTANCE, "access_token": TOKEN,
            "access_token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest(), **changes}


@pytest.mark.parametrize("key,value", [
    ("installation_id", "../escape"), ("installation_id", "A" * 32),
    ("account_id", ACCOUNT.upper()), ("account_id", "../escape"),
    ("instance_id", "c" * 31), ("account_id", None),
])
def test_identities_cannot_select_another_directory_or_unit(key, value):
    with pytest.raises(host.Refusal, match="canonical"):
        host.account_identity(request(**{key: value}))


def test_system_user_short_name_is_bound_by_full_gecos_and_unit_ids():
    identity = host.account_identity(request())
    assert identity["linux_user"] == "mc-aaaaaaaa-b6fed3ce81ac"
    assert INSTALLATION in identity["gecos"] and ACCOUNT in identity["gecos"]
    assert identity["unit_name"] == "mc-srt-" + INSTALLATION + "-" + ACCOUNT + ".service"


@pytest.mark.parametrize("limits", [
    {"memory_mb": True}, {"memory_mb": 255}, {"pids": 1}, {"cpus": float("nan")},
    {"cpus": float("inf")}, {"cpus": True}, {"cpuset_cpus": "0;reboot"},
    {"cpuset_cpus": "2-1"}, {"cpuset_cpus": "1-999999"},
    {"cpuset_cpus": "4"}, {"cpus": 3}, {"unknown": 1},
])
def test_invalid_or_unavailable_resource_limits_are_rejected(limits):
    with pytest.raises(host.Refusal):
        host.limits_for({"limits": limits}, {0, 2})


def test_cpu_selection_is_canonical_and_hard_limit_does_not_assume_contiguous_cpus():
    limits = host.limits_for({"limits": {"cpus": 2, "cpuset_cpus": "4,2,2"}}, {2, 4, 8})
    assert limits["cpuset_cpus"] == "2,4"
    assert host.parse_cpu_set("0-2,4") == {0, 1, 2, 4}
    with pytest.raises(host.Refusal):
        host.limits_for({"limits": {"cpus": 2, "cpuset_cpus": "2"}}, {2, 4})


@pytest.mark.parametrize("network", [
    {"allowed_domains": ["example.org/path"]}, {"allowed_domains": ["*"]},
    {"allowed_domains": ["host..name"]}, {"parent_proxy": "file:///etc/passwd"},
    {"parent_proxy": "http://localhost:7897/x"}, {"parent_proxy": "http://localhost:bad"},
    {"parent_proxy": "http://localhost:7897\nanything"}, {"extra": True},
])
def test_network_policy_does_not_accept_shell_or_path_injection(network):
    with pytest.raises(host.Refusal):
        host.network_for({"network": network})


def test_root_paths_cannot_be_chosen_by_request(tmp_path):
    controller = host.Controller(runtime_root=str(tmp_path / "runtime"), state_root=str(tmp_path / "state"))
    with pytest.raises(host.Refusal, match="administrator provisioned"):
        controller._roots({"state_root": "/etc"})


def test_egress_grants_pin_exact_ports_and_never_broaden_a_hostname():
    policy = host.network_for({"network": {"allowed_domains": ["API.Example.org:00443", "*.example.org:443"],
                                           "denied_domains": ["internal.example.org:443"]}})
    assert policy["allowed_domains"] == ["*.example.org:443", "api.example.org:443"]
    assert policy["denied_domains"] == ["internal.example.org:443"]
    with pytest.raises(host.Refusal, match="exact port"):
        host.network_for({"network": {"allowed_domains": ["api.example.org"]}})


@pytest.mark.parametrize("change", ["root_uid", "wrong_gecos", "wrong_home", "shared_uid", "shared_group", "supplementary_group"])
def test_execution_uid_and_group_cannot_be_adopted_or_shared(monkeypatch, tmp_path, change):
    identity = host.account_identity(request())
    home = tmp_path / "home"
    user = SimpleNamespace(pw_name=identity["linux_user"], pw_uid=491, pw_gid=491,
                           pw_gecos=identity["gecos"], pw_dir=str(home), pw_shell="/usr/sbin/nologin")
    other = SimpleNamespace(pw_name="other", pw_uid=500, pw_gid=500)
    group = SimpleNamespace(gr_name=identity["linux_user"], gr_gid=491, gr_mem=[])
    groups = [group]
    if change == "root_uid":
        user.pw_uid = 0
    elif change == "wrong_gecos":
        user.pw_gecos = "another full account ID"
    elif change == "wrong_home":
        user.pw_dir = "/another/account"
    elif change == "shared_uid":
        other.pw_uid = 491
    elif change == "shared_group":
        other.pw_gid = 491
    else:
        groups.append(SimpleNamespace(gr_name="sudo", gr_gid=27, gr_mem=[user.pw_name]))
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwnam=lambda name: user, getpwall=lambda: [user, other]))
    monkeypatch.setitem(sys.modules, "grp", SimpleNamespace(getgrgid=lambda number: group, getgrall=lambda: groups))
    controller = host.Controller()
    with pytest.raises(host.Refusal):
        controller._user(identity, home, create=False)


def test_pid_starttime_parses_spaces_and_parentheses_in_comm(tmp_path):
    process = tmp_path / "42"
    process.mkdir()
    # Field 3 is state, field 22 is the 20th field after comm.
    (process / "stat").write_text("42 (some (odd) worker) S " + " ".join(["0"] * 18 + ["123456", "0"]))
    assert host.proc_starttime(42, tmp_path) == "123456"


def _runtime(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    files = {}
    for relative in ["app/src/matcreator/local_srt_policy.py", "app/src/matcreator/scripts/local_srt_supervisor.py",
                     "app/src/matcreator/scripts/local_srt_worker.py", "app/src/matcreator/scripts/local_srt_channel.py", "bin/bwrap"]:
        path = runtime / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# protected public source\n")
        files[relative] = host.file_digest(path)
    (runtime / "manifest.json").write_text(json.dumps({"version": 1, "files": files,
                                                       "source_sha256": host.digest(files)}))
    monkeypatch.setattr(host, "_immutable", lambda *a, **k: None)
    monkeypatch.setattr(host, "_immutable_tree", lambda *a, **k: None)
    monkeypatch.setattr(host.os, "access", lambda *a: True)
    return host.Controller(runtime_root=str(runtime), state_root=str(tmp_path / "state")), files


def test_manifest_pins_both_public_file_tree_and_raw_manifest_revision(tmp_path, monkeypatch):
    controller, files = _runtime(tmp_path, monkeypatch)
    info = controller.runtime_info({})
    assert info["source_hash"] == host.digest(files)
    assert info["runtime_sha256"] == host.file_digest(controller.runtime / "manifest.json")
    source = controller.runtime / next(iter(files))
    source.write_text("# changed despite old manifest\n")
    with pytest.raises(host.Refusal, match="differs from its manifest"):
        controller.runtime_info({})


def test_unmanifested_python_file_cannot_shadow_public_runtime(tmp_path, monkeypatch):
    controller, _ = _runtime(tmp_path, monkeypatch)
    (controller.runtime / "app/sitecustomize.py").write_text("raise RuntimeError('injected')")
    with pytest.raises(host.Refusal, match="absent from the trusted manifest"):
        controller.runtime_info({})


def test_manifest_parent_traversal_is_rejected_before_read(tmp_path, monkeypatch):
    controller, _ = _runtime(tmp_path, monkeypatch)
    files = {"../secrets": "d" * 64}
    (controller.runtime / "manifest.json").write_text(json.dumps({"version": 1, "files": files,
                                                                "source_sha256": host.digest(files)}))
    with pytest.raises(host.Refusal, match="unsafe path"):
        controller.runtime_info({})


def test_stale_manifest_pin_is_not_silently_upgraded(tmp_path, monkeypatch):
    controller, _ = _runtime(tmp_path, monkeypatch)
    with pytest.raises(host.Refusal, match="revision differs"):
        controller.runtime_info({"runtime_sha256": "0" * 64})


def _portable_admin_stat(monkeypatch, *, overrides=None):
    """Model POSIX admin permissions while retaining real portable walk paths."""
    original = Path.lstat
    overrides = overrides or {}
    def lstat(path, *args, **kwargs):
        actual = original(path, *args, **kwargs)
        if stat.S_ISDIR(actual.st_mode):
            mode = stat.S_IFDIR | 0o755
        elif stat.S_ISREG(actual.st_mode):
            mode = stat.S_IFREG | 0o644
        else:
            mode = actual.st_mode
        changes = overrides.get(path, {})
        return SimpleNamespace(st_uid=changes.get("uid", 0), st_gid=0,
                               st_mode=changes.get("mode", mode))
    monkeypatch.setattr(Path, "lstat", lstat)


@pytest.mark.parametrize("change", [{"uid": 1000}, {"mode": stat.S_IFREG | 0o664},
                                    {"mode": stat.S_IFIFO | 0o600}])
def test_immutable_tree_rejects_foreign_writable_or_nonregular_entry(tmp_path, monkeypatch, change):
    tree = tmp_path / "public"
    tree.mkdir()
    nested = tree / "nested"
    nested.mkdir()
    entry = nested / "dependency.py"
    entry.write_text("# protected dependency")
    _portable_admin_stat(monkeypatch, overrides={entry: change})
    with pytest.raises(host.Refusal, match="dependency entry"):
        host._immutable_tree(tree)


def test_immutable_tree_symlink_still_checks_foreign_resolved_target(tmp_path, monkeypatch):
    tree = tmp_path / "public"
    tree.mkdir()
    link = tree / "dependency-link.py"
    link.write_text("placeholder for portable symlink model")
    target = tmp_path / "foreign.py"
    target.write_text("# a foreign account can edit this target")
    _portable_admin_stat(monkeypatch, overrides={link: {"mode": stat.S_IFLNK | 0o777}, target: {"uid": 1000}})
    original_resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda path, **kwargs: target if path == link else original_resolve(path, **kwargs))
    with pytest.raises(host.Refusal, match="administrator controlled"):
        host._immutable_tree(tree)


def test_immutable_tree_walk_permission_error_is_not_silently_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(host, "_immutable", lambda *args, **kwargs: None)
    def walk(*args, **kwargs):
        kwargs["onerror"](PermissionError("unreadable dependency directory"))
        return iter(())
    monkeypatch.setattr(host.os, "walk", walk)
    with pytest.raises(host.Refusal, match="cannot be inspected"):
        host._immutable_tree(tmp_path)


@pytest.fixture
def controlled(tmp_path, monkeypatch):
    controller = host.Controller(runtime_root=str(tmp_path / "runtime"), state_root=str(tmp_path / "state"))
    identity = host.account_identity(request())
    paths = controller.paths(identity)
    info = {"source_hash": "d" * 64, "runtime_sha256": "e" * 64,
            "source_root": str(controller.runtime / "app"), "python": str(controller.runtime / "venv/bin/python")}
    policy = {"filesystem": {"denyRead": ["/"], "allowRead": [str(paths["home"])]}}
    spec = controller._spec(identity, paths, info, 491, 491, TOKEN, host.limits_for({}), host.network_for({}))
    spec["policy_sha256"] = host.digest(policy)
    record = {"version": 1, "source_hash": info["source_hash"], "spec_hash": host.digest(spec),
              "policy_hash": host.digest(policy), "access_token_sha256": hashlib.sha256(TOKEN.encode()).hexdigest(),
              "ownership_id": "f" * 64, "stopped": False}
    unit = {"LoadState": "loaded", "ActiveState": "active", "SubState": "running", "MainPID": "42",
            "User": identity["linux_user"], "Group": identity["linux_user"],
            "WorkingDirectory": spec["source_root"], "NoNewPrivileges": "yes", "KillMode": "control-group",
            "Restart": "no", "Delegate": "no", "ProtectControlGroups": "yes",
            "ControlGroup": "/system.slice/" + identity["unit_name"], "InvocationID": "1" * 32,
            "Environment": "PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1"}
    unit["ExecStart"] = "{ path=" + spec["python"] + " ; argv[]=" + " ".join(controller.command(spec, paths)) + " ; ignore_errors=no ; }"
    monkeypatch.setattr(host, "boot_id", lambda: BOOT)
    monkeypatch.setattr(host.os, "sched_getaffinity", lambda pid: {0, 1}, raising=False)
    monkeypatch.setattr(controller, "runtime_info", lambda request: info)
    monkeypatch.setattr(controller, "_user", lambda *a, **k: (491, 491))
    monkeypatch.setattr(controller, "_directories", lambda *a, **k: None)
    monkeypatch.setattr(controller, "_process", lambda pid, uid, group: "123456")
    monkeypatch.setattr(controller, "_listener", lambda pid, port: None)
    monkeypatch.setattr(controller, "_check_cgroup", lambda *a, **k: None)
    monkeypatch.setattr(controller, "_policy", lambda value: (policy, host.digest(policy)))
    documents = {"spec.json": spec, "record.json": record, "policy.json": policy}
    monkeypatch.setattr(host, "_read_json", lambda path, **kwargs: documents[path.name])
    return SimpleNamespace(controller=controller, identity=identity, paths=paths, info=info,
                           policy=policy, spec=spec, record=record, unit=unit, documents=documents)


@pytest.mark.parametrize("field,value", [
    ("User", "root"), ("Group", "shared"), ("NoNewPrivileges", "no"),
    ("KillMode", "process"), ("Delegate", "yes"), ("ProtectControlGroups", "no"),
    ("Restart", "always"), ("ControlGroup", "/other/unit.service"),
    ("WorkingDirectory", "/writable/home"), ("Environment", "PYTHONPATH=/writable/home"),
    ("ExecStart", "{ path=/bin/sh ; argv[]=/bin/sh -c anything ; }"),
])
def test_cannot_reattach_to_reconfigured_or_privileged_unit(controlled, field, value):
    unit = {**controlled.unit, field: value}
    with pytest.raises(host.Refusal) as error:
        controlled.controller._check_unit(unit, controlled.spec, controlled.paths)
    assert error.value.code == "unit_mismatch"


def test_existing_token_is_never_overwritten(controlled):
    other = "x" * 43
    with pytest.raises(host.Refusal, match="credential differs"):
        controlled.controller._existing(controlled.identity, controlled.paths, controlled.info,
                                        request(access_token=other, access_token_sha256=hashlib.sha256(other.encode()).hexdigest()))
    assert controlled.spec["access_token"] == TOKEN


def test_existing_account_policy_and_limits_are_pinned(controlled):
    with pytest.raises(host.Refusal, match="configuration differs"):
        controlled.controller._existing(controlled.identity, controlled.paths, controlled.info,
                                        request(limits={"memory_mb": 2048}))
    controlled.documents["policy.json"] = {"filesystem": {"allowRead": ["/"]}}
    with pytest.raises(host.Refusal, match="policy differs"):
        controlled.controller._existing(controlled.identity, controlled.paths, controlled.info, request())


def test_writable_account_cannot_supply_a_new_spec_without_root_record_digest(controlled):
    controlled.spec["home"] = "/another/account"
    with pytest.raises(host.Refusal, match="identity or policy differs"):
        controlled.controller._existing(controlled.identity, controlled.paths, controlled.info, request())


def test_existing_unit_is_reattached_without_starting_another(controlled, monkeypatch):
    controlled.paths["account"].mkdir(parents=True)
    monkeypatch.setattr(controlled.controller, "_lock", lambda *a, **k: nullcontext())
    monkeypatch.setattr(controlled.controller, "_show", lambda unit: controlled.unit)
    monkeypatch.setattr(controlled.controller, "_start", lambda *a: pytest.fail("duplicate launch"))
    result = controlled.controller.ensure(request())
    assert result["state"] == "starting" and result["created"] is False
    assert result["ingress_kind"] == result["ownership_receipt"]["ingress_kind"] == "stdio-mux-v1"
    assert result["main_pid"] == 42 and result["main_starttime"] == "123456"
    assert TOKEN not in json.dumps(result)


def test_missing_service_with_retained_data_is_preserved_not_replaced(controlled, monkeypatch):
    controlled.paths["account"].mkdir(parents=True)
    monkeypatch.setattr(controlled.controller, "_lock", lambda *a, **k: nullcontext())
    monkeypatch.setattr(controlled.controller, "_show", lambda unit: {"LoadState": "not-found"})
    monkeypatch.setattr(controlled.controller, "_start", lambda *a: pytest.fail("replacement launch"))
    with pytest.raises(host.Refusal, match="preserved"):
        controlled.controller.ensure(request())
    assert controlled.paths["account"].is_dir()


def test_process_replacement_with_same_unit_is_rejected(controlled):
    controlled.record["launch_identity"] = {"main_pid": 42, "main_starttime": "other",
                                             "boot_id": BOOT, "unit_invocation_id": "1" * 32}
    with pytest.raises(host.Refusal, match="process was replaced"):
        controlled.controller._state(controlled.spec, controlled.paths, controlled.record, controlled.unit, created=False)


def test_late_state_from_old_account_or_old_boot_cannot_supply_a_port(controlled):
    controlled.paths["state"].mkdir(parents=True)
    (controlled.paths["state"] / "state.json").write_text("placeholder")
    state = {key: controlled.spec[key] for key in ("installation_id", "account_id", "instance_id", "uid", "gid",
                                                   "home", "unit_name", "runtime_sha256", "policy_sha256")}
    state.update(main_pid=42, main_starttime="123456", boot_id="old-boot", status="ready", web_port=12345)
    controlled.documents["state.json"] = state
    with pytest.raises(host.Refusal, match="another process"):
        controlled.controller._state(controlled.spec, controlled.paths, controlled.record, controlled.unit, created=False)
    state["boot_id"] = BOOT
    state["account_id"] = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(host.Refusal):
        controlled.controller._state(controlled.spec, controlled.paths, controlled.record, controlled.unit, created=False)


def test_stop_requires_exact_creation_receipt_and_does_not_kill_by_name(controlled, monkeypatch):
    controller = controlled.controller
    controlled.record["launch_identity"] = {"main_pid": 42, "main_starttime": "123456",
                                             "boot_id": BOOT, "unit_invocation_id": "1" * 32}
    monkeypatch.setattr(controller, "_lock", lambda *a, **k: nullcontext())
    monkeypatch.setattr(controller, "_show", lambda unit: controlled.unit)
    commands = []
    monkeypatch.setattr(controller, "run", lambda argv, **k: commands.append(argv))
    with pytest.raises(host.Refusal, match="exact service"):
        controller.stop_test_owned(request(ownership_receipt={"unit_name": controlled.identity["unit_name"]}))
    assert commands == []


def test_exact_owned_service_stop_retains_data_and_returns_tombstone(controlled, monkeypatch):
    controller = controlled.controller
    controlled.paths["home"].mkdir(parents=True)
    retained = controlled.paths["home"] / "input.txt"
    retained.write_text("personal input")
    initial = controller._state(controlled.spec, controlled.paths, controlled.record, controlled.unit, created=True)
    controlled.record["launch_identity"] = {key: initial[key] for key in (
        "main_pid", "main_starttime", "boot_id", "unit_invocation_id")}
    monkeypatch.setattr(controller, "_lock", lambda *a, **k: nullcontext())
    units = iter([controlled.unit, {"LoadState": "not-found", "ActiveState": "inactive", "MainPID": "0"}])
    monkeypatch.setattr(controller, "_show", lambda unit: next(units))
    commands = []
    def run(argv, **kwargs):
        commands.append(argv)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(controller, "run", run)
    monkeypatch.setattr(host, "proc_starttime", lambda *a: "reused-pid-different-start")
    writes = []
    monkeypatch.setattr(host, "_atomic_json", lambda path, value, gid: writes.append((path, dict(value))))
    result = controller.stop_test_owned(request(ownership_receipt=initial["ownership_receipt"]))
    assert commands == [["systemctl", "stop", controlled.identity["unit_name"]]]
    assert result["state"] == "stopped" and result["cleanup_confirmed"] and result["data_retained"]
    assert result["web_port"] == 0 and result["main_pid"] == 0
    assert writes[0][1]["stopped"] is True and retained.read_text() == "personal input"


def test_stopped_tombstone_does_not_silently_adopt_new_running_unit(controlled):
    controlled.record["stopped"] = True
    with pytest.raises(host.Refusal, match="unexpectedly active"):
        controlled.controller._state(controlled.spec, controlled.paths, controlled.record, controlled.unit, created=False)


def test_service_launch_is_unprivileged_isolated_and_secret_free_in_argv(controlled, monkeypatch):
    commands = []
    monkeypatch.setattr(controlled.controller, "run", lambda argv, **kwargs: commands.append(argv) or SimpleNamespace(returncode=0))
    controlled.spec["limits"]["cpuset_cpus"] = "0"
    controlled.controller._start(controlled.spec, controlled.paths)
    command = commands[0]
    assert command[:2] == ["systemd-run", "--unit=" + controlled.identity["unit_name"]]
    assert "--property=User=" + controlled.identity["linux_user"] in command
    assert "--property=AllowedCPUs=0" in command
    assert "--property=MemoryMax=1536M" in command and "--property=TasksMax=256" in command
    assert "--property=MemorySwapMax=0" in command
    assert "--property=CPUQuota=100%" in command and "--property=KillMode=control-group" in command
    assert command[command.index("--") + 1:] == controlled.controller.command(controlled.spec, controlled.paths)
    assert "-I" in command and TOKEN not in " ".join(command)
    assert not any("PYTHONPATH" in part for part in command)


def test_keeper_matches_only_exact_installation_systemd_units(controlled, monkeypatch):
    commands = []
    def run(argv, **kwargs):
        commands.append(argv)
        return SimpleNamespace(returncode=0, stdout=controlled.identity["unit_name"] + " loaded active running test\n")
    monkeypatch.setattr(controlled.controller, "run", run)
    assert controlled.controller._running_units(INSTALLATION)
    assert commands[0][-1] == "mc-srt-" + INSTALLATION + "-*.service"
    monkeypatch.setattr(controlled.controller, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="other.service loaded active running\n"))
    with pytest.raises(host.Refusal, match="Unexpected"):
        controlled.controller._running_units(INSTALLATION)


def test_main_never_echoes_secrets_from_unexpected_errors(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "stdin", io.StringIO(json.dumps(request())))
    def fail(*a):
        raise RuntimeError("secret " + TOKEN)
    monkeypatch.setattr(host.Controller, "request", fail)
    assert host.main() == 0
    value = capsys.readouterr().out
    assert TOKEN not in value and json.loads(value) == {"version": 1, "ok": False, "code": "controller_failed"}


@pytest.mark.parametrize("changed,value", [("memory.max", "max"), ("memory.swap.max", "max"), ("pids.max", "max"),
                                          ("cpu.max", "max 100000"), ("cpu.max", "200000 100000"),
                                          ("cpuset.cpus.effective", "0-3")])
def test_actual_cgroup_files_must_enforce_the_recorded_limits(tmp_path, changed, value):
    controller = host.Controller(cgroup_root=str(tmp_path))
    group = tmp_path / "system.slice" / "test.service"
    group.mkdir(parents=True)
    files = {"memory.max": str(1536 * 1048576), "memory.swap.max": "0", "pids.max": "256", "cpu.max": "100000 100000",
             "cpuset.cpus.effective": "0"}
    for name, content in files.items():
        (group / name).write_text(content)
    limits = host.limits_for({"limits": {"cpuset_cpus": "0"}})
    controller._check_cgroup("/system.slice/test.service", limits)
    (group / changed).write_text(value)
    with pytest.raises(host.Refusal) as error:
        controller._check_cgroup("/system.slice/test.service", limits)
    assert error.value.code == "limit_mismatch"


def test_failed_application_health_does_not_prevent_exact_owned_cleanup(controlled, monkeypatch):
    controller = controlled.controller
    initial = controller._state(controlled.spec, controlled.paths, controlled.record, controlled.unit, created=True)
    controlled.record["launch_identity"] = {key: initial[key] for key in (
        "main_pid", "main_starttime", "boot_id", "unit_invocation_id")}
    controlled.paths["state"].mkdir(parents=True)
    (controlled.paths["state"] / "state.json").write_text("unusable worker state")
    controlled.documents["state.json"] = {"status": "failed"}
    monkeypatch.setattr(controller, "_lock", lambda *a, **k: nullcontext())
    units = iter([controlled.unit, {"LoadState": "not-found", "MainPID": "0", "ActiveState": "inactive"}])
    monkeypatch.setattr(controller, "_show", lambda *a: next(units))
    monkeypatch.setattr(controller, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(host, "proc_starttime", lambda *a: "new-pid-start")
    monkeypatch.setattr(host, "_atomic_json", lambda *a: None)
    result = controller.stop_test_owned(request(ownership_receipt=initial["ownership_receipt"]))
    assert result["cleanup_confirmed"] and result["state"] == "stopped"


def test_protocol_errors_are_structured_json_not_raw_json_error(monkeypatch, capsys):
    monkeypatch.setattr(host.sys, "stdin", io.StringIO("{broken"))
    assert host.main() == 0
    assert json.loads(capsys.readouterr().out) == {"version": 1, "ok": False, "code": "invalid_request"}
