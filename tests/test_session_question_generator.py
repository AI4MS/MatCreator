import asyncio
import json
from pathlib import Path

import pytest
import yaml

from matcreator.control_plane.session_question_generator import (
    StagedSessionQuestionService,
    SUPPORTED_VERIFY_TYPES,
    validate_question,
)


def _question() -> dict:
    return {
        "id": "session_run_in",
        "task_type": "simulation",
        "capabilities": ["tool_utilization"],
        "domain": "agnostic",
        "difficulty": "easy",
        "intent": "Generate a run input file.",
        "human_prompt_seed": "Create run.in.",
        "reference_answers": [{"key": "run_in", "value": "run.in"}],
        "scoring_checklist": [
            {
                "id": "run_in",
                "criterion": "Generate run.in.",
                "verify": "artifact_exists",
                "capability": "tool_utilization",
            }
        ],
    }


class RecordingPlugin:
    name = "recording"

    def __init__(self) -> None:
        self.template = None
        self.session = None

    async def generate(
        self, *, template_path: Path, session_path: Path, output_path: Path
    ) -> None:
        self.template = json.loads(template_path.read_text(encoding="utf-8"))
        self.session = json.loads(session_path.read_text(encoding="utf-8"))
        output_path.write_text(yaml.safe_dump(_question(), sort_keys=False), encoding="utf-8")


def test_service_passes_separate_template_and_session_files(tmp_path) -> None:
    template_path = tmp_path / "template.json"
    template_path.write_text(
        json.dumps({"template_version": "test-v1", "executable_verify_types": ["artifact_exists"]}),
        encoding="utf-8",
    )
    plugin = RecordingPlugin()
    service = StagedSessionQuestionService(
        tmp_path / "staging", plugin, template_path=template_path
    )

    draft = asyncio.run(
        service.create(
            {
                "session_id": "session-1",
                "owner_id": "alice",
                "events": [{"type": "tool", "name": "write_file"}],
                "graph": {"nodes": [{"status": "success", "action": "Created run.in"}]},
            }
        )
    )

    assert plugin.template["template_version"] == "test-v1"
    assert plugin.session["schema_version"] == "matcreator.session-question-invocation.v1"
    assert plugin.session["operation"] == "generate"
    assert plugin.session["evidence"]["schema_version"] == "matcreator.session-question-trajectory.v1"
    assert plugin.session["evidence"]["events"] == [{"type": "tool", "name": "write_file"}]
    assert draft.question == _question()
    metadata = json.loads((draft.staging_path / "generation.json").read_text(encoding="utf-8"))
    assert metadata["generator_plugin"] == "recording"
    assert metadata["template_version"] == "test-v1"
    assert metadata["session_schema_version"] == "matcreator.session-question-trajectory.v1"
    assert not list((tmp_path / "staging").glob(".*.generating"))


def test_service_rejects_missing_plugin_output_and_cleans_up(tmp_path) -> None:
    class EmptyPlugin:
        name = "empty"

        async def generate(self, **_paths) -> None:
            return None

    template_path = tmp_path / "template.json"
    template_path.write_text('{"template_version": "test-v1"}', encoding="utf-8")
    service = StagedSessionQuestionService(
        tmp_path / "staging", EmptyPlugin(), template_path=template_path
    )

    with pytest.raises(ValueError, match="did not produce question.yaml"):
        asyncio.run(service.create({"session_id": "session-1", "graph": {"nodes": []}}))

    assert not list((tmp_path / "staging").glob(".*.generating"))


def test_packaged_template_verifiers_match_local_validator() -> None:
    template_path = (
        Path(__file__).parents[1]
        / "src"
        / "matcreator"
        / "question_templates"
        / "mab_qa.json"
    )
    template = json.loads(template_path.read_text(encoding="utf-8"))

    assert set(template["executable_verify_types"]) == SUPPORTED_VERIFY_TYPES


def test_local_validation_does_not_require_mat_bench() -> None:
    assert validate_question(_question()) == []


def test_refine_passes_current_question_and_validation_feedback(tmp_path) -> None:
    class RefiningPlugin(RecordingPlugin):
        async def generate(
            self, *, template_path: Path, session_path: Path, output_path: Path
        ) -> None:
            self.session = json.loads(session_path.read_text(encoding="utf-8"))
            question = _question()
            question["intent"] = "Generate a refined run input file."
            output_path.write_text(yaml.safe_dump(question, sort_keys=False), encoding="utf-8")

    template_path = tmp_path / "template.json"
    template_path.write_text('{"template_version": "test-v1"}', encoding="utf-8")
    initial_plugin = RecordingPlugin()
    service = StagedSessionQuestionService(
        tmp_path / "staging", initial_plugin, template_path=template_path
    )
    draft = asyncio.run(
        service.create({"session_id": "session-1", "graph": {"nodes": []}})
    )
    invalid_yaml = draft.as_dict()["question_yaml"].replace(
        "verify: artifact_exists", "verify: unsupported_verifier"
    )
    invalid = service.update(draft.draft_id, invalid_yaml)
    assert invalid.status == "invalid"

    refining_plugin = RefiningPlugin()
    service.generator = refining_plugin
    refined = asyncio.run(service.refine(draft.draft_id, "Fix the verifier."))

    assert refining_plugin.session["operation"] == "refine"
    assert refining_plugin.session["current_question"]["scoring_checklist"][0]["verify"] == "unsupported_verifier"
    assert refining_plugin.session["validation_errors"] == [
        "Unsupported executable verifier: unsupported_verifier"
    ]
    assert refining_plugin.session["user_instruction"] == "Fix the verifier."
    assert refined.status == "ready_for_review"
    assert refined.refinement_count == 1
    metadata = json.loads((refined.staging_path / "generation.json").read_text(encoding="utf-8"))
    assert metadata["history"][0]["feedback"] == [
        "Unsupported executable verifier: unsupported_verifier"
    ]
    assert "current_question" not in metadata["history"][0]


def test_legacy_draft_migrates_to_stable_root_on_update(tmp_path) -> None:
    legacy_root = tmp_path / "workspace" / "evaluations" / "question-drafts"
    stable_root = tmp_path / ".matcreator" / "evals" / "question-drafts"
    draft_id = "a" * 32
    draft_path = legacy_root / draft_id / "legacy_question"
    draft_path.mkdir(parents=True)
    (draft_path / "question.yaml").write_text(
        yaml.safe_dump(_question(), sort_keys=False), encoding="utf-8"
    )
    (draft_path / "generation.json").write_text(
        json.dumps(
            {
                "draft_id": draft_id,
                "status": "ready_for_review",
                "source": {"session_id": "legacy-session"},
                "evidence": {"source": {"session_id": "legacy-session"}},
                "validation_errors": [],
            }
        ),
        encoding="utf-8",
    )
    service = StagedSessionQuestionService(stable_root, legacy_roots=[legacy_root])

    assert service.get(draft_id).staging_path == draft_path
    updated = service.update(draft_id, yaml.safe_dump(_question(), sort_keys=False))

    assert updated.staging_path.is_relative_to(stable_root)
    assert not (legacy_root / draft_id).exists()
    assert (updated.staging_path / "question.yaml").is_file()