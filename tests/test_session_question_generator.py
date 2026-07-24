import asyncio
import json
import sys
import types
from pathlib import Path

import pytest
import yaml

from matcreator.control_plane.session_question_generator import (
    BuiltinLlmQuestionGeneratorPlugin,
    MkbProjectionQuestionGeneratorPlugin,
    NoQuestionExtracted,
    QuestionGenerationDiagnosticError,
    QuestionTemplateStore,
    build_session_question_generator,
    has_observable_session_question_evidence,
    list_session_question_generators,
    StagedSessionQuestionService,
    validate_question,
)


class FakeBenchmarkBankClient:
    """Records bank/question publish calls for assertions."""

    def __init__(self) -> None:
        self.ensured_banks: list[tuple[str, str | None]] = []
        self.published: list[dict] = []

    async def ensure_bank(self, bank_id: str, *, display_name: str | None = None) -> dict:
        self.ensured_banks.append((bank_id, display_name))
        return {"bank_id": bank_id, "display_name": display_name}

    async def publish_question(
        self, bank_id: str, *, question: dict, data_files: list[tuple[str, Path]] | None = None
    ) -> dict:
        self.published.append(
            {
                "bank_id": bank_id,
                "question": question,
                "data_files": [(path, source.read_bytes()) for path, source in (data_files or [])],
            }
        )
        return {"question_id": question["id"], "bank_id": bank_id}


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


def test_builtin_generator_prompt_limits_keys_to_template_schema(monkeypatch, tmp_path) -> None:
    template_path = tmp_path / "template.json"
    template_path.write_text(
        json.dumps(
            {
                "extraction_schema": {
                    "questions": {"item_schema": {"id": {}, "intent": {}, "optional": {}}}
                },
                "executable_verify_types": ["artifact_exists"],
            }
        ),
        encoding="utf-8",
    )
    session_path = tmp_path / "session.json"
    session_path.write_text('{"operation": "generate"}', encoding="utf-8")
    captured: dict[str, object] = {}

    async def acompletion(**kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=types.SimpleNamespace(content='{"id": "question"}'))]
        )

    monkeypatch.setitem(sys.modules, "litellm", types.SimpleNamespace(acompletion=acompletion))

    plugin = BuiltinLlmQuestionGeneratorPlugin(model="test-model")
    asyncio.run(plugin.generate(
        template_path=template_path,
        session_path=session_path,
        output_path=tmp_path / "question.yaml",
    ))

    prompt = captured["messages"][0]["content"]
    assert "one bare question object as JSON, not a questions wrapper" in prompt
    assert '["id", "intent", "optional"]' in prompt
    assert "Do not add keys that are absent from that schema" in prompt


def test_generator_registry_exposes_selectable_agents(monkeypatch) -> None:
    for variable in (
        "MKB_EXTRACTION_MODEL",
        "MKB_LLM_API_KEY",
        "MKB_LLM_API_BASE",
        "LLM_MODEL",
        "LLM_API_KEY",
        "LLM_BASE_URL",
    ):
        monkeypatch.delenv(variable, raising=False)
    generators = list_session_question_generators()

    assert [generator["generator_id"] for generator in generators] == [
        "builtin_llm", "mkb_projection"
    ]
    mkb = build_session_question_generator(
        "mkb_projection",
        {
            "llm": {
                "model": "matcreator-model",
                "api_key": "matcreator-key",
                "base_url": "https://matcreator.example/v1",
            },
            "session_question_generator": {},
        },
    )
    assert isinstance(mkb, MkbProjectionQuestionGeneratorPlugin)
    assert mkb.model == "matcreator-model"
    assert mkb.api_key == "matcreator-key"
    assert mkb.base_url == "https://matcreator.example/v1"
    with pytest.raises(ValueError, match="Unknown session question generator plugin"):
        build_session_question_generator("not-registered", {})


def test_mkb_projection_generator_uses_template_and_returns_yaml(monkeypatch, tmp_path) -> None:
    template_path = tmp_path / "template.json"
    template_path.write_text(
        json.dumps(
            {
                "domain": "computational materials science",
                "purpose": "qa_benchmark",
                "system_prompt": "Use the supplied template.",
                "extraction_schema": {"questions": {"type": "list"}},
                "executable_verify_types": ["artifact_exists"],
            }
        ),
        encoding="utf-8",
    )
    session_path = tmp_path / "session.json"
    session_path.write_text('{"operation": "generate", "evidence": {"steps": []}}', encoding="utf-8")
    captured: dict[str, object] = {}

    def build_projection_prompt(**kwargs):
        captured["prompt_args"] = kwargs
        return "MKB projection prompt"

    class Agent:
        def __init__(self, **kwargs) -> None:
            captured["agent"] = kwargs

    class AgentRunner:
        def __init__(self, **kwargs) -> None:
            captured["runner"] = kwargs

        async def create_session(self, **kwargs) -> None:
            captured["created_session"] = kwargs

        async def run(self, **kwargs):
            captured["run"] = kwargs
            return types.SimpleNamespace(success=True, error=None, final_text=json.dumps(_question()))

    def LiteLlm(**kwargs):
        captured["model"] = kwargs
        return "mkb-model"

    monkeypatch.setattr(
        MkbProjectionQuestionGeneratorPlugin,
        "_load_mkb_components",
        staticmethod(lambda: (build_projection_prompt, AgentRunner, Agent, LiteLlm)),
    )

    plugin = MkbProjectionQuestionGeneratorPlugin(
        model="mkb-test-model", api_key="test-key", base_url="https://example.test/v1"
    )
    asyncio.run(plugin.generate(
        template_path=template_path,
        session_path=session_path,
        output_path=tmp_path / "question.yaml",
    ))

    assert captured["prompt_args"]["purpose"] == "qa_benchmark"
    assert captured["model"] == {
        "model": "mkb-test-model",
        "api_key": "test-key",
        "base_url": "https://example.test/v1",
    }
    assert captured["agent"]["tools"] == []
    assert "MatCreator session adapter" in captured["agent"]["instruction"]
    assert "artifact_exists" in captured["agent"]["instruction"]
    assert json.loads(captured["run"]["message"])["operation"] == "generate"
    assert yaml.safe_load((tmp_path / "question.yaml").read_text(encoding="utf-8")) == _question()


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("```json\n{\"id\": \"question\"}\n```", {"id": "question"}),
        ("Here is the question:\n{\"id\": \"question\"}", {"id": "question"}),
        ("```yaml\nid: question\n```", {"id": "question"}),
        ("questions:\n  - id: question\n", {"id": "question"}),
    ],
)
def test_mkb_projection_response_parser_accepts_common_structured_wrappers(response, expected) -> None:
    assert MkbProjectionQuestionGeneratorPlugin._question_from_response(response) == expected


def test_mkb_projection_response_parser_rejects_unstructured_reply() -> None:
    with pytest.raises(QuestionGenerationDiagnosticError, match="JSON or YAML question object") as error:
        MkbProjectionQuestionGeneratorPlugin._question_from_response("I cannot extract a question.")
    assert error.value.diagnostics == {
        "generator": "mkb_projection",
        "stage": "parse_response",
        "response_length": 28,
        "response_preview": "I cannot extract a question.",
        "expected_format": "One JSON or YAML question object (or a questions list with exactly one item).",
    }


@pytest.mark.parametrize(
    "response",
    [
        '{"no_qa_extracted": true, "reason": "No runnable task is grounded in the session."}',
        '{"questions": []}',
    ],
)
def test_mkb_projection_response_parser_reports_no_question_extracted(response) -> None:
    with pytest.raises(NoQuestionExtracted):
        MkbProjectionQuestionGeneratorPlugin._question_from_response(response)


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


def test_service_rejects_a_session_without_observable_evidence(tmp_path) -> None:
    template_path = tmp_path / "template.json"
    template_path.write_text('{"template_version": "test-v1"}', encoding="utf-8")
    service = StagedSessionQuestionService(
        tmp_path / "staging", RecordingPlugin(), template_path=template_path
    )

    with pytest.raises(NoQuestionExtracted, match="no observable execution steps"):
        asyncio.run(service.create({"session_id": "empty-session", "graph": {"nodes": []}}))
    assert not (tmp_path / "staging").exists()
    assert not has_observable_session_question_evidence(
        {"steps": [], "events": [], "artifacts": []}
    )


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
        asyncio.run(
            service.create(
                {"session_id": "session-1", "graph": {"nodes": []}, "events": [{"type": "tool"}]}
            )
        )

    assert not list((tmp_path / "staging").glob(".*.generating"))


def test_packaged_template_verifiers_match_benchmark_schema() -> None:
    template_path = (
        Path(__file__).parents[1]
        / "src"
        / "matcreator"
        / "question_templates"
        / "mab_qa.json"
    )
    template = json.loads(template_path.read_text(encoding="utf-8"))

    from mat_bench.schemas import VerifyLiteral

    assert set(template["executable_verify_types"]) <= set(VerifyLiteral.__args__)


def test_template_store_derives_filename_from_template_name_and_renames(tmp_path) -> None:
    default_template = (
        Path(__file__).parents[1]
        / "src"
        / "matcreator"
        / "question_templates"
        / "mab_qa.json"
    )
    store = QuestionTemplateStore(tmp_path / "templates", default_template)
    template = json.loads(default_template.read_text(encoding="utf-8"))
    template["name"] = "My Custom Template"

    saved = store.save_for_name(template)

    assert saved["template_id"] == "my-custom-template"
    assert (tmp_path / "templates" / "my-custom-template.json").is_file()

    template["name"] = "Renamed Template"
    renamed = store.save_for_name(template, previous_id=saved["template_id"])

    assert renamed["template_id"] == "renamed-template"
    assert not (tmp_path / "templates" / "my-custom-template.json").exists()
    assert (tmp_path / "templates" / "renamed-template.json").is_file()


def test_question_validation_accepts_benchmark_verifiers() -> None:
    assert validate_question(_question()) == []
    question = _question()
    question["scoring_checklist"][0]["verify"] = "llm_binary_judge"

    assert validate_question(question) == []


def test_question_validation_rejects_unknown_verifiers() -> None:
    question = _question()
    question["scoring_checklist"][0]["verify"] = "unsupported_verifier"

    assert any("unsupported_verifier" in error for error in validate_question(question))


def test_service_exports_staged_declared_data_files(tmp_path) -> None:
    template_path = tmp_path / "template.json"
    template_path.write_text('{"template_version": "test-v1"}', encoding="utf-8")
    service = StagedSessionQuestionService(
        tmp_path / "staging", RecordingPlugin(), template_path=template_path
    )
    draft = asyncio.run(
        service.create(
            {"session_id": "session-1", "graph": {"nodes": []}, "events": [{"type": "tool"}]}
        )
    )
    question = _question()
    question["data_files"] = [{"key": "parameters", "path": "inputs/parameters.json"}]
    updated = service.update(draft.draft_id, yaml.safe_dump(question, sort_keys=False))

    service.stage_data_file(updated.draft_id, "inputs/parameters.json", b'{"encut": 320}\n')
    service.approve(updated.draft_id)
    exported = service.export(updated.draft_id, tmp_path / "question-bank")

    target = tmp_path / "question-bank" / question["id"]
    assert exported.status == "exported"
    assert yaml.safe_load((target / "question.yaml").read_text(encoding="utf-8")) == question
    assert (target / "inputs" / "parameters.json").read_bytes() == b'{"encut": 320}\n'


@pytest.mark.parametrize("declared_path", ["../secret.txt", "/tmp/secret.txt", "inputs\\secret.txt", "."])
def test_service_rejects_unsafe_data_file_paths(tmp_path, declared_path) -> None:
    template_path = tmp_path / "template.json"
    template_path.write_text('{"template_version": "test-v1"}', encoding="utf-8")
    service = StagedSessionQuestionService(
        tmp_path / "staging", RecordingPlugin(), template_path=template_path
    )
    draft = asyncio.run(
        service.create(
            {"session_id": "session-1", "graph": {"nodes": []}, "events": [{"type": "tool"}]}
        )
    )
    question = _question()
    question["data_files"] = [{"key": "unsafe", "path": declared_path}]
    updated = service.update(draft.draft_id, yaml.safe_dump(question, sort_keys=False))

    with pytest.raises(ValueError, match="data-file path"):
        service.stage_data_file(updated.draft_id, declared_path, b"secret")


def test_service_does_not_export_when_declared_data_file_is_missing(tmp_path) -> None:
    template_path = tmp_path / "template.json"
    template_path.write_text('{"template_version": "test-v1"}', encoding="utf-8")
    service = StagedSessionQuestionService(
        tmp_path / "staging", RecordingPlugin(), template_path=template_path
    )
    draft = asyncio.run(
        service.create(
            {"session_id": "session-1", "graph": {"nodes": []}, "events": [{"type": "tool"}]}
        )
    )
    question = _question()
    question["data_files"] = [{"key": "parameters", "path": "inputs/parameters.json"}]
    updated = service.update(draft.draft_id, yaml.safe_dump(question, sort_keys=False))
    service.approve(updated.draft_id)

    with pytest.raises(ValueError, match="missing from the staged draft"):
        service.export(updated.draft_id, tmp_path / "question-bank")

    assert not (tmp_path / "question-bank" / question["id"]).exists()
    assert service.get(updated.draft_id).status == "approved"


def test_service_publishes_approved_draft_to_custom_bank(tmp_path) -> None:
    template_path = tmp_path / "template.json"
    template_path.write_text('{"template_version": "test-v1"}', encoding="utf-8")
    service = StagedSessionQuestionService(
        tmp_path / "staging", RecordingPlugin(), template_path=template_path
    )
    draft = asyncio.run(
        service.create(
            {"session_id": "session-1", "graph": {"nodes": []}, "events": [{"type": "tool"}]}
        )
    )
    question = _question()
    question["data_files"] = [{"key": "parameters", "path": "inputs/parameters.json"}]
    updated = service.update(draft.draft_id, yaml.safe_dump(question, sort_keys=False))
    service.stage_data_file(updated.draft_id, "inputs/parameters.json", b'{"encut": 320}\n')
    service.approve(updated.draft_id)

    client = FakeBenchmarkBankClient()
    published = asyncio.run(
        service.publish(updated.draft_id, client, "user-alice", display_name="alice's questions")
    )

    assert published.status == "published"
    assert published.published_bank_id == "user-alice"
    assert published.published_question_id == question["id"]
    assert client.ensured_banks == [("user-alice", "alice's questions")]
    assert client.published[0]["bank_id"] == "user-alice"
    assert client.published[0]["question"]["id"] == question["id"]
    assert client.published[0]["data_files"] == [("inputs/parameters.json", b'{"encut": 320}\n')]

    with pytest.raises(ValueError, match="review-ready"):
        service.approve(updated.draft_id)


def test_service_requires_approved_status_before_publish(tmp_path) -> None:
    template_path = tmp_path / "template.json"
    template_path.write_text('{"template_version": "test-v1"}', encoding="utf-8")
    service = StagedSessionQuestionService(
        tmp_path / "staging", RecordingPlugin(), template_path=template_path
    )
    draft = asyncio.run(
        service.create(
            {"session_id": "session-1", "graph": {"nodes": []}, "events": [{"type": "tool"}]}
        )
    )
    question = _question()
    updated = service.update(draft.draft_id, yaml.safe_dump(question, sort_keys=False))

    client = FakeBenchmarkBankClient()
    with pytest.raises(ValueError, match="approved"):
        asyncio.run(service.publish(updated.draft_id, client, "user-alice"))
    assert client.ensured_banks == []


def test_service_does_not_publish_when_declared_data_file_is_missing(tmp_path) -> None:
    template_path = tmp_path / "template.json"
    template_path.write_text('{"template_version": "test-v1"}', encoding="utf-8")
    service = StagedSessionQuestionService(
        tmp_path / "staging", RecordingPlugin(), template_path=template_path
    )
    draft = asyncio.run(
        service.create(
            {"session_id": "session-1", "graph": {"nodes": []}, "events": [{"type": "tool"}]}
        )
    )
    question = _question()
    question["data_files"] = [{"key": "parameters", "path": "inputs/parameters.json"}]
    updated = service.update(draft.draft_id, yaml.safe_dump(question, sort_keys=False))
    service.approve(updated.draft_id)

    client = FakeBenchmarkBankClient()
    with pytest.raises(ValueError, match="missing from the staged draft"):
        asyncio.run(service.publish(updated.draft_id, client, "user-alice"))
    assert client.published == []
    assert service.get(updated.draft_id).status == "approved"


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
        service.create(
            {"session_id": "session-1", "graph": {"nodes": []}, "events": [{"type": "tool"}]}
        )
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
    assert any(
        "unsupported_verifier" in error
        for error in refining_plugin.session["validation_errors"]
    )
    assert refining_plugin.session["user_instruction"] == "Fix the verifier."
    assert refined.status == "ready_for_review"
    assert refined.refinement_count == 1
    metadata = json.loads((refined.staging_path / "generation.json").read_text(encoding="utf-8"))
    assert any(
        "unsupported_verifier" in error
        for error in metadata["history"][0]["feedback"]
    )
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
