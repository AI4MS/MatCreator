"""Transfer one managed VASP run through a tracked Bohrium sandbox."""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from matcreator.aidb.managed_vasp import (
    LAUNCHER,
    OUTPUTS,
    RECEIPT,
    _completion_lock,
    _read_receipt_hashes,
    get_managed_vasp_completion,
    receive_managed_vasp_result,
    staged_relaxation,
)
from matcreator.aidb.service import AIDBError, _save
from matcreator.aidb.vasp_archive import file_digest


_ACTIVE = {"queued", "running", "resuming"}


def _run_path(service, job_id):
    return service.store.path.parent / "vasp-jobs" / job_id / "sandbox-run.json"


def _run_directory(job_id):
    return f"/tmp/matcreator-vasp-{job_id}"


def start_sandbox_vasp(service, job_id, spec, revision, binding_id, workspace):
    """Stage and start a single durable background command on a tracked sandbox."""
    run_path = _run_path(service, job_id)
    with _completion_lock(run_path):
        job = service.store.get_job(job_id)
        snapshot = job["snapshot"]
        saved = json.loads(run_path.read_text(encoding="utf-8")) if run_path.exists() else None
        if saved and (saved["spec"] != spec or saved["evidence"]["revision"] != revision
                      or saved["evidence"]["binding_id"] != binding_id):
            raise AIDBError("这个 sandbox 已绑定另一份 VASP 输入或数据库。", "stale_input")
        if snapshot.get("managed_vasp_command"):
            return {"job_id": job_id, "status": "running",
                    "message": "已跟踪原 VASP 命令。请轮询命令或回收其产物。"}
        if snapshot.get("background_command"):
            raise AIDBError("请先等待 sandbox 当前命令结束。", "command_running")
        if job["status"] not in _ACTIVE:
            raise AIDBError("当前 sandbox 无法启动 VASP。", "command_unavailable")

        remote = _run_directory(job_id)
        token = saved["evidence"]["token"] if saved else uuid4().hex
        with staged_relaxation(spec, revision, token, service.store.path.parent / "vasp-stage",
                               workspace) as (staged, expected):
            evidence = {"schema": 1, "token": token, "revision": revision, "expected": expected,
                        "binding_id": binding_id, "workspace": str(workspace)}
            state = {"spec": spec, "evidence": evidence, "outputs": staged["out_files"]}
            _save(run_path, state)
            prepared = service.run_job_command(job_id, f"mkdir -p -m 700 -- {remote}")
            if prepared.get("exit_code") != 0:
                raise AIDBError("无法准备 sandbox 的 VASP 运行目录。", "upload_failed")
            folder = Path(staged["input_root"]) / staged["input_path"]
            for item in sorted(folder.iterdir()):
                service.upload_job_file(job_id, item, f"{remote}/{item.name}")
            launch = service.start_job_command(job_id, f"cd {remote} && bash {LAUNCHER}",
                                               persist_before_launch=True)
        if launch["launch_status"] == "rejected":
            return {"status": "error", "job_id": job_id, "message": "sandbox 拒绝启动 VASP。可以重试原作业。"}
        if launch["launch_status"] == "uncertain":
            return {"status": "launch_uncertain", "job_id": job_id,
                    "message": "无法确认 sandbox 是否已启动 VASP。请轮询原作业。"}
        return {"status": "running", **launch}


def collect_sandbox_vasp(service, job_id, destination):
    """Retrieve outputs after the tracked command ends and invoke the shared receiver."""
    run_path = _run_path(service, job_id)
    with _completion_lock(run_path):
        job = service.store.get_job(job_id)
        snapshot = job["snapshot"]
        command = snapshot.get("last_command_result")
        if not snapshot.get("managed_vasp_command") or not command or command.get("running") is not False:
            raise AIDBError("请先轮询原 VASP 命令，等待它结束后再回收。", "command_running")

        staged = json.loads(run_path.read_text(encoding="utf-8"))
        evidence = staged["evidence"]
        if job["status"] in {"cancelled", "terminate_requested", "terminated", "failed", "lost"}:
            completed = get_managed_vasp_completion(
                user_id=job["owner_id"], session_id=job["session_id"], job_id=job_id, store=service.store,
            )
            if completed and completed["status"] == "verified":
                return completed, evidence
            raise AIDBError("此 sandbox 已结束或失败，无法再归档未核实的作业", "command_unavailable")
        if "destination" not in staged:
            workspace = Path(evidence["workspace"])
            if destination == workspace or not destination.is_relative_to(workspace) or destination.resolve() != destination:
                raise AIDBError("VASP 交付目录必须是工作区内的普通子目录。", "invalid_output")
            staged["destination"] = str(destination)
            _save(run_path, staged)
        destination = staged["destination"]
        private_root = service.store.path.parent / "vasp-jobs" / job_id / "sandbox-outputs"
        private_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        required = {*OUTPUTS, RECEIPT, *evidence["expected"].get("required_artifacts", [])}
        def retrieve(name, validate):
            path = private_root / name
            if path.is_file() and not path.is_symlink() and validate(path):
                return path
            if job["status"] not in _ACTIVE:
                raise ValueError("sandbox unavailable")
            with TemporaryDirectory(prefix="download-", dir=private_root) as temporary:
                received = Path(temporary) / name
                service.download_job_file(job_id, f"{_run_directory(job_id)}/{name}", received)
                if not received.is_file() or received.is_symlink():
                    raise ValueError("no downloaded file")
                # Keep bytes private until their receipt can be checked. On a
                # retry they can be checked again before attempting a download.
                os.replace(received, path)
                return path if validate(path) else None

        successful = command.get("exit_code") == 0
        declared = {}

        def validate_receipt(path):
            try:
                declared.update(_read_receipt_hashes(path, evidence["token"]))
            except (AIDBError, OSError, ValueError):
                return False
            return True

        missing, received = set(), {}
        for name in (RECEIPT, *(name for name in staged["outputs"] if name != RECEIPT)):
            def validate(path):
                if not successful:
                    return True
                if name == RECEIPT:
                    return validate_receipt(path)
                digest = declared.get(name)
                return digest is None or file_digest(path) == digest

            try:
                path = retrieve(name, validate)
                if path is None:
                    missing.add(name)
                else:
                    received[name] = path
            except (OSError, RuntimeError, ValueError):
                missing.add(name)

        # If a well-formed receipt has corrupt hashes, refresh it on the
        # next attempt as well. Healthy cached files remain in place.
        if successful and required.intersection(missing) - {RECEIPT} and RECEIPT in received:
            if any((private_root / name).is_file() for name in required.intersection(missing)):
                received.pop(RECEIPT).unlink()
        if successful and not declared:
            for name in required - {RECEIPT}:
                received.pop(name, None)
        artifacts = [{"source": job["external_id"], "destination": str(path)} for path in received.values()]
        failure = None
        if command.get("exit_code") == 93:
            failure = ("output_incomplete", "VASP 命令结束，但运行目录缺少必要产物。")
        elif command.get("exit_code") != 0:
            failure = ("command_failed", f'VASP 启动器退出码为 {command.get("exit_code")}。')
        elif required.intersection(missing):
            failure = ("download_failed", "未能下载必需的 VASP 产物：" + ", ".join(sorted(required.intersection(missing))))
        completed = receive_managed_vasp_result({
            **job, "output_dir": destination, "artifacts": artifacts,
            "specification": {"managed_vasp": evidence},
        }, service.store.path.parent, failure=failure, unavailable_outputs=sorted(set(missing) - required))
        return completed, evidence
