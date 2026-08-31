from __future__ import annotations

import math
from copy import deepcopy

import pytest

from matcreator.control_plane.remote_task_contract import (
    REMOTE_TASK_SCHEMA,
    WORKLOAD_PROFILES,
    RemoteTaskContractError,
    build_remote_task_envelope,
    canonical_json,
    canonical_json_digest,
    make_public_input_record,
    phase_plan_for,
    remote_task_contract_digest,
    validate_attempt,
    validate_remote_task_envelope,
    validate_task_run_id,
)


def _input(
    remote_path: str = "/work/inputs/structure.xyz",
    *,
    role: str = "structure",
    fill: str = "a",
) -> dict[str, object]:
    return {
        "role": role,
        "remote_path": remote_path,
        "size": 123,
        "sha256": fill * 64,
        "required": True,
    }


@pytest.mark.parametrize("profile", WORKLOAD_PROFILES)
def test_profiles_have_explicit_plans_and_only_execution_has_progress(
    profile: str,
) -> None:
    plan = phase_plan_for(profile)
    assert [item["phase"] for item in plan] == [
        "preparation",
        "provisioning",
        "input_staging",
        "execution",
        "validation",
        "collection",
    ]
    assert [item["phase"] for item in plan if item["progress_applicable"]] == [
        "execution"
    ]
    assert all(item["label"] for item in plan)

    plan[0]["label"] = "caller mutation"
    assert phase_plan_for(profile)[0]["label"] != "caller mutation"


def test_profiles_expose_workload_specific_execution_labels() -> None:
    labels = {
        profile: next(
            item["label"]
            for item in phase_plan_for(profile)
            if item["phase"] == "execution"
        )
        for profile in WORKLOAD_PROFILES
    }
    assert len(set(labels.values())) == len(WORKLOAD_PROFILES)


def test_envelope_and_digest_are_stable_across_input_order() -> None:
    structure = _input()
    controls = _input(
        "/work/inputs/md.yaml", role="run_controls", fill="B"
    )
    first = build_remote_task_envelope(
        task_run_id="md-run_20260828",
        attempt=2,
        workload_profile="md",
        inputs=[structure, controls],
    )
    second = build_remote_task_envelope(
        task_run_id="md-run_20260828",
        attempt=2,
        workload_profile="md",
        inputs=[controls, structure],
    )

    assert first == second
    assert first["schema_version"] == REMOTE_TASK_SCHEMA
    assert first["contract_digest"].startswith("sha256:")
    assert len(first["contract_digest"]) == len("sha256:") + 64
    assert first["inputs"][0]["remote_path"] == "/work/inputs/md.yaml"
    assert first["inputs"][0]["sha256"] == "b" * 64
    assert remote_task_contract_digest(first) == first["contract_digest"]
    assert validate_remote_task_envelope(first) == first


def test_public_input_is_a_strict_whitelist_without_local_or_secret_fields() -> None:
    record = make_public_input_record(**_input())
    assert set(record) == {"role", "remote_path", "size", "sha256", "required"}

    for forbidden in ("local_path", "credential", "api_key", "token"):
        candidate = {**_input(), forbidden: "must-not-escape"}
        with pytest.raises(RemoteTaskContractError, match="unknown fields"):
            build_remote_task_envelope(
                task_run_id="run-1",
                attempt=1,
                workload_profile="generic",
                inputs=[candidate],
            )


@pytest.mark.parametrize(
    "task_run_id",
    ["", ".", "..", "contains space", "path/segment", r"path\segment", "x" * 129],
)
def test_task_run_id_rejects_unstable_or_path_like_values(task_run_id: str) -> None:
    with pytest.raises(RemoteTaskContractError, match="task_run_id"):
        validate_task_run_id(task_run_id)


@pytest.mark.parametrize("attempt", [True, False, 0, -1, 2_147_483_648, 1.0, "1"])
def test_attempt_is_a_positive_bounded_integer(attempt: object) -> None:
    with pytest.raises(RemoteTaskContractError, match="attempt"):
        validate_attempt(attempt)


@pytest.mark.parametrize(
    "remote_path",
    [
        "relative/input.dat",
        r"C:\Users\owner\secret.dat",
        "C:/Users/owner/secret.dat",
        "//server/share/secret.dat",
        "/work/../secret.dat",
        "/work//input.dat",
        "/work/input.dat/",
        "/",
    ],
)
def test_input_rejects_local_or_noncanonical_paths(remote_path: str) -> None:
    with pytest.raises(RemoteTaskContractError, match="remote_path"):
        make_public_input_record(**_input(remote_path))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("role", "Structure File"),
        ("size", -1),
        ("size", True),
        ("sha256", "not-a-digest"),
        ("required", 1),
    ],
)
def test_input_validates_each_public_field(field: str, value: object) -> None:
    candidate = _input()
    candidate[field] = value
    with pytest.raises(RemoteTaskContractError):
        make_public_input_record(**candidate)


def test_duplicate_remote_targets_are_rejected() -> None:
    with pytest.raises(RemoteTaskContractError, match="remote_path must be unique"):
        build_remote_task_envelope(
            task_run_id="run-1",
            attempt=1,
            workload_profile="md",
            inputs=[_input(role="structure"), _input(role="restart")],
        )


def test_tampering_is_detected_in_fields_plan_inputs_and_digest() -> None:
    envelope = build_remote_task_envelope(
        task_run_id="run-1",
        attempt=1,
        workload_profile="vasp",
        inputs=[_input("/work/inputs/POSCAR", role="structure")],
    )

    changed_attempt = deepcopy(envelope)
    changed_attempt["attempt"] = 2
    with pytest.raises(RemoteTaskContractError, match="contract_digest"):
        validate_remote_task_envelope(changed_attempt)

    changed_plan = deepcopy(envelope)
    changed_plan["phase_plan"][0]["progress_applicable"] = True
    with pytest.raises(RemoteTaskContractError, match="phase_plan"):
        validate_remote_task_envelope(changed_plan)

    changed_inputs = deepcopy(envelope)
    changed_inputs["inputs"][0]["size"] += 1
    with pytest.raises(RemoteTaskContractError, match="contract_digest"):
        validate_remote_task_envelope(changed_inputs)

    bad_digest = deepcopy(envelope)
    bad_digest["contract_digest"] = "sha256:" + "0" * 64
    with pytest.raises(RemoteTaskContractError, match="contract_digest"):
        validate_remote_task_envelope(bad_digest)


def test_validator_returns_a_detached_copy() -> None:
    envelope = build_remote_task_envelope(
        task_run_id="run-1",
        attempt=1,
        workload_profile="generic",
    )
    validated = validate_remote_task_envelope(envelope)
    validated["phase_plan"][0]["label"] = "mutated"
    assert envelope["phase_plan"][0]["label"] != "mutated"


def test_canonical_json_and_digest_are_deterministic_and_finite() -> None:
    left = {"z": [2, 1], "a": "μ"}
    right = {"a": "μ", "z": [2, 1]}
    assert canonical_json(left) == '{"a":"μ","z":[2,1]}'
    assert canonical_json_digest(left) == canonical_json_digest(right)
    with pytest.raises(RemoteTaskContractError, match="canonical JSON"):
        canonical_json({"invalid": math.nan})
