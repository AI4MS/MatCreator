"""Prepare a tracked VASP launch and receive its verified calculation result."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import stat
from tempfile import TemporaryDirectory, mkdtemp

from matcreator.aidb.aimd import CURVE_FILENAME, MD_TYPE, md_runtime_conflicts, temperature_curve_html
from matcreator.aidb.archive import archive_verified_vasp_result
from matcreator.aidb.band_structure import BAND_FILES, write_band_files
from matcreator.aidb.relaxation_inputs import RelaxationInputs, vasp_launch
from matcreator.aidb.service import AIDBError, SessionAIDB, _save
from matcreator.aidb.vasp_archive import file_digest, parse_vasp_result
from matcreator.control_plane.remote_jobs import RemoteJobStore
from matcreator.workspace import ADK_DIR


LAUNCHER = "matcreator-vasp-launch.sh"
RECEIPT = "matcreator-vasp-receipt.sha256"
OUTPUTS = ("vasprun.xml", "OUTCAR")
RESTART_OUTPUTS = frozenset({"CHGCAR", "WAVECAR"})


def _launcher(command, token, hashes, required):
    checks = "\n".join(f"{digest}  {name}" for name, digest in sorted(hashes.items()))
    unchanged = "\n".join(f"{digest}  {name}" for name, digest in sorted(hashes.items()) if name not in required)
    absent = " ".join(shlex.quote(name) for name in (*OUTPUTS, RECEIPT, *(n for n in required if n not in hashes)))
    optional = "\n".join(
        f'    if [[ -s {name} && ! -L {name} ]]; then' + (
            f'\n        if ! sha256sum --check --status <<<"{hashes[name]}  {name}"; then'
            f'\n            sha256sum -- {name}\n        fi' if name in hashes else f"\n        sha256sum -- {name}"
        ) + "\n    fi"
        for name in required
    )
    return f"""#!/usr/bin/env bash
set -euo pipefail
for output in {absent}; do
    [[ ! -e "$output" && ! -L "$output" ]]
done
sha256sum --check --status <<'MATCREATOR_INPUTS'
{checks}
MATCREATOR_INPUTS
set +e
(
    set -e
    {command}
)
vasp_exit=$?
set -e
if [[ "$vasp_exit" != 0 ]]; then
    printf '%s\\n' "VASP exited with status $vasp_exit" >&2
    exit 92
fi
sha256sum --check --status <<'MATCREATOR_INPUTS'
{unchanged}
MATCREATOR_INPUTS
for output in vasprun.xml OUTCAR; do
    [[ -f "$output" && ! -L "$output" ]] || exit 93
done
[[ ! -e {RECEIPT} && ! -L {RECEIPT} ]]
{{
    printf '%s\\n' 'matcreator-vasp-v1 {token}'
    sha256sum -- vasprun.xml OUTCAR
{optional}
}} > {RECEIPT}
"""


def _copy_input(source, target):
    fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as src:
        if not stat.S_ISREG(os.fstat(src.fileno()).st_mode):
            raise ValueError("VASP input must be a regular file")
        with target.open("xb") as dest:
            shutil.copyfileobj(src, dest)


@contextmanager
def staged_relaxation(spec, revision, token, private_root, workspace):
    """Copy only the authorized inputs into the only directory sent to the provider."""
    from monty.json import jsanitize

    if not private_root.resolve().is_relative_to(workspace):
        private_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    else:
        raise AIDBError("VASP 暂存目录必须独立于工作区", "invalid_input")
    source = Path(spec["input_root"]) / spec["input_path"]
    command = spec["command"]
    with TemporaryDirectory(prefix="relax-", dir=private_root) as temporary:
        stage = Path(temporary) / source.name
        stage.mkdir(mode=0o700)
        launch = vasp_launch(source, command)
        if launch is None:
            raise AIDBError("VASP 启动命令或脚本已改变，请重新核对输入", "stale_input")
        _, script = launch
        names = set(revision["sha256"])
        if script:
            if script.name == LAUNCHER:
                raise AIDBError("VASP 启动脚本使用了保留文件名", "invalid_input")
            names.add(script.name)
        for name in names:
            _copy_input(source / name, stage / name)
        inputs = RelaxationInputs.read(stage, command, vasp_type=spec.get("vasp_type"))
        staged_revision = {key: value for key, value in revision.items() if key != "upstream_scf"}
        if inputs is None or inputs.revision != staged_revision:
            raise AIDBError("VASP 暂存文件与提交前核对的输入不一致", "stale_input")
        hashes = {**inputs.file_hashes}
        if script:
            hashes[script.name] = inputs.launcher_hash
        required = sorted(RESTART_OUTPUTS.intersection(spec.get("required_artifacts", ())))
        (stage / LAUNCHER).write_text(_launcher(command, token, hashes, required), encoding="utf-8")
        expected = jsanitize({
            "formula": inputs.formula,
            "calculation_type": inputs.calculation_type,
            "required_artifacts": required,
            "temperature_requested": spec.get("temperature_requested", False),
            "structure": inputs.structure.as_dict(),
            "incar": inputs.incar,
            "kpoints": inputs.kpoints.as_dict() if inputs.kpoints is not None else None,
            "potentials": inputs.potentials,
            "lexch": inputs.lexch,
        }, strict=True)
        requested = [name for name in spec["out_files"] or []
                     if inputs.calculation_type != "nscf" or name not in BAND_FILES]
        outputs = list(dict.fromkeys([*requested, *OUTPUTS, *required, RECEIPT]))
        if any(Path(name).name.startswith("POTCAR") or Path(name).name == LAUNCHER for name in outputs):
            raise AIDBError("VASP 产物清单不能包含赝势或内部启动器", "invalid_input")
        for item in stage.iterdir():
            item.chmod(0o600 if item.name in required else 0o400)
        launch_spec = {key: value for key, value in spec.items() if key != "upstream_record_id"}
        launch_spec.update({"input_root": temporary, "input_path": stage.name,
                       "command": f"bash {LAUNCHER}", "out_files": outputs})
        yield launch_spec, expected


@contextmanager
def _completion_lock(path):
    import sqlite3

    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    connection = sqlite3.connect(path.with_suffix(".lock.sqlite"), timeout=150)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield
    finally:
        connection.close()


def _completion_path(adk_dir, job_id):
    return adk_dir / "vasp-jobs" / job_id / "completion.json"


def _read_completion(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def get_managed_vasp_completion(*, user_id: str, session_id: str, job_id: str,
                                store: RemoteJobStore | None = None) -> dict | None:
    """Return a durable completion only for the tracked job's owner and session."""
    if not re.fullmatch("[0-9a-f]{32}", job_id):
        return None
    store = store or RemoteJobStore(ADK_DIR / "remote-jobs.db")
    job = store.get_job(job_id)
    if not job or job["owner_id"] != user_id or job["session_id"] != session_id:
        return None
    batch_collected = job["status"] == "collected" and job["specification"].get("managed_vasp")
    sandbox_tracked = job["provider"] == "bohr_sandbox" and job["snapshot"].get("managed_vasp_command")
    if not (batch_collected or sandbox_tracked):
        return None
    return _read_completion(_completion_path(store.path.parent, job_id))


def _plain_child(root, path):
    """Require an unchanged regular path below the private or user workspace root."""
    return path.is_relative_to(root) and path != root and path.resolve() == path


def _collected_files(job, private_root):
    root = private_root / job["job_id"]
    for artifact in job["artifacts"]:
        path = Path(artifact["destination"])
        if artifact.get("source") != job["external_id"] or not _plain_child(root, path) or not path.is_file():
            continue
        yield path


def resolve_restart_artifacts(record, required, owner_id):
    """Resolve record claims against the tracked job, its receipt and retained bytes."""
    facts = record["source_metadata"]["vasp_facts"]["source"]
    reference = facts.get("restart_artifacts") or {}
    job_id = reference.get("job_id", "")
    if not re.fullmatch("[0-9a-f]{32}", job_id):
        raise AIDBError("此记录没有已核实的 VASP 重启文件", "not_ready")
    store = RemoteJobStore(ADK_DIR / "remote-jobs.db")
    job = store.get_job(job_id)
    if not job or job["owner_id"] != owner_id:
        raise AIDBError("无法取得原作业的 VASP 重启文件", "not_ready")
    private = store.path.parent / "vasp-jobs"
    completion = _read_completion(_completion_path(store.path.parent, job_id))
    if (not completion or completion["status"] != "verified"
            or completion.get("record_id") != record["record_id"]
            or completion.get("verified_source", {}).get("source_id") != record["source_id"]):
        raise AIDBError("原作业的回执与此记录不一致", "not_ready")
    retention = completion.get("retained_outputs") or {}
    root = private / job_id
    folder = root / retention.get("directory", "")
    if not _plain_child(root, folder) or not folder.is_dir():
        raise AIDBError("无法取得原作业的 VASP 文件目录", "not_ready")
    files = [folder / name for name in (*OUTPUTS, RECEIPT, *required)]
    verified = [path for path in files if _plain_child(root, path) and path.is_file()]
    folder, hashes, artifacts = _verify_receipt(verified, retention["token"], required)
    if (hashes != facts.get("file_sha256")
            or not set(required) <= artifacts.keys()
            or any(artifacts[name] != reference.get("files", {}).get(name) for name in required)):
        raise AIDBError("记录所需的 VASP 重启文件已缺失或变更", "not_ready")
    return {name: (folder / name, details["sha256"]) for name, details in artifacts.items()} | {
        name: (folder / name, digest) for name, digest in hashes.items()
    }


def resolve_band_dependency(session, record_id, inputs, owner_id):
    """Verify the actual staged restart bytes against the selected static SCF record."""
    import numpy as np
    from pymatgen.core import Structure

    if not record_id:
        raise AIDBError("NSCF 需要上游 SCF 的记录 ID 和已核实的 CHGCAR", "invalid_input")
    record = session.detail(record_id)["record"]
    if record.get("calculation_type") != "static":
        raise AIDBError("NSCF 上游记录必须是静态 SCF", "invalid_input")
    facts = (record.get("source_metadata") or {}).get("vasp_facts") or {}
    source = facts.get("source") or {}
    if not source.get("restart_artifacts"):
        raise AIDBError("无法核实上游 SCF 的 CHGCAR 来源", "not_ready")
    upstream = Structure.from_dict(record["structure"])
    downstream = inputs.structure
    submitted = source.get("submitted_inputs") or {}
    required = sorted(RESTART_OUTPUTS.intersection(inputs.file_hashes))
    if upstream.species != downstream.species:
        raise AIDBError("上游 SCF 与 NSCF 的原子不一致", "invalid_input")
    displacement = upstream.frac_coords - downstream.frac_coords
    displacement -= np.rint(displacement)
    if (not np.allclose(upstream.lattice.matrix, downstream.lattice.matrix, rtol=0, atol=2e-6)
            or not np.allclose(displacement @ downstream.lattice.matrix, 0, rtol=0, atol=2e-5)
            or (submitted.get("sha256") or {}).get("POTCAR") != inputs.file_hashes["POTCAR"]):
        raise AIDBError("上游 SCF 与 NSCF 的晶胞、坐标或赝势不一致", "invalid_input")
    retained = resolve_restart_artifacts(record, required, owner_id)
    hashes = {name: inputs.file_hashes[name] for name in required}
    if any(retained[name][1] != digest for name, digest in hashes.items()):
        raise AIDBError("NSCF 的 CHGCAR 或 WAVECAR 与选定的上游 SCF 不一致", "invalid_input")
    return {"record_id": record["record_id"], "source_id": record["source_id"], "sha256": hashes}


def resolve_band_artifacts(record, dependency, owner_id):
    """Read verified band deliverables and check the archived upstream artifact identity."""
    source = record["source_metadata"]["vasp_facts"]["source"]
    upstream = source.get("upstream_scf") or {}
    if not upstream.get("source_id") or not upstream.get("record_id") or upstream.get("sha256") != dependency["sha256"]:
        raise AIDBError("归档 NSCF 的上游电荷密度或重启文件与本次输入不一致", "not_ready")
    return retained_band_artifacts(record, owner_id)


def retained_band_artifacts(record, owner_id):
    source = record["source_metadata"]["vasp_facts"]["source"]
    reference = source.get("band_artifacts") or {}
    job_id = reference.get("job_id", "")
    if not re.fullmatch("[0-9a-f]{32}", job_id):
        raise AIDBError("此记录没有已核实的 VASP 能带文件", "not_ready")
    store = RemoteJobStore(ADK_DIR / "remote-jobs.db")
    job = store.get_job(job_id)
    completion = _read_completion(_completion_path(store.path.parent, job_id))
    if (not job or job["owner_id"] != owner_id or not completion or completion["status"] != "verified"
            or completion.get("record_id") != record["record_id"]
            or completion.get("verified_source", {}).get("source_id") != record["source_id"]):
        raise AIDBError("能带记录与原作业回执不一致", "not_ready")
    retention = completion["retained_outputs"]
    root = store.path.parent / "vasp-jobs" / job_id
    folder = root / retention["directory"]
    if not _plain_child(root, folder) or not folder.is_dir():
        raise AIDBError("无法取得原作业的 VASP 能带目录", "not_ready")
    files = [folder / name for name in (*OUTPUTS, RECEIPT)]
    if not all(_plain_child(root, path) and path.is_file() for path in files):
        raise AIDBError("原作业缺少已核实的 VASP 能带来源文件", "not_ready")
    _, hashes, _ = _verify_receipt(files, retention["token"], ())
    if hashes != source.get("file_sha256"):
        raise AIDBError("已归档的 VASP 能带来源文件已改变", "not_ready")
    result = {name: (folder / name, digest) for name, digest in hashes.items()}
    for name in BAND_FILES:
        file = folder / name
        digest = reference.get("sha256", {}).get(name)
        if not _plain_child(root, file) or not file.is_file() or not digest or file_digest(file) != digest:
            raise AIDBError("已归档的 VASP 能带数据或图已缺失或改变", "not_ready")
        result[name] = (file, digest)
    return result


def collect_batch_vasp(service, job_id, destination):
    """Resume a managed download under the same job and delivery destination."""
    root = service.store.path.parent / "vasp-jobs" / job_id
    transfer = root / "batch-collection.json"
    with _completion_lock(transfer):
        job = service.store.get_job(job_id)
        if job["status"] not in {"succeeded", "collecting", "collected"}:
            raise ValueError(f"Job '{job_id}' cannot collect outputs while {job['status']}")
        saved = _read_completion(transfer)
        if saved is None and job["status"] == "collected":
            saved = {"destination": job["output_dir"]}
        if saved is None:
            if destination.exists() or destination.is_symlink():
                raise ValueError("Batch Job output destination must not exist; choose a new directory")
            saved = {"destination": str(destination)}
            _save(transfer, saved)
        if job["status"] == "collecting":
            job = service.store.transition_job(
                job_id, "succeeded", expected_revision=job["state_revision"],
            )
        evidence = job["specification"]["managed_vasp"]
        required = evidence["expected"].get("required_artifacts", ())

        def validate(artifacts):
            files = list(_collected_files({**job, "artifacts": artifacts}, root.parent))
            folder, _, verified = _verify_receipt(files, evidence["token"], required)
            declared = _read_receipt_hashes(folder / RECEIPT, evidence["token"])
            if set(required).intersection(declared) - verified.keys():
                raise AIDBError("VASP 重启产物与运行凭据不一致或下载不完整", "source_unverified")

        repair = job["status"] == "collected"
        if repair:
            completion = _read_completion(_completion_path(service.store.path.parent, job_id))
            if completion and completion["status"] == "verified":
                return job
            try:
                validate(job["artifacts"])
            except (AIDBError, OSError, ValueError):
                pass
            else:
                return job
        download = Path(mkdtemp(prefix="download-", dir=root)) / "results"
        result = service.collect_job_outputs(
            job_id, download, output_dir=saved["destination"], validate_artifacts=validate,
            repair_collected=repair,
        )
        if result["error"] or result["status"] != "collected":
            shutil.rmtree(download.parent, ignore_errors=True)
        return result


def _publish_outputs(files, destination, workspace, excluded):
    if not _plain_child(workspace, destination):
        raise AIDBError("原产物交付目录已改变", "invalid_output")
    destination.mkdir(parents=True, exist_ok=True)
    # The provider can return nested paths. Preserve its tree below the private
    # transfer root when showing files in the requested workspace directory.
    common = Path(os.path.commonpath([str(item.parent) for item in files]))
    published, failed = [], []
    for path in files:
        if path.name in excluded:
            continue
        target = destination / path.relative_to(common)
        try:
            if not _plain_child(workspace, target):
                raise ValueError("non-regular delivery path")
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and (not target.is_file() or file_digest(path) != file_digest(target)):
                raise ValueError("delivery path already contains other data")
            if not target.exists():
                with TemporaryDirectory(prefix=".matcreator-output-", dir=target.parent) as temporary:
                    copied = Path(temporary) / "file"
                    shutil.copyfile(path, copied)
                    os.link(copied, target)
        except (OSError, ValueError):
            failed.append(target.relative_to(workspace).as_posix())
        else:
            published.append(target.relative_to(workspace).as_posix())
    return sorted(published), sorted(failed)


def _read_receipt_hashes(receipt, token):
    if not receipt.is_file() or receipt.is_symlink() or receipt.stat().st_size > 1024:
        raise AIDBError("VASP 运行凭据缺失或格式异常", "source_unverified")
    lines = receipt.read_text(encoding="ascii").splitlines()
    if not lines or lines[0] != f"matcreator-vasp-v1 {token}":
        raise AIDBError("VASP 运行凭据与本次运行不一致", "source_unverified")
    hashes = {}
    for line in lines[1:]:
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match or match[2] in hashes:
            raise AIDBError("VASP 运行凭据格式异常", "source_unverified")
        hashes[match[2]] = match[1]
    if tuple(hashes)[:len(OUTPUTS)] != OUTPUTS:
        raise AIDBError("VASP 运行凭据缺少必需产物", "source_unverified")
    return hashes


def _verify_receipt(files, token, required):
    pairs = {}
    for name in (*OUTPUTS, RECEIPT):
        candidates = [path for path in files if path.name == name]
        if len(candidates) != 1:
            raise AIDBError(f"跟踪作业缺少唯一的 {name} 产物", "source_unverified")
        pairs[name] = candidates[0]
    receipt = pairs[RECEIPT]
    if any(path.parent != receipt.parent for path in pairs.values()):
        raise AIDBError("VASP 产物与运行凭据不在同一回收目录", "source_unverified")
    declared = _read_receipt_hashes(receipt, token)
    hashes = {name: file_digest(pairs[name]) for name in OUTPUTS}
    if any(declared[name] != digest for name, digest in hashes.items()):
        raise AIDBError("已回收的 VASP 产物与本次运行的凭据不一致", "source_unverified")
    artifacts = {}
    for name in required:
        candidates = [path for path in files if path.name == name and path.parent == receipt.parent]
        if len(candidates) != 1 or candidates[0].stat().st_size <= 0:
            continue
        item = candidates[0]
        digest = file_digest(item)
        if declared.get(name) == digest:
            artifacts[name] = {"sha256": digest, "size": item.stat().st_size}
    return receipt.parent, hashes, artifacts


def _restart_artifact_matches(path, raw):
    """Check that a receipted file is readable and agrees with the final cell."""
    import numpy as np
    from pymatgen.core import Structure
    from pymatgen.io.vasp import Chgcar

    structure = Structure.from_dict(raw["structure"])
    try:
        if path.name == "CHGCAR":
            charge = Chgcar.from_file(path)
            displacement = charge.structure.frac_coords - structure.frac_coords
            displacement -= np.rint(displacement)
            return (
                charge.structure.species == structure.species
                and np.allclose(charge.structure.lattice.matrix, structure.lattice.matrix, rtol=0, atol=2e-6)
                and np.allclose(displacement @ structure.lattice.matrix, 0, rtol=0, atol=2e-5)
                and charge.data["total"].size > 0
                and all(np.isfinite(grid).all() for grid in charge.data.values())
            )
        # Check the direct-access record header without loading coefficients into memory.
        with path.open("rb") as stream:
            header = np.fromfile(stream, dtype="<f8", count=3)
            if len(header) != 3 or not np.isfinite(header).all():
                return False
            record_bytes, spin, version = header
            if (version not in {45200, 45210, 53300, 53310} or spin not in {1, 2}
                    or record_bytes < 104 or record_bytes % 8):
                return False
            stream.seek(int(record_bytes))
            details = np.fromfile(stream, dtype="<f8", count=12)
            if len(details) != 12 or not np.isfinite(details).all():
                return False
            kpoints, bands, cutoff = details[:3]
            if not kpoints.is_integer() or kpoints < 1 or not bands.is_integer() or bands < 1:
                return False
            return (
                path.stat().st_size == int(record_bytes) * (2 + int(spin * kpoints * (bands + 1)))
                and np.allclose(details[3:].reshape(3, 3), structure.lattice.matrix, rtol=0, atol=2e-6)
                and cutoff == raw["calculation_parameters"].get("ENCUT", raw["calculation_parameters"].get("ENMAX"))
            )
    except (ArithmeticError, IndexError, KeyError, OSError, ValueError):
        return False


def _verify_runtime_inputs(expected, parsed, kind, parameters, lexch):
    from math import isclose

    from monty.json import jsanitize
    import numpy as np
    from pymatgen.core import Structure

    expected_kind = expected.get("calculation_type", "relaxation")
    if kind != ("static" if expected_kind == "matpes" else expected_kind):
        raise AIDBError("回收产物与提交的 VASP 计算类型不同", "source_conflict")
    initial = parsed.initial_structure
    submitted = Structure.from_dict(expected["structure"])
    if (initial.species != submitted.species or parsed.final_structure.species != submitted.species
            or not np.allclose(initial.lattice.matrix, submitted.lattice.matrix, rtol=0, atol=1e-7)
            or not np.allclose(initial.frac_coords, submitted.frac_coords, rtol=0, atol=1e-7)):
        raise AIDBError("VASP 运行的初始晶胞、坐标或原子顺序与提交输入不同", "source_conflict")
    if expected_kind in {"static", "matpes", "nscf"}:
        final = parsed.final_structure
        displacement = final.frac_coords - initial.frac_coords
        displacement -= np.rint(displacement)
        if (len(parsed.ionic_steps) != 1
                or not np.allclose(final.lattice.matrix, initial.lattice.matrix, rtol=0, atol=1e-7)
                or not np.allclose(displacement, 0, rtol=0, atol=1e-7)):
            raise AIDBError("静态 VASP 产物未保持提交的固定结构", "source_conflict")
    if list(parsed.potcar_symbols) != expected["potentials"] or list(lexch) != expected["lexch"]:
        raise AIDBError("VASP 运行的赝势标识与提交输入不同", "source_conflict")

    # VASP may omit the parallel-only NCORE tag from XML. Its exact staged
    # value is still covered by the launcher's checksum.
    supplied = dict(expected["incar"])
    echoed = jsanitize(dict(parsed.incar), strict=True)
    if "NCORE" not in echoed:
        supplied.pop("NCORE", None)
    if expected_kind == MD_TYPE and "RANDOM_SEED" not in echoed:
        # Some versions emit this only in OUTCAR, checked by the receiver.
        supplied.pop("RANDOM_SEED", None)
    def same_tag(key, actual):
        value = supplied.get(key)
        if key == "EDIFF" and type(value) in (float, int) and type(actual) in (float, int):
            # pymatgen can write a computed decimal threshold as a float one
            # rounding unit from VASP's XML representation of the same value.
            return isclose(value, actual, rel_tol=1e-15, abs_tol=0)
        return value == actual

    tags = sorted(key for key in supplied.keys() | echoed.keys() if not same_tag(key, echoed.get(key)))
    effective = ("IBRION", "NSW", "ISIF", "ISPIN", "ICHARG", "ISTART", "EDIFF", "EDIFFG",
                 "GGA", "LDAU", "LDAUL", "LDAUU", "LDAUJ", "LDAUTYPE", "LSORBIT", "LNONCOLLINEAR")
    tags.extend(key for key in effective if key in supplied and not same_tag(key, parameters.get(key)))
    if expected_kind == MD_TYPE:
        tags.extend(md_runtime_conflicts(supplied, parameters))
    if "ENCUT" in supplied and supplied["ENCUT"] != parameters.get("ENCUT", parameters.get("ENMAX")):
        tags.append("ENCUT")
    if tags:
        raise AIDBError("VASP 运行的 INCAR 设置无法核实：" + ", ".join(sorted(set(tags))), "source_conflict")

    mesh = expected["kpoints"]
    runtime_mesh = jsanitize(parsed.kpoints.as_dict(), strict=True)
    if expected_kind == "nscf":
        return
    if mesh is None:
        matched = (supplied.get("KSPACING") == parameters.get("KSPACING")
                   and supplied.get("KGAMMA", True) == parameters.get("KGAMMA")
                   and runtime_mesh.get("nkpoints") == 0)
    else:
        fields = ("nkpoints", "generation_style", "kpoints", "usershift", "kpts_weights", "coord_type",
                  "tet_number", "tet_weight", "tet_connections")
        matched = all(mesh.get(key) == runtime_mesh.get(key) for key in fields)
    if not matched:
        raise AIDBError("VASP 运行的 k 点与提交输入不同或无法核实", "source_conflict")


def workspace_managed_vasp_source(session, owner_id, folder):
    """Resolve the original tracked run for this session's delivered VASP pair."""
    store = RemoteJobStore(ADK_DIR / "remote-jobs.db")
    matched = []
    requested = {(folder / name).relative_to(session.workspace).as_posix() for name in OUTPUTS}
    for job in store.list_jobs(owner_id=owner_id, session_id=session.session_id):
        completion = get_managed_vasp_completion(
            user_id=owner_id, session_id=session.session_id, job_id=job["job_id"], store=store,
        )
        if completion and requested <= set(completion["artifact_paths"]):
            matched.append((job, completion))
    if not matched:
        return None
    if len(matched) != 1:
        raise AIDBError("交付目录对应多个跟踪作业，无法确定原运行", "source_unverified")
    job, completion = matched[0]
    if (job["status"] in {"cancelled", "terminate_requested", "terminated", "failed", "lost"}
            and completion["status"] != "verified"):
        raise AIDBError("原跟踪作业已结束或失败，无法再归档未核实的结果", "source_unverified")
    if completion["compute_status"] != "succeeded" or not completion.get("verified_source"):
        raise AIDBError("原跟踪作业的结果尚未核实，请先恢复回收", "source_unverified")
    if not completion["binding_id"]:
        raise AIDBError("原作业提交时会话未绑定 AIDB", "stale_binding")
    session.binding(completion["binding_id"])
    private = store.path.parent / "vasp-jobs"
    if job["provider"] == "bohr_sandbox":
        root = private / job["job_id"]
        evidence = json.loads((root / "sandbox-run.json").read_text(encoding="utf-8"))["evidence"]
        files = [path for path in (root / "sandbox-outputs").iterdir()
                 if _plain_child(root, path) and path.is_file()]
    else:
        evidence = job["specification"]["managed_vasp"]
        files = list(_collected_files(job, private))
    raw, private_folder, _ = _verified_raw(job, evidence, files)
    source = raw["source_metadata"]["vasp_facts"]["source"]
    if (completion["verified_source"] != _source_identity(raw)
            or any(not _plain_child(session.workspace, folder / name)
                   or file_digest(folder / name) != source["file_sha256"][name] for name in OUTPUTS)):
        raise AIDBError("工作区产物与原跟踪作业的回执不一致", "source_conflict")
    if raw["calculation_type"] == "nscf":
        hashes = completion.get("band_artifacts", {}).get("sha256", {})
        if set(hashes) != set(BAND_FILES) or any(
            not _plain_child(private_folder, private_folder / name)
            or not (private_folder / name).is_file()
            or file_digest(private_folder / name) != hashes[name]
            for name in BAND_FILES
        ):
            raise AIDBError("原跟踪作业缺少已核实的能带数据或图", "source_unverified")
        source["band_artifacts"] = completion["band_artifacts"]
    return raw


def _source_identity(raw):
    source = raw["source_metadata"]["vasp_facts"]["source"]
    return {"sha256": source["sha256"], "frame_index": raw["frame_index"], "source_id": raw["source_id"]}


def _verified_raw(job, evidence, files, *, capture_bands=None):
    expected = evidence["expected"]
    required = expected.get("required_artifacts", [])
    folder, hashes, artifacts = _verify_receipt(files, evidence["token"], required)
    md = expected.get("calculation_type") == MD_TYPE
    band_kpoints = None
    if expected.get("calculation_type") == "nscf":
        from pymatgen.io.vasp import Kpoints

        band_kpoints = Kpoints.from_dict(expected["kpoints"])
    raw = parse_vasp_result(folder, verify_inputs=lambda *args: _verify_runtime_inputs(expected, *args),
                            include_temperature=md, submitted_incar=expected["incar"] if md else None,
                            band_kpoints=band_kpoints, capture_bands=capture_bands)
    facts = raw["source_metadata"]["vasp_facts"]
    if md and "RANDOM_SEED" in expected["incar"] and facts["sampling"].get("initial_random_seed") is None:
        raise AIDBError("无法核实 VASP 运行所用的初始随机种子", "source_conflict")
    if expected.get("calculation_type") == "matpes":
        raw["calculation_type"] = facts["sampling"]["type"] = "matpes"
    source = facts["source"]
    if band_kpoints is not None:
        source["upstream_scf"] = evidence["revision"]["upstream_scf"]
    if "matpes_frame" in evidence:
        source["labeling_input"] = evidence["matpes_frame"]
    if "eos_member" in evidence:
        source["eos_member"] = evidence["eos_member"]
    if source["file_sha256"] != hashes:
        raise AIDBError("VASP 产物在核验期间发生变化", "source_unverified")
    run_identity = json.dumps([job["provider"], job["external_id"]], separators=(",", ":"))
    source["run_fingerprint"] = hashlib.sha256(run_identity.encode()).hexdigest()
    source["submitted_inputs"] = {
        **evidence["revision"], "provider": job["provider"], "run_id": job["external_id"],
        "run_fingerprint": source["run_fingerprint"],
    }
    raw["source_id"] = f'vasp:{source["run_fingerprint"]}:{raw["frame_index"]}'
    artifacts = {name: item for name, item in artifacts.items()
                 if _restart_artifact_matches(folder / name, raw)
                 and file_digest(folder / name) == item["sha256"]}
    if artifacts:
        source["restart_artifacts"] = {"job_id": job["job_id"], "files": artifacts}
    return raw, folder, artifacts


def receive_managed_vasp_result(job, adk_dir, *, failure=None, unavailable_outputs=()):
    """Validate one collected managed job and archive its proven final frame."""
    evidence = job["specification"]["managed_vasp"]
    workspace = Path(evidence["workspace"])
    expected = evidence["expected"]
    md = expected.get("calculation_type") == MD_TYPE
    path = _completion_path(adk_dir, job["job_id"])
    with _completion_lock(path):
        existing = _read_completion(path)
        if existing and existing["status"] == "verified" and not existing.get("artifact_delivery_issue"):
            return existing
        state = {"status": "pending", "compute_status": "unverified", "artifact_paths": [],
                 "binding_id": evidence["binding_id"], "record_id": None,
                 "calculation_type": evidence["expected"].get("calculation_type", "relaxation")}
        files = list(_collected_files(job, adk_dir / "vasp-jobs"))
        excluded = {LAUNCHER, RECEIPT}
        if state["calculation_type"] == "nscf":
            excluded.update(BAND_FILES)
        # Skip launch scripts when collecting broad output globs.
        try:
            if files:
                state["artifact_paths"], failed = _publish_outputs(
                    files, Path(job["output_dir"]), workspace,
                    excluded | {item.name for item in files if item.name.startswith("POTCAR")},
                )
                if failed:
                    state["artifact_delivery_issue"] = "部分计算文件未能复制到原工作区目录"
        except (AIDBError, OSError):
            state["artifact_delivery_issue"] = "无法把计算文件复制到原工作区目录"
        if unavailable_outputs:
            message = "未能下载以下请求的 VASP 产物：" + ", ".join(unavailable_outputs)
            delivery_issue = state.get("artifact_delivery_issue")
            state["artifact_delivery_issue"] = f"{delivery_issue}。{message}" if delivery_issue else message
        if existing and existing["status"] == "verified" and not md:
            completed = {**existing, **{key: value for key, value in state.items()
                                       if key in {"artifact_paths", "artifact_delivery_issue"}}}
            if "artifact_delivery_issue" not in state:
                completed.pop("artifact_delivery_issue", None)
            _save(path, completed)
            return completed

        def finish(status, **fields):
            result = {**state, **fields, "status": status}
            _save(path, result)
            return result

        if failure:
            return finish("failed", failure_kind=failure[0], reason=failure[1])

        try:
            required = expected.get("required_artifacts", [])
            band = []
            raw, folder, artifacts = _verified_raw(job, evidence, files, capture_bands=band.append)
            facts = raw["source_metadata"]["vasp_facts"]
            source = facts["source"]
            if "matpes_frame" in evidence:
                state["labeling_input"] = evidence["matpes_frame"]
            if "eos_member" in evidence:
                state["eos_member"] = evidence["eos_member"]
            if artifacts:
                state["retained_outputs"] = {
                    "directory": folder.relative_to(adk_dir / "vasp-jobs" / job["job_id"]).as_posix(),
                    "token": evidence["token"],
                }
            state.update(compute_status="succeeded", summary={
                "formula": expected["formula"], "energy_ev_per_cell": raw["energy"], "frame_index": raw["frame_index"],
            })
            missing = set(required) - artifacts.keys()
            if missing:
                state["artifact_delivery_issue"] = "未核实本次运行所需产物：" + "、".join(sorted(missing))
                return finish("failed", compute_status="incomplete", reason=state["artifact_delivery_issue"])
            if raw["calculation_type"] == "nscf":
                try:
                    write_band_files(folder, band[0], {"source_id": raw["source_id"],
                                     "upstream_scf": source["upstream_scf"], "file_sha256": source["file_sha256"]})
                    band_files = [folder / name for name in BAND_FILES]
                    delivered, failed = _publish_outputs(band_files, Path(job["output_dir"]), workspace, set())
                    state["artifact_paths"] = sorted(set(state["artifact_paths"]) | set(delivered))
                    if failed:
                        raise AIDBError("未能交付 NSCF 能带数据和图", "incomplete")
                except (AIDBError, OSError, ValueError) as exc:
                    return finish("failed", compute_status="incomplete", reason=str(exc))
                state["band_artifacts"] = source["band_artifacts"] = {"job_id": job["job_id"],
                                            "sha256": {file.name: file_digest(file) for file in band_files}}
                state["retained_outputs"] = {
                    "directory": folder.relative_to(adk_dir / "vasp-jobs" / job["job_id"]).as_posix(),
                    "token": evidence["token"],
                }
                state["summary"].update(calculation_type="nscf", upstream_record_id=source["upstream_scf"]["record_id"],
                                        band_structure=facts["band_structure"])
            verified_source = _source_identity(raw)
            if existing and existing.get("verified_source", verified_source) != verified_source:
                raise AIDBError("重新收集的 VASP 产物与原已核实来源不同", "source_conflict")
            state["verified_source"] = verified_source
            if "eos_member" in evidence:
                state["eos_result"] = {
                    "structure": raw["structure"],
                    "energy_ev_per_cell": raw["energy"],
                    "source_id": raw["source_id"],
                    "input_revision": evidence["revision"],
                    "provider": job["provider"],
                    "run_id": job["external_id"],
                    "energy_definition": raw["source_metadata"]["vasp_facts"]["labels"]["energy_definition"],
                }
            if md:
                state["temperature_requested"] = expected.get("temperature_requested", False)
                sampling = raw["source_metadata"]["vasp_facts"]["sampling"]
                state["temperature_available"] = "temperature_series_kelvin" in sampling
                try:
                    curve = temperature_curve_html(raw)
                    with TemporaryDirectory(prefix="md-temperature-", dir=path.parent) as temporary:
                        generated = Path(temporary) / CURVE_FILENAME
                        generated.write_text(curve, encoding="utf-8")
                        published, failed = _publish_outputs([generated], Path(job["output_dir"]), workspace, set())
                        if failed:
                            raise ValueError("无法把温度曲线复制到原工作区目录")
                        state["artifact_paths"] = sorted(set(state["artifact_paths"]) | set(published))
                except (AIDBError, OSError, ValueError) as exc:
                    if state["temperature_requested"] or state["temperature_available"]:
                        issue = state.get("artifact_delivery_issue")
                        state["artifact_delivery_issue"] = f"{issue}。{exc}" if issue else str(exc)
                if existing and existing["status"] == "verified":
                    return finish("verified", record_id=existing["record_id"], outcome=existing["outcome"])
            finish("pending")
            if not evidence["binding_id"]:
                return finish("failed", failure_kind="archive_failed", reason="提交时会话未绑定 AIDB")
            session = SessionAIDB(workspace, job["session_id"])
            archived = archive_verified_vasp_result(session, raw, binding_id=evidence["binding_id"])
            state["archive_receipt"] = archived["archive_receipt"]
            if not archived["readback_verified"]:
                if archived.get("database_status") in {"saved", "existing"}:
                    state["unverified_record_id"] = archived["record_id"]
                return finish("failed", failure_kind="archive_failed", reason=archived.get("message") or "AIDB 归档读回未完成")
            outcome = "added" if archived["status"] == "saved" else "existing"
            return finish("verified", record_id=archived["record_id"], outcome=outcome)
        except (AIDBError, OSError, ValueError) as exc:
            if existing and existing["status"] == "verified":
                return existing
            kind = "archive_failed" if state["compute_status"] == "succeeded" else "output_unverified"
            return finish("failed", failure_kind=kind, reason=str(exc))
