from pathlib import Path
import logging

from matcreator.agents.execution_agent.step_executor import StepExecutorResult
from matcreator.agents.execution_agent.step_executor_runner import _verify_step_result_artifacts


def test_success_with_missing_artifact_requires_replanning(tmp_path, caplog):
    existing_artifact = tmp_path / "result.txt"
    existing_artifact.write_text("ok", encoding="utf-8")
    missing_artifact = tmp_path / "missing.txt"

    result = StepExecutorResult(
        status="success",
        key_results="Generated result files.",
        concise_summary="Generated result files.",
        artifacts=[str(existing_artifact), str(missing_artifact)],
    )

    caplog.set_level(logging.WARNING)
    verified, missing_artifacts = _verify_step_result_artifacts(
        result,
        allowed_roots=[tmp_path],
    )

    assert verified.status == "needs_replanning"
    assert verified.artifacts == [str(existing_artifact)]
    assert missing_artifacts == [str(missing_artifact)]
    assert str(missing_artifact) in (verified.replan_reason or "")
    assert verified.concise_summary == verified.replan_reason
    assert verified.key_results == verified.replan_reason
    assert str(missing_artifact) in caplog.text
    assert "claimed artifact path(s)" in caplog.text


def test_success_accepts_existing_file_and_directory_artifacts(tmp_path):
    file_artifact = tmp_path / "result.txt"
    file_artifact.write_text("ok", encoding="utf-8")
    directory_artifact = tmp_path / "outputs"
    directory_artifact.mkdir()

    result = StepExecutorResult(
        status="success",
        key_results="Generated artifacts.",
        concise_summary="Generated artifacts.",
        artifacts=[str(file_artifact), str(directory_artifact)],
    )

    verified, missing_artifacts = _verify_step_result_artifacts(
        result,
        allowed_roots=[tmp_path],
    )

    assert verified.status == "success"
    assert verified.artifacts == [str(file_artifact), str(directory_artifact)]
    assert missing_artifacts == []


def test_relative_artifact_path_is_not_treated_as_verified(tmp_path, monkeypatch):
    relative_artifact = Path("result.txt")
    (tmp_path / relative_artifact).write_text("ok", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = StepExecutorResult(
        status="success",
        key_results="Generated artifact.",
        concise_summary="Generated artifact.",
        artifacts=[str(relative_artifact)],
    )

    verified, missing_artifacts = _verify_step_result_artifacts(
        result,
        allowed_roots=[tmp_path],
    )

    assert verified.status == "needs_replanning"
    assert verified.artifacts == []
    assert missing_artifacts == [str(relative_artifact)]


def test_existing_artifact_outside_allowed_roots_requires_replanning(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_artifact = tmp_path / "outside.txt"
    outside_artifact.write_text("not from this step", encoding="utf-8")

    result = StepExecutorResult(
        status="success",
        key_results="Generated artifact.",
        concise_summary="Generated artifact.",
        artifacts=[str(outside_artifact)],
    )

    verified, missing_artifacts = _verify_step_result_artifacts(
        result,
        allowed_roots=[workspace],
    )

    assert verified.status == "needs_replanning"
    assert verified.artifacts == []
    assert missing_artifacts == [str(outside_artifact)]
