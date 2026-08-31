"""Provider-neutral public contract for durable remote task submissions.

The contract intentionally contains only stable, replay-safe submission data.
It does not contain provider credentials, local filesystem paths, timestamps,
live handles, or lifecycle behavior.  Remote-job services may persist or
transport an envelope produced here, but this module performs no I/O.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any, Final

REMOTE_TASK_SCHEMA: Final = "mc.remote-task.v1"
WORKLOAD_PROFILES: Final = ("md", "vasp", "training", "generic")

_TASK_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ROLE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_MAX_ATTEMPT = 2_147_483_647

_PHASE_LABELS: Final[dict[str, tuple[tuple[str, str], ...]]] = {
    "md": (
        ("preparation", "Prepare MD structure, model, and run controls"),
        ("provisioning", "Provision the molecular-dynamics runtime"),
        ("input_staging", "Stage MD inputs in the remote workspace"),
        ("execution", "Run molecular dynamics"),
        ("validation", "Validate trajectory and thermodynamic outputs"),
        ("collection", "Collect trajectory, logs, and restart artifacts"),
    ),
    "vasp": (
        ("preparation", "Prepare and validate the VASP input set"),
        ("provisioning", "Provision the VASP runtime"),
        ("input_staging", "Stage POSCAR, INCAR, KPOINTS, and potentials"),
        ("execution", "Run the VASP calculation"),
        ("validation", "Validate completion and convergence evidence"),
        ("collection", "Collect VASP outputs and restart artifacts"),
    ),
    "training": (
        ("preparation", "Prepare dataset, model, and training controls"),
        ("provisioning", "Provision the training runtime"),
        ("input_staging", "Stage datasets and training configuration"),
        ("execution", "Run model training"),
        ("validation", "Validate metrics and checkpoints"),
        ("collection", "Collect checkpoints, metrics, and logs"),
    ),
    "generic": (
        ("preparation", "Prepare and validate task inputs"),
        ("provisioning", "Provision the task runtime"),
        ("input_staging", "Stage inputs in the remote workspace"),
        ("execution", "Run the remote task"),
        ("validation", "Validate task completion evidence"),
        ("collection", "Collect declared task outputs"),
    ),
}

_INPUT_FIELDS: Final = frozenset(
    {"role", "remote_path", "size", "sha256", "required"}
)
_ENVELOPE_FIELDS: Final = frozenset(
    {
        "schema_version",
        "task_run_id",
        "attempt",
        "workload_profile",
        "phase_plan",
        "inputs",
        "contract_digest",
    }
)


class RemoteTaskContractError(ValueError):
    """Raised when public remote-task contract data is invalid."""


def _require_exact_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise RemoteTaskContractError(f"{field} must be a string")
    return value


def validate_task_run_id(task_run_id: Any) -> str:
    """Validate and return one stable, path-safe task run identifier."""

    value = _require_exact_string(task_run_id, field="task_run_id")
    if value in {".", ".."} or not _TASK_RUN_ID.fullmatch(value):
        raise RemoteTaskContractError(
            "task_run_id must be 1-128 path-safe ASCII characters"
        )
    return value


def validate_attempt(attempt: Any) -> int:
    """Validate and return the positive, bounded submission attempt number."""

    if isinstance(attempt, bool) or not isinstance(attempt, int):
        raise RemoteTaskContractError("attempt must be an integer")
    if not 1 <= attempt <= _MAX_ATTEMPT:
        raise RemoteTaskContractError(
            f"attempt must be between 1 and {_MAX_ATTEMPT}"
        )
    return attempt


def validate_workload_profile(workload_profile: Any) -> str:
    """Validate one explicit workload profile without guessing from files."""

    value = _require_exact_string(workload_profile, field="workload_profile")
    if value not in _PHASE_LABELS:
        supported = ", ".join(WORKLOAD_PROFILES)
        raise RemoteTaskContractError(
            f"workload_profile must be one of: {supported}"
        )
    return value


def phase_plan_for(workload_profile: Any) -> list[dict[str, Any]]:
    """Return a fresh, serializable phase plan for a workload profile.

    Progress is meaningful only while the task is in ``execution``.  Other
    phases report discrete state transitions rather than invented percentages.
    """

    profile = validate_workload_profile(workload_profile)
    return [
        {
            "phase": phase,
            "label": label,
            "progress_applicable": phase == "execution",
        }
        for phase, label in _PHASE_LABELS[profile]
    ]


def _validate_remote_path(remote_path: Any) -> str:
    value = _require_exact_string(remote_path, field="remote_path")
    if (
        not value
        or "\x00" in value
        or "\\" in value
        or value.startswith("//")
        or _WINDOWS_ABSOLUTE_PATH.match(value)
    ):
        raise RemoteTaskContractError(
            "remote_path must be a canonical absolute POSIX path"
        )

    path = PurePosixPath(value)
    if (
        not path.is_absolute()
        or value == "/"
        or value.endswith("/")
        or any(part in {".", ".."} for part in value.split("/"))
        or str(path) != value
    ):
        raise RemoteTaskContractError(
            "remote_path must be a canonical absolute POSIX file path"
        )
    return value


def make_public_input_record(
    *,
    role: Any,
    remote_path: Any,
    size: Any,
    sha256: Any,
    required: Any = True,
) -> dict[str, Any]:
    """Build the strictly public record for one staged input file."""

    role_value = _require_exact_string(role, field="role")
    if not _ROLE.fullmatch(role_value):
        raise RemoteTaskContractError(
            "role must be a lowercase path-safe token of at most 64 characters"
        )
    path_value = _validate_remote_path(remote_path)
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise RemoteTaskContractError("size must be a non-negative integer")
    hash_value = _require_exact_string(sha256, field="sha256")
    if not _SHA256.fullmatch(hash_value):
        raise RemoteTaskContractError("sha256 must contain exactly 64 hex digits")
    if not isinstance(required, bool):
        raise RemoteTaskContractError("required must be a boolean")

    return {
        "role": role_value,
        "remote_path": path_value,
        "size": size,
        "sha256": hash_value.lower(),
        "required": required,
    }


def _normalize_input_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise RemoteTaskContractError("each input must be an object")
    keys = frozenset(record)
    if keys != _INPUT_FIELDS:
        missing = sorted(_INPUT_FIELDS - keys)
        unknown = sorted(str(key) for key in keys - _INPUT_FIELDS)
        details: list[str] = []
        if missing:
            details.append(f"missing fields: {', '.join(missing)}")
        if unknown:
            details.append(f"unknown fields: {', '.join(unknown)}")
        raise RemoteTaskContractError(
            "input record must contain only public fields (" + "; ".join(details) + ")"
        )
    return make_public_input_record(
        role=record["role"],
        remote_path=record["remote_path"],
        size=record["size"],
        sha256=record["sha256"],
        required=record["required"],
    )


def normalize_public_inputs(inputs: Any) -> list[dict[str, Any]]:
    """Validate, deduplicate, and deterministically order public inputs."""

    if isinstance(inputs, (str, bytes, bytearray)) or not isinstance(inputs, Sequence):
        raise RemoteTaskContractError("inputs must be a sequence of input records")
    normalized = [_normalize_input_record(record) for record in inputs]
    remote_paths = [record["remote_path"] for record in normalized]
    if len(remote_paths) != len(set(remote_paths)):
        raise RemoteTaskContractError("remote_path must be unique within one task")
    return sorted(normalized, key=lambda record: (record["remote_path"], record["role"]))


def canonical_json(value: Any) -> str:
    """Serialize a JSON value in the deterministic form used for digests."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise RemoteTaskContractError("value is not canonical JSON data") from exc


def canonical_json_digest(value: Any) -> str:
    """Return the SHA-256 digest of :func:`canonical_json`."""

    payload = canonical_json(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def remote_task_contract_digest(envelope: Mapping[str, Any]) -> str:
    """Compute an envelope digest, excluding the self-referential digest field."""

    if not isinstance(envelope, Mapping):
        raise RemoteTaskContractError("envelope must be an object")
    payload = dict(envelope)
    payload.pop("contract_digest", None)
    return canonical_json_digest(payload)


def build_remote_task_envelope(
    *,
    task_run_id: Any,
    attempt: Any,
    workload_profile: Any,
    inputs: Any = (),
) -> dict[str, Any]:
    """Build a deterministic ``mc.remote-task.v1`` public envelope."""

    profile = validate_workload_profile(workload_profile)
    envelope: dict[str, Any] = {
        "schema_version": REMOTE_TASK_SCHEMA,
        "task_run_id": validate_task_run_id(task_run_id),
        "attempt": validate_attempt(attempt),
        "workload_profile": profile,
        "phase_plan": phase_plan_for(profile),
        "inputs": normalize_public_inputs(inputs),
    }
    envelope["contract_digest"] = remote_task_contract_digest(envelope)
    return envelope


def validate_remote_task_envelope(envelope: Any) -> dict[str, Any]:
    """Validate a complete envelope and return a detached canonical copy."""

    if not isinstance(envelope, Mapping):
        raise RemoteTaskContractError("envelope must be an object")
    keys = frozenset(envelope)
    if keys != _ENVELOPE_FIELDS:
        raise RemoteTaskContractError(
            "envelope must contain exactly the mc.remote-task.v1 public fields"
        )
    if envelope["schema_version"] != REMOTE_TASK_SCHEMA:
        raise RemoteTaskContractError(
            f"schema_version must be {REMOTE_TASK_SCHEMA}"
        )

    profile = validate_workload_profile(envelope["workload_profile"])
    validate_task_run_id(envelope["task_run_id"])
    validate_attempt(envelope["attempt"])

    expected_plan = phase_plan_for(profile)
    if envelope["phase_plan"] != expected_plan:
        raise RemoteTaskContractError(
            "phase_plan must match the declared workload_profile"
        )

    normalized_inputs = normalize_public_inputs(envelope["inputs"])
    if envelope["inputs"] != normalized_inputs:
        raise RemoteTaskContractError("inputs must use canonical ordering and values")

    digest = envelope["contract_digest"]
    if not isinstance(digest, str) or digest != remote_task_contract_digest(envelope):
        raise RemoteTaskContractError("contract_digest does not match the envelope")
    return deepcopy(dict(envelope))


__all__ = [
    "REMOTE_TASK_SCHEMA",
    "WORKLOAD_PROFILES",
    "RemoteTaskContractError",
    "build_remote_task_envelope",
    "canonical_json",
    "canonical_json_digest",
    "make_public_input_record",
    "normalize_public_inputs",
    "phase_plan_for",
    "remote_task_contract_digest",
    "validate_attempt",
    "validate_remote_task_envelope",
    "validate_task_run_id",
    "validate_workload_profile",
]
