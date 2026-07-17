import asyncio
import logging
from pathlib import Path

from matcreator.llm_cards import LLMCard
from matcreator.agents.execution_agent.step_executor import (
    StepExecutorInput,
    StepExecutorResult,
    build_step_executor_agent,
)
from matcreator.agents.execution_agent.step_executor_runner import (
    _artifact_allowed_roots,
    _build_step_content,
    _schedule_step_runner_cleanup,
    _verify_step_result_artifacts,
)
from matcreator.agents.session_log import SESSION_ARTIFACTS_KEY


def test_step_executor_registers_tracked_e2b_tools() -> None:
    agent = build_step_executor_agent(LLMCard(name="test", model="test-model"))
    tool_names = {tool.name for tool in agent.tools if hasattr(tool, "name")}

    assert {
        "submit_e2b_sandbox",
        "get_e2b_job_status",
        "run_e2b_command",
        "upload_e2b_input",
        "pause_e2b_sandbox",
        "terminate_e2b_sandbox",
    } <= tool_names


class _FakeState:
    def __init__(self, data):
        self._data = data

    def to_dict(self):
        return self._data


def test_cancelled_runner_cleanup_is_scheduled_without_waiting():
    class FakeRunner:
        def __init__(self) -> None:
            self.closed = False
            self.closed_event = asyncio.Event()

        async def close(self) -> None:
            self.closed = True
            self.closed_event.set()

    async def exercise() -> None:
        release = asyncio.Event()

        async def slow_task() -> None:
            await release.wait()

        runner = FakeRunner()
        task = asyncio.create_task(slow_task())
        _schedule_step_runner_cleanup(runner, (task,))

        await asyncio.sleep(0)
        assert not runner.closed

        release.set()
        await asyncio.wait_for(runner.closed_event.wait(), timeout=1)
        assert runner.closed

    asyncio.run(exercise())


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


def test_output_dir_constrains_artifacts_without_restricting_workspace_access(tmp_path):
    workspace = tmp_path / "workspace"
    output_dir = workspace / "case_001"
    output_dir.mkdir(parents=True)
    shared_artifact = workspace / "shared.txt"
    shared_artifact.write_text("readable shared file, but not a valid output", encoding="utf-8")
    output_artifact = output_dir / "result.txt"
    output_artifact.write_text("valid output", encoding="utf-8")

    result = StepExecutorResult(
        status="success",
        key_results="Generated artifacts.",
        concise_summary="Generated artifacts.",
        artifacts=[str(shared_artifact), str(output_artifact)],
    )

    verified, missing_artifacts = _verify_step_result_artifacts(
        result,
        allowed_roots=_artifact_allowed_roots(workspace, [], output_dir),
    )

    assert verified.status == "needs_replanning"
    assert verified.artifacts == [str(output_artifact)]
    assert missing_artifacts == [str(shared_artifact)]


def test_multimodal_card_attaches_referenced_image_to_step_content(tmp_path):
    image_path = tmp_path / "plot.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    step_input = StepExecutorInput(
        step_number=1,
        action=f"Analyze the image at {image_path}",
        suggested_skills=["plot"],
        workspace_dir=str(tmp_path),
    )

    content, input_images = _build_step_content(
        step_input,
        llm_card=LLMCard(
            name="vision",
            model="openai/vision-model",
            modalities=("text", "image"),
        ),
        workspace_dir=tmp_path,
        state={},
    )

    assert input_images == [str(image_path)]
    assert len(content.parts) == 2
    assert content.parts[1].inline_data.mime_type == "image/png"


def test_text_only_card_does_not_attach_referenced_image(tmp_path):
    image_path = tmp_path / "plot.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    step_input = StepExecutorInput(
        step_number=1,
        action=f"Analyze the image at {image_path}",
        suggested_skills=["plot"],
        workspace_dir=str(tmp_path),
    )

    content, input_images = _build_step_content(
        step_input,
        llm_card=LLMCard(name="text", model="openai/text-model"),
        workspace_dir=tmp_path,
        state={},
    )

    assert input_images == []
    assert len(content.parts) == 1


def test_multimodal_card_can_attach_session_image_artifact_when_image_requested(tmp_path):
    image_path = tmp_path / "previous_plot.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    step_input = StepExecutorInput(
        step_number=1,
        action="Analyze the previous plot.",
        suggested_skills=["plot"],
        workspace_dir=str(tmp_path),
    )

    content, input_images = _build_step_content(
        step_input,
        llm_card=LLMCard(
            name="vision",
            model="openai/vision-model",
            tags=("vision",),
        ),
        workspace_dir=tmp_path,
        state={SESSION_ARTIFACTS_KEY: [str(image_path)]},
    )

    assert input_images == [str(image_path)]
    assert len(content.parts) == 2


def test_multimodal_card_accepts_adk_state_object_for_session_image_artifacts(tmp_path):
    image_path = tmp_path / "previous_plot.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    step_input = StepExecutorInput(
        step_number=1,
        action="Describe the previous image.",
        suggested_skills=["plot"],
        workspace_dir=str(tmp_path),
    )

    content, input_images = _build_step_content(
        step_input,
        llm_card=LLMCard(
            name="vision",
            model="openai/vision-model",
            modalities=("text", "image"),
        ),
        workspace_dir=tmp_path,
        state=_FakeState({SESSION_ARTIFACTS_KEY: [str(image_path)]}),
    )

    assert input_images == [str(image_path)]
    assert len(content.parts) == 2


def test_step_executor_retries_only_malformed_streamed_tool_arguments():
    agent = build_step_executor_agent(LLMCard(name="test", model="openai/test"))

    assert agent.retry_config is not None
    assert agent.retry_config.max_attempts == 2
    assert agent.retry_config.exceptions == ["JSONDecodeError"]
