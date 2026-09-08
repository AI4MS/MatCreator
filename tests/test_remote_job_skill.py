from __future__ import annotations

import re
from pathlib import Path

import pytest
from google.adk.skills import load_skill_from_dir

from matcreator.agents.execution_agent import remote_job_tools

SKILL_DIR = Path("src/matcreator/skills/remote-job")
VASP_SKILL_DIR = SKILL_DIR.parent / "vasp-pymatgen"
BOHRIUM_SKILL_DIR = SKILL_DIR.parent / "bohrium"
BATCHJOB_REF = SKILL_DIR / "references/bohr-batchjob-ref.md"
VASP_BATCHJOB_REF = VASP_SKILL_DIR / "references/bohr-batchjob.md"
MONITORING_DOC = Path("docs/remote_job_monitoring.md")


def test_remote_job_skill_loads_and_documents_lifecycle() -> None:
    loaded = load_skill_from_dir(SKILL_DIR)

    assert loaded.name == "remote-job"
    assert "submit_bohr_sandbox" in loaded.instructions
    assert "submit_bohr_batchjob" in loaded.instructions
    assert "attach_bohr_batchjob" in loaded.instructions
    assert "collect_remote_job_outputs" in loaded.instructions
    assert "needs_replanning" in loaded.instructions
    assert "pause_remote_job" in loaded.instructions
    assert "NOT supported by either bohr provider" in loaded.instructions


def test_remote_job_skill_metadata_tools_exclude_e2b_and_are_callable() -> None:
    loaded = load_skill_from_dir(SKILL_DIR)
    metadata = loaded.frontmatter.metadata or {}
    tools = metadata.get("tools", [])

    assert tools, "expected metadata.tools to be populated"
    assert "submit_bohr_batchjob" in tools
    assert "attach_bohr_batchjob" in tools
    assert "submit_bohr_job" not in tools
    assert "submit_e2b_sandbox" not in tools
    for tool_name in tools:
        assert callable(getattr(remote_job_tools, tool_name, None)), (
            f"missing callable tool function: {tool_name}"
        )


def test_remote_job_skill_reference_files_exist() -> None:
    references_dir = SKILL_DIR / "references"

    assert (references_dir / "bohr-sandbox-ref.md").exists()
    assert BATCHJOB_REF.exists()
    assert not (references_dir / "bohr-job-ref.md").exists()


def test_batchjob_reference_documents_tool_contract_and_safe_lifecycle() -> None:
    text = BATCHJOB_REF.read_text()
    table_parameters = set(re.findall(r"^\| `(\w+)` \|", text, re.MULTILINE))

    assert {
        "name", "image", "command", "machine_type", "sku_id", "project_id",
        "input_path", "out_files", "max_run_time", "max_wait_time",
    } <= table_parameters
    assert not {
        "job_name", "image_address", "input_directory", "result_path",
    } & table_parameters
    for required in (
        "exactly one", "mutually exclusive", "BOHRIUM_PROJECT_ID",
        '"24h"', '"30m"', "--dry-run", "--input", "--out-file",
        "symlinks", "empty directories", "special files", "nonempty directory",
        "relative to the step workspace", "root of the remote job's working directory",
        "batchjob_id", "job_id", "jobId", "new, nonexistent", "durable",
        "no-op", "safely extracts", "two hours", "needs_replanning",
        "status_name", "terminal", "errorMessage", "errorCode",
        "confirmed: false", "REMOTE JOB ALREADY SUBMITTED",
        "unsupported", "never submit again automatically",
    ):
        assert required in text, f"missing Batch Job guidance: {required}"
    for status in (
        "prepared", "pending", "active", "running", "succeeded",
        "failed", "deleted", "killed", "unknown",
    ):
        assert f"`{status}`" in text
    for command in ("describe", "download", "kill"):
        assert f'bohr batchjob {command} "$BATCHJOB_ID"' in text
    assert "--dest ./new-results" in text
    assert "numeric backend status or `exitCode`" in text
    assert "attach_bohr_batchjob" in text


def test_remote_job_guidance_assigns_monitoring_to_harness() -> None:
    instructions = load_skill_from_dir(SKILL_DIR).instructions
    assert "harness" in instructions.lower()
    documentation = MONITORING_DOC.read_text()
    for required in ("busy", "durable", "attach_bohr_batchjob", "Deleted sessions", "credentials"):
        assert required in documentation


def test_vasp_skill_loads_with_tracked_batchjob_dependency() -> None:
    loaded = load_skill_from_dir(VASP_SKILL_DIR)
    metadata = loaded.frontmatter.metadata or {}

    assert "remote-job" in metadata.get("dependent_skills", [])
    assert "bohrium" in metadata.get("dependent_skills", [])
    assert 'path="references/bohr-batchjob.md"' in loaded.instructions
    assert 'path="references/remote-sandbox-execution.md"' in loaded.instructions
    assert 'path="references/bohr.md"' not in loaded.instructions
    assert "50 jobs without explicit user approval" in loaded.instructions
    assert "Older submission pointers" in loaded.instructions
    assert "NSCF `CHGCAR`" in loaded.instructions


def test_vasp_batchjob_reference_preserves_execution_and_result_safety() -> None:
    text = VASP_BATCHJOB_REF.read_text()

    for required in (
        "submit_bohr_batchjob(", "BOHRIUM_VASP_IMAGE",
        'input_path="./vasp-scf-Al"', "VERIFIED_BATCHJOB_MACHINE_TYPE",
        "bohr batchjob machine list -o json", "export FI_PROVIDER=tcp",
        "source /opt/intel/oneapi/setvars.sh", "mpirun -np <N_CORES>",
        "actual allocated CPU core count", "user_incar_settings",
        "vasprun.xml", "OUTCAR", "OSZICAR", "CONTCAR", "CHGCAR",
        '"log"', '"err"', '"stdout.log"', '"stderr.log"',
        "max_run_time=\"24h\"", "max_wait_time=\"30m\"",
        "**50 jobs**", "explicit user approval", "collect_remote_job_outputs",
        "new and nonexistent", "no-op", "not VASP convergence",
        "electronic convergence", "ionic convergence",
        "needs_replanning", "user_control", "never blindly retry",
    ):
        assert required in text, f"missing VASP Batch Job guidance: {required}"
    assert text.index("export FI_PROVIDER=tcp") < text.index(
        "source /opt/intel/oneapi/setvars.sh"
    ) < text.index("mpirun -np <N_CORES>")


@pytest.mark.parametrize(
    "path",
    [
        SKILL_DIR / "SKILL.md",
        BATCHJOB_REF,
        BOHRIUM_SKILL_DIR / "SKILL.md",
        VASP_SKILL_DIR / "SKILL.md",
        VASP_BATCHJOB_REF,
        MONITORING_DOC,
    ],
)
def test_active_batchjob_guidance_does_not_route_to_legacy_commands(path: Path) -> None:
    text = path.read_text()

    assert "bohr-job-ref.md" not in text
    assert not re.search(r"\bbohr (?:job|job_group|node)\b", text)
    assert "bohr sandbox machine list" not in text
    assert "--backward_files" not in text
    assert "--input_directory" not in text
    assert "submit_bohr_batchjob" in text
    if path != VASP_SKILL_DIR / "SKILL.md":
        assert "bohr batchjob machine list -o json" in text


@pytest.mark.parametrize(
    "path",
    [
        VASP_SKILL_DIR / "references/bohr.md",
        BOHRIUM_SKILL_DIR / "references/bohrium-cli-ref.md",
        BOHRIUM_SKILL_DIR / "references/bohrium-machines-ref.md",
    ],
)
def test_retained_legacy_references_are_labeled_and_redirected(path: Path) -> None:
    header = "\n".join(path.read_text().splitlines()[:12])

    assert "Historical" in header
    assert "Legacy" in header
    assert "bohr-batchjob" in header


@pytest.mark.parametrize(
    "path",
    [
        SKILL_DIR / "SKILL.md",
        BATCHJOB_REF,
        BOHRIUM_SKILL_DIR / "SKILL.md",
        BOHRIUM_SKILL_DIR / "references/bohrium-cli-ref.md",
        BOHRIUM_SKILL_DIR / "references/bohrium-machines-ref.md",
        VASP_SKILL_DIR / "SKILL.md",
        VASP_SKILL_DIR / "references/bohr.md",
        VASP_BATCHJOB_REF,
        MONITORING_DOC,
    ],
)
def test_migrated_document_reference_links_resolve(path: Path) -> None:
    text = path.read_text()

    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
        if "://" in target or target.startswith("#"):
            continue
        assert (path.parent / target.split("#", 1)[0]).exists(), (path, target)
    for skill_name, resource in re.findall(
        r'load_skill_resource\(skill_name="([^"]+)", path="([^"]+)"\)', text
    ):
        assert (SKILL_DIR.parent / skill_name / resource).is_file(), (path, resource)
