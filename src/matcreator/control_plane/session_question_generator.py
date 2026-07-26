"""Generate, validate, and stage session-derived benchmark question drafts."""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from hashlib import sha256
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, get_args

from mat_bench.schemas import QuestionItem, VerifyLiteral
import yaml


_TEMPLATE_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}")
DEFAULT_TEMPLATE_ID = "default"

# A draft is read-only once it has left MatCreator's review workspace, either by
# filesystem export to the shared bank root or by publishing to a custom bank.
LOCKED_DRAFT_STATUSES = frozenset({"exported", "published"})


class SessionQuestionGeneratorPlugin(Protocol):
    """File-oriented boundary for question-authoring providers."""

    name: str

    async def generate(
        self, *, template_path: Path, session_path: Path, output_path: Path
    ) -> None: ...


class BenchmarkBankClient(Protocol):
    """Boundary for publishing an approved question to a token-owned custom bank."""

    async def ensure_bank(self, bank_id: str, *, display_name: str | None = None) -> dict[str, Any]: ...

    async def publish_question(
        self,
        bank_id: str,
        *,
        question: dict[str, Any],
        data_files: list[tuple[str, Path]] | None = None,
    ) -> dict[str, Any]: ...


class QuestionGenerationDiagnosticError(ValueError):
    """A user-safe generation failure with structured UI diagnostics."""

    def __init__(self, message: str, *, diagnostics: dict[str, Any]) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics


class NoQuestionExtracted(ValueError):
    """The selected session does not support one grounded benchmark question."""

    def __init__(self, reason: str) -> None:
        self.reason = reason.strip() or "No grounded benchmark question could be extracted."
        super().__init__(self.reason)


class BuiltinLlmQuestionGeneratorPlugin:
    """Built-in authoring plugin backed by the configured MatCreator LLM."""

    name = "builtin_llm"

    def __init__(self, *, model: str, api_key: str | None = None, base_url: str | None = None) -> None:
        if not model:
            raise ValueError("The builtin_llm question generator requires an LLM model")
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> BuiltinLlmQuestionGeneratorPlugin:
        llm = config.get("llm") if isinstance(config.get("llm"), dict) else {}
        return cls(
            model=os.environ.get("LLM_MODEL") or str(llm.get("model") or ""),
            api_key=os.environ.get("LLM_API_KEY") or str(llm.get("api_key") or "") or None,
            base_url=os.environ.get("LLM_BASE_URL") or str(llm.get("base_url") or "") or None,
        )

    async def generate(
        self, *, template_path: Path, session_path: Path, output_path: Path
    ) -> None:
        from litellm import acompletion

        template = json.loads(template_path.read_text(encoding="utf-8"))
        item_schema = (
            template.get("extraction_schema", {})
            .get("questions", {})
            .get("item_schema", {})
        )
        allowed_question_keys = sorted(item_schema) if isinstance(item_schema, dict) else []
        invocation = json.loads(session_path.read_text(encoding="utf-8"))
        operation = invocation.get("operation", "generate")
        operation_instruction = (
            "Generate the initial question from the observed session evidence."
            if operation == "generate"
            else (
                "Refine the complete current_question using MatCreator's validation_errors and "
                "the optional user_instruction. Preserve grounded content that is already valid, "
                "fix the reported issues, and return a complete replacement question object."
            )
        )
        response = await acompletion(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Use the following maintained question-authoring template to derive exactly "
                        "one self-contained benchmark question from the observed session. Return only "
                        "one bare question object as JSON, not a questions wrapper. Its top-level keys "
                        "must be a subset of the keys defined by extraction_schema.questions.item_schema: "
                        + json.dumps(allowed_question_keys)
                        + ". Do not add keys that are absent from that schema. Do not invent unobserved "
                        "inputs, artifacts, or reference values. The template's executable_verify_types "
                        "field is the authoritative verifier allowlist and overrides verifier names in examples. "
                        + operation_instruction
                        + "\n\n"
                        + json.dumps(template, ensure_ascii=False, sort_keys=True)
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(invocation, ensure_ascii=False, sort_keys=True),
                },
            ],
            api_key=self.api_key,
            base_url=self.base_url,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        question = json.loads(content)
        if isinstance(question, dict) and isinstance(question.get("questions"), list):
            questions = question["questions"]
            if len(questions) != 1:
                raise ValueError("Question generator must produce exactly one question")
            question = questions[0]
        if not isinstance(question, dict):
            raise ValueError("Question generator did not return an object")
        temporary = output_path.with_suffix(".tmp")
        temporary.write_text(
            yaml.safe_dump(question, allow_unicode=False, sort_keys=False), encoding="utf-8"
        )
        temporary.replace(output_path)


class MkbProjectionQuestionGeneratorPlugin:
    """Generate one draft through MKB's template-driven projection agent stack.

    MKB's stock projection runner owns an MKB knowledge-frame database. Session
    evidence deliberately remains in MatCreator instead, so this adapter reuses
    MKB's public projection prompt and runner without pretending a session is an
    MKB frame or creating a second persistence system. By default it uses the
    same model and credentials as MatCreator's configured agent.
    """

    name = "mkb_projection"

    def __init__(
        self, *, model: str, api_key: str | None = None, base_url: str | None = None
    ) -> None:
        if not model:
            raise ValueError("The mkb_projection question generator requires an LLM model")
        self.model = model
        self.api_key = api_key
        self.base_url = base_url

    @classmethod
    def from_config(
        cls, config: dict[str, Any], generator_config: dict[str, Any] | None = None
    ) -> MkbProjectionQuestionGeneratorPlugin:
        """Use MKB-specific values only as explicit overrides of MatCreator LLM settings."""
        if generator_config is None:
            candidate = config.get("session_question_generator")
            generator_config = candidate if isinstance(candidate, dict) else config
        llm = config.get("llm") if isinstance(config.get("llm"), dict) else {}
        return cls(
            model=(
                os.environ.get("MKB_EXTRACTION_MODEL")
                or str(generator_config.get("model") or "")
                or os.environ.get("LLM_MODEL")
                or str(llm.get("model") or "")
            ),
            api_key=(
                os.environ.get("MKB_LLM_API_KEY")
                or str(generator_config.get("api_key") or "")
                or os.environ.get("LLM_API_KEY")
                or str(llm.get("api_key") or "")
                or None
            ),
            base_url=(
                os.environ.get("MKB_LLM_API_BASE")
                or str(generator_config.get("base_url") or "")
                or os.environ.get("LLM_BASE_URL")
                or str(llm.get("base_url") or "")
                or None
            ),
        )

    @staticmethod
    def _load_mkb_components() -> tuple[Any, Any, Any, Any]:
        """Import optional MKB components only when this plugin is selected."""
        try:
            prompt_builder = import_module("mkb.agents.prompts.projection").build_projection_prompt
            agent_runner = import_module("mkb.agents.runner").AgentRunner
            agent = import_module("google.adk.agents").Agent
            lite_llm = import_module("google.adk.models.lite_llm").LiteLlm
        except (ImportError, AttributeError) as exc:
            raise RuntimeError(
                "The mkb_projection question generator requires mat-know-base with its "
                "materials extra. Reinstall MatCreator's dependencies."
            ) from exc
        return prompt_builder, agent_runner, agent, lite_llm

    @staticmethod
    def _question_from_response(content: str) -> dict[str, Any]:
        """Decode a single question from an LLM response without trusting prose.

        MKB's standard projection prompt describes a ``questions`` JSON
        document, while this session adapter asks for its one item directly.
        Providers commonly add a Markdown fence or a one-line introduction
        despite the latter instruction. Accept those harmless wrappers, plus
        YAML (the target authoring format), but still require a mapping below.
        """
        text = (content or "").strip()
        candidates = [text]
        candidates.extend(
            match.strip()
            for match in re.findall(r"```(?:json|ya?ml)?\s*\n?(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
            if match.strip()
        )

        question: Any = None
        for candidate in candidates:
            try:
                question = json.loads(candidate)
                break
            except json.JSONDecodeError:
                pass

        if question is None:
            decoder = json.JSONDecoder()
            for match in re.finditer(r"[\[{]", text):
                try:
                    value, _end = decoder.raw_decode(text[match.start() :])
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    question = value
                    break

        if question is None:
            for candidate in candidates:
                try:
                    value = yaml.safe_load(candidate)
                except yaml.YAMLError:
                    continue
                if isinstance(value, dict):
                    question = value
                    break

        if question is None:
            preview_limit = 4_000
            preview = text[:preview_limit]
            if len(text) > preview_limit:
                preview += "\n… response truncated for display"
            raise QuestionGenerationDiagnosticError(
                "MKB projection agent did not return a JSON or YAML question object",
                diagnostics={
                    "generator": "mkb_projection",
                    "stage": "parse_response",
                    "response_length": len(text),
                    "response_preview": preview or "(The model returned no text.)",
                    "expected_format": "One JSON or YAML question object (or a questions list with exactly one item).",
                },
            )
        if isinstance(question, dict):
            if question.get("no_qa_extracted") is True:
                raise NoQuestionExtracted(str(question.get("reason") or ""))
            if isinstance(question.get("questions"), list):
                questions = question["questions"]
                if not questions:
                    raise NoQuestionExtracted(
                        str(question.get("reason") or "The session did not contain a grounded benchmark task.")
                    )
                if len(questions) != 1:
                    raise ValueError("MKB projection agent must produce exactly one question")
                question = questions[0]
        if not isinstance(question, dict):
            raise ValueError("MKB projection agent did not return an object")
        return question

    async def generate(
        self, *, template_path: Path, session_path: Path, output_path: Path
    ) -> None:
        template = json.loads(template_path.read_text(encoding="utf-8"))
        if not isinstance(template, dict):
            raise ValueError("Question authoring template must contain an object")
        invocation = json.loads(session_path.read_text(encoding="utf-8"))
        if not isinstance(invocation, dict):
            raise ValueError("Session question invocation must contain an object")

        prompt_builder, agent_runner_class, agent_class, lite_llm_class = self._load_mkb_components()
        extraction_schema = template.get("extraction_schema")
        if not isinstance(extraction_schema, dict):
            extraction_schema = {}
        allowed_verify_types = template.get("executable_verify_types")
        if not isinstance(allowed_verify_types, list):
            allowed_verify_types = []
        instruction = prompt_builder(
            domain=str(template.get("domain") or "computational materials science"),
            system_prompt=str(template.get("system_prompt") or ""),
            extraction_schema=extraction_schema,
            purpose=str(template.get("purpose") or "qa_benchmark"),
            source_type="session",
        )
        instruction += (
            "\n\n---\n\n# MatCreator session adapter\n"
            "You are receiving bounded MatCreator session evidence directly in the user message, "
            "not an MKB knowledge frame. Do not call or expect MKB tools, database records, "
            "or source identifiers. Derive exactly one self-contained benchmark question from "
            "the observed evidence. If no grounded question can be extracted, return only "
            "{\"no_qa_extracted\": true, \"reason\": \"brief explanation\"}; never invent "
            "unobserved inputs, artifacts, or reference values. Otherwise return only one bare "
            "JSON question object; never a `questions` wrapper or Markdown.\n\n"
            "MatCreator's executable verifier allowlist is authoritative and overrides any "
            "conflicting MKB/template prose. Use only: "
            + json.dumps(allowed_verify_types)
            + ".\n"
        )
        if invocation.get("operation") == "refine":
            instruction += (
                "For a refinement, return a complete replacement of current_question. Preserve "
                "grounded valid content, correct validation_errors, and follow user_instruction.\n"
            )

        agent = agent_class(
            name="matcreator_session_question_projection",
            model=lite_llm_class(
                model=self.model,
                api_key=self.api_key,
                base_url=self.base_url,
            ),
            instruction=instruction,
            tools=[],
        )
        runner = agent_runner_class(
            agent=agent,
            app_name="matcreator_session_question_generator",
            max_llm_calls=1,
        )
        session_id = f"session-question-{uuid.uuid4().hex}"
        await runner.create_session(session_id=session_id, user_id="matcreator")
        result = await runner.run(
            session_id=session_id,
            user_id="matcreator",
            message=json.dumps(invocation, ensure_ascii=False, sort_keys=True),
        )
        if not result.success:
            raise RuntimeError(f"MKB projection agent failed: {result.error or 'unknown error'}")
        question = self._question_from_response(result.final_text)
        temporary = output_path.with_suffix(".tmp")
        temporary.write_text(
            yaml.safe_dump(question, allow_unicode=False, sort_keys=False), encoding="utf-8"
        )
        temporary.replace(output_path)


@dataclass(frozen=True)
class SessionQuestionGeneratorDefinition:
    """Declarative registration for a selectable question-extraction agent."""

    generator_id: str
    label: str
    description: str
    factory: Callable[[dict[str, Any], dict[str, Any]], SessionQuestionGeneratorPlugin]

    def as_dict(self) -> dict[str, str]:
        return {
            "generator_id": self.generator_id,
            "label": self.label,
            "description": self.description,
        }


def _build_builtin_llm_generator(
    config: dict[str, Any], _generator_config: dict[str, Any]
) -> SessionQuestionGeneratorPlugin:
    return BuiltinLlmQuestionGeneratorPlugin.from_config(config)


def _build_mkb_projection_generator(
    config: dict[str, Any], generator_config: dict[str, Any]
) -> SessionQuestionGeneratorPlugin:
    return MkbProjectionQuestionGeneratorPlugin.from_config(config, generator_config)


# Register a new extraction agent here. The web UI discovers this registry via
# the control-plane API; no frontend switch statement is required.
SESSION_QUESTION_GENERATORS: dict[str, SessionQuestionGeneratorDefinition] = {
    "builtin_llm": SessionQuestionGeneratorDefinition(
        generator_id="builtin_llm",
        label="MatCreator LLM",
        description="Generate a question directly with MatCreator's configured LLM.",
        factory=_build_builtin_llm_generator,
    ),
    "mkb_projection": SessionQuestionGeneratorDefinition(
        generator_id="mkb_projection",
        label="MKB projection agent",
        description="Use MKB's template-driven QA benchmark projection agent.",
        factory=_build_mkb_projection_generator,
    ),
}


def list_session_question_generators() -> list[dict[str, str]]:
    """Return UI-safe metadata for every registered extraction agent."""
    return [definition.as_dict() for definition in SESSION_QUESTION_GENERATORS.values()]


def has_session_question_generator(generator_id: str) -> bool:
    return generator_id in SESSION_QUESTION_GENERATORS


def build_session_question_generator(
    generator_id: str, config: dict[str, Any]
) -> SessionQuestionGeneratorPlugin:
    """Instantiate a registered generator from application and plugin settings."""
    definition = SESSION_QUESTION_GENERATORS.get(generator_id)
    if definition is None:
        raise ValueError(f"Unknown session question generator plugin: {generator_id}")
    generator_config = config.get("session_question_generator")
    if not isinstance(generator_config, dict):
        generator_config = {}
    return definition.factory(config, generator_config)


class CallableSessionQuestionGenerator:
    """Adapt an async question callable to the file-oriented plugin contract."""

    name = "callable"

    def __init__(self, generate: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]) -> None:
        self._generate = generate

    async def generate(
        self, *, template_path: Path, session_path: Path, output_path: Path
    ) -> None:
        invocation = json.loads(session_path.read_text(encoding="utf-8"))
        payload = invocation.get("evidence", invocation)
        question = await self._generate(payload)
        if not isinstance(question, dict):
            raise ValueError("Question generator did not return an object")
        output_path.write_text(
            yaml.safe_dump(question, allow_unicode=False, sort_keys=False), encoding="utf-8"
        )


@dataclass(frozen=True)
class GeneratedQuestionDraft:
    draft_id: str
    status: str
    question: dict[str, Any]
    evidence: dict[str, Any]
    validation_errors: list[str]
    staging_path: Path
    refinement_count: int = 0
    published_bank_id: str | None = None
    published_question_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "status": self.status,
            "question": self.question,
            "question_yaml": yaml.safe_dump(self.question, allow_unicode=False, sort_keys=False),
            "evidence": self.evidence,
            "validation_errors": self.validation_errors,
            "staging_path": str(self.staging_path),
            "refinement_count": self.refinement_count,
            "published_bank_id": self.published_bank_id,
            "published_question_id": self.published_question_id,
        }


def build_session_question_evidence(session_log: dict[str, Any]) -> dict[str, Any]:
    """Reduce a session log to bounded, observable evidence for question authoring."""
    nodes = session_log.get("graph", {}).get("nodes", [])
    trajectory_steps = [node for node in nodes if isinstance(node, dict)][:20]
    events = [event for event in session_log.get("events", []) if isinstance(event, dict)][:50]
    return {
        "schema_version": "matcreator.session-question-trajectory.v1",
        "source": {
            "session_id": str(session_log.get("session_id") or ""),
            "owner_id": session_log.get("owner_id"),
            "event_count": session_log.get("event_count", 0),
            "artifact_count": session_log.get("artifact_count", 0),
        },
        "steps": [
            {
                "step_number": node.get("step_number"),
                "action": node.get("action") or "Unnamed step",
                "summary": node.get("summary") or "",
                "status": node.get("status"),
                "tool_call_count": node.get("tool_call_count", 0),
                "artifact_count": node.get("artifact_count", 0),
            }
            for node in trajectory_steps
        ],
        "events": events,
        "artifacts": [str(path) for path in session_log.get("artifacts", [])][:20],
    }


def has_observable_session_question_evidence(evidence: dict[str, Any]) -> bool:
    """Whether a session has material from which a grounded task can be authored."""
    return any(
        isinstance(evidence.get(key), list) and evidence[key]
        for key in ("steps", "events", "artifacts")
    )


def validate_question(question: dict[str, Any]) -> list[str]:
    """Validate a generated question against the authoritative mat-bench schema."""
    try:
        QuestionItem.model_validate(question)
    except ValueError as exc:
        return [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            + (f" (received {error['input']!r})" if "input" in error else "")
            for error in exc.errors()
        ]
    return []


def _safe_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")[:80]
    return cleaned or "session-question"


def validate_question_template(template: dict[str, Any]) -> list[str]:
    """Validate the executable contract required of a question-authoring template."""
    errors: list[str] = []
    if not isinstance(template.get("name"), str) or not template["name"].strip():
        errors.append("Question authoring template needs a non-empty name")
    verify_types = template.get("executable_verify_types")
    if not isinstance(verify_types, list) or not verify_types:
        errors.append("Question authoring template needs executable_verify_types")
    else:
        supported_verify_types = frozenset(get_args(VerifyLiteral))
        invalid = sorted({str(value) for value in verify_types if value not in supported_verify_types})
        if invalid:
            errors.append(f"Unsupported executable verifiers: {', '.join(invalid)}")
    if not isinstance(template.get("system_prompt"), str) or not template["system_prompt"].strip():
        errors.append("Question authoring template needs a non-empty system_prompt")
    return errors


class QuestionTemplateStore:
    """Persist user-owned question-authoring templates beneath one trusted root."""

    def __init__(self, root: str | Path, default_template_path: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.default_template_path = Path(default_template_path).expanduser().resolve()

    @staticmethod
    def _validate_template_id(template_id: str) -> str:
        candidate = (template_id or "").strip()
        if not _TEMPLATE_ID_RE.fullmatch(candidate):
            raise KeyError("Question template was not found")
        return candidate

    @classmethod
    def template_id_for(cls, template: dict[str, Any]) -> str:
        name = template.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Question authoring template needs a non-empty name")
        return cls._validate_template_id(_safe_component(name.lower()))

    def _path(self, template_id: str) -> Path:
        template_id = self._validate_template_id(template_id)
        path = (self.root / f"{template_id}.json").resolve()
        if not path.is_relative_to(self.root):
            raise KeyError("Question template was not found")
        return path

    @staticmethod
    def _read_template(path: Path) -> dict[str, Any]:
        try:
            template = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Question authoring template is invalid") from exc
        if not isinstance(template, dict):
            raise ValueError("Question authoring template must contain an object")
        errors = validate_question_template(template)
        if errors:
            raise ValueError("; ".join(errors))
        return template

    @staticmethod
    def _summary(template_id: str, template: dict[str, Any], *, is_default: bool) -> dict[str, Any]:
        serialized = json.dumps(template, sort_keys=True, separators=(",", ":")).encode()
        return {
            "template_id": template_id,
            "name": str(template["name"]),
            "template_version": str(template.get("template_version") or ""),
            "is_default": is_default,
            "sha256": sha256(serialized).hexdigest(),
        }

    def list(self) -> list[dict[str, Any]]:
        default = self._read_template(self.default_template_path)
        templates = [self._summary(DEFAULT_TEMPLATE_ID, default, is_default=True)]
        if not self.root.is_dir():
            return templates
        for path in sorted(self.root.glob("*.json")):
            try:
                template = self._read_template(path)
                template_id = self._validate_template_id(path.stem)
            except (KeyError, ValueError):
                continue
            templates.append(self._summary(template_id, template, is_default=False))
        return templates

    def get(self, template_id: str) -> tuple[dict[str, Any], dict[str, Any], Path]:
        template_id = self._validate_template_id(template_id)
        is_default = template_id == DEFAULT_TEMPLATE_ID
        path = self.default_template_path if is_default else self._path(template_id)
        if not path.is_file():
            raise KeyError("Question template was not found")
        template = self._read_template(path)
        return template, self._summary(template_id, template, is_default=is_default), path

    def save(self, template_id: str, template: dict[str, Any], *, overwrite: bool = False) -> dict[str, Any]:
        template_id = self._validate_template_id(template_id)
        if template_id == DEFAULT_TEMPLATE_ID:
            raise ValueError("The default question template cannot be modified")
        errors = validate_question_template(template)
        if errors:
            raise ValueError("; ".join(errors))
        path = self._path(template_id)
        if path.exists() and not overwrite:
            raise FileExistsError("A question template with this id already exists")
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(template, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)
        return self._summary(template_id, template, is_default=False)

    def save_for_name(self, template: dict[str, Any], *, previous_id: str = "") -> dict[str, Any]:
        """Save a template under the filename derived from its required name."""
        template_id = self.template_id_for(template)
        previous_id = self._validate_template_id(previous_id) if previous_id else ""
        if previous_id == DEFAULT_TEMPLATE_ID:
            raise ValueError("The default question template cannot be modified")
        if previous_id and previous_id != template_id:
            previous_path = self._path(previous_id)
            if not previous_path.is_file():
                raise KeyError("Question template was not found")
            if self._path(template_id).exists():
                raise FileExistsError("A question template with this name already exists")
            metadata = self.save(template_id, template)
            previous_path.unlink()
            return metadata
        return self.save(template_id, template, overwrite=bool(previous_id))

    def delete(self, template_id: str) -> None:
        template_id = self._validate_template_id(template_id)
        if template_id == DEFAULT_TEMPLATE_ID:
            raise ValueError("The default question template cannot be deleted")
        path = self._path(template_id)
        if not path.is_file():
            raise KeyError("Question template was not found")
        path.unlink()


class StagedSessionQuestionService:
    """Create durable review-only question drafts outside the live benchmark bank."""

    def __init__(
        self,
        staging_root: str | Path,
        generator: SessionQuestionGeneratorPlugin | None = None,
        *,
        template_path: str | Path | None = None,
        template_metadata: dict[str, Any] | None = None,
        legacy_roots: list[str | Path] | None = None,
    ) -> None:
        self.staging_root = Path(staging_root).expanduser().resolve()
        self.generator = generator
        self.template_path = Path(template_path).expanduser().resolve() if template_path else None
        self.template_metadata = dict(template_metadata or {})
        self.legacy_roots = [Path(root).expanduser().resolve() for root in (legacy_roots or [])]

    def _draft_path(self, draft_id: str, *, migrate: bool = False) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", draft_id):
            raise KeyError("Question draft was not found")
        draft_root = None
        source_root = None
        for root in [self.staging_root, *self.legacy_roots]:
            candidate = (root / draft_id).resolve()
            if candidate.is_relative_to(root) and candidate.is_dir():
                draft_root = candidate
                source_root = root
                break
        if draft_root is None or source_root is None:
            raise KeyError("Question draft was not found")
        if migrate and source_root != self.staging_root:
            self.staging_root.mkdir(parents=True, exist_ok=True)
            target_root = (self.staging_root / draft_id).resolve()
            if target_root.exists():
                raise ValueError("Question draft migration target already exists")
            draft_root.replace(target_root)
            draft_root = target_root
        question_paths = [path for path in draft_root.iterdir() if (path / "question.yaml").is_file()]
        if len(question_paths) != 1:
            raise ValueError("Question draft storage is incomplete")
        return question_paths[0]

    @staticmethod
    def _write_json(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _write_yaml(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(yaml.safe_dump(value, allow_unicode=False, sort_keys=False), encoding="utf-8")
        temporary.replace(path)

    def _load(
        self, draft_id: str, *, migrate: bool = False
    ) -> tuple[Path, dict[str, Any], dict[str, Any]]:
        draft_path = self._draft_path(draft_id, migrate=migrate)
        try:
            question = yaml.safe_load((draft_path / "question.yaml").read_text(encoding="utf-8"))
            metadata = json.loads((draft_path / "generation.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise ValueError("Question draft storage is invalid") from exc
        if not isinstance(question, dict) or not isinstance(metadata, dict):
            raise ValueError("Question draft storage is invalid")
        return draft_path, question, metadata

    @staticmethod
    def _data_file_path(root: Path, declared_path: str) -> Path:
        if not isinstance(declared_path, str) or not declared_path:
            raise ValueError("Question data-file path must be a non-empty string")
        if "\\" in declared_path:
            raise ValueError("Question data-file path must use forward slashes")
        relative_path = Path(declared_path)
        if (
            declared_path == "."
            or relative_path.is_absolute()
            or any(part in {"", ".", ".."} for part in relative_path.parts)
        ):
            raise ValueError(f"Question data-file path '{declared_path}' is unsafe")
        resolved = (root / relative_path).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"Question data-file path '{declared_path}' escapes the question directory")
        return resolved

    @classmethod
    def _declared_data_file_paths(cls, question: dict[str, Any]) -> set[str]:
        data_files = question.get("data_files") or []
        if not isinstance(data_files, list):
            raise ValueError("Question data_files must be a list")
        declared_paths: set[str] = set()
        for data_file in data_files:
            if not isinstance(data_file, dict):
                raise ValueError("Question data_files entries must be objects")
            path = data_file.get("path")
            cls._data_file_path(Path("/").resolve(), path)
            if path in declared_paths:
                raise ValueError(f"Question data-file path '{path}' is declared more than once")
            declared_paths.add(path)
        return declared_paths

    @staticmethod
    def _draft_from_values(
        draft_path: Path, question: dict[str, Any], metadata: dict[str, Any]
    ) -> GeneratedQuestionDraft:
        return GeneratedQuestionDraft(
            draft_id=str(metadata["draft_id"]),
            status=str(metadata["status"]),
            question=question,
            evidence=dict(metadata.get("evidence", {"source": metadata.get("source", {})})),
            validation_errors=list(metadata.get("validation_errors", [])),
            staging_path=draft_path,
            refinement_count=int(metadata.get("refinement_count", 0)),
            published_bank_id=metadata.get("published_bank_id"),
            published_question_id=metadata.get("published_question_id"),
        )

    @staticmethod
    def _question_sha256(question: dict[str, Any]) -> str:
        return sha256(json.dumps(question, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def list(self) -> list[dict[str, Any]]:
        drafts: dict[str, dict[str, Any]] = {}
        for root in [*reversed(self.legacy_roots), self.staging_root]:
            if not root.is_dir():
                continue
            for draft_root in root.iterdir():
                if not re.fullmatch(r"[0-9a-f]{32}", draft_root.name):
                    continue
                try:
                    draft_path, question, metadata = self._load(draft_root.name)
                except (KeyError, ValueError):
                    continue
                drafts[draft_root.name] = {
                    "draft_id": draft_root.name,
                    "question_id": str(question.get("id") or ""),
                    "intent": str(question.get("intent") or ""),
                    "status": str(metadata.get("status") or "invalid"),
                    "source_session_id": str(metadata.get("source", {}).get("session_id") or ""),
                    "validation_errors": list(metadata.get("validation_errors", [])),
                    "refinement_count": int(metadata.get("refinement_count", 0)),
                    "updated_at": metadata.get("updated_at"),
                    "staging_path": str(draft_path),
                    "published_bank_id": metadata.get("published_bank_id"),
                }
        return sorted(drafts.values(), key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    async def create(self, session_log: dict[str, Any]) -> GeneratedQuestionDraft:
        if self.generator is None or self.template_path is None:
            raise RuntimeError("Question generator is not configured")
        if not self.template_path.is_file():
            raise ValueError("Question authoring template was not found")
        template_bytes = self.template_path.read_bytes()
        try:
            template = json.loads(template_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Question authoring template is invalid") from exc
        if not isinstance(template, dict):
            raise ValueError("Question authoring template must contain an object")
        evidence = build_session_question_evidence(session_log)
        if not has_observable_session_question_evidence(evidence):
            raise NoQuestionExtracted(
                "The selected session has no observable execution steps, events, or artifacts. "
                "Run work in that session or select a session with completed activity before "
                "generating a benchmark question."
            )
        draft_id = uuid.uuid4().hex
        invocation_path = (self.staging_root / f".{draft_id}.generating").resolve()
        if not invocation_path.is_relative_to(self.staging_root):
            raise ValueError("Question generation path escapes its configured root")
        invocation_path.mkdir(parents=True, exist_ok=False)
        session_path = invocation_path / "session.json"
        output_path = invocation_path / "question.yaml"
        self._write_json(
            session_path,
            {
                "schema_version": "matcreator.session-question-invocation.v1",
                "operation": "generate",
                "iteration": 0,
                "evidence": evidence,
            },
        )
        try:
            await self.generator.generate(
                template_path=self.template_path,
                session_path=session_path,
                output_path=output_path,
            )
            if not output_path.is_file():
                raise ValueError("Question generator did not produce question.yaml")
            question = yaml.safe_load(output_path.read_text(encoding="utf-8"))
        except Exception:
            shutil.rmtree(invocation_path, ignore_errors=True)
            raise
        if not isinstance(question, dict):
            shutil.rmtree(invocation_path, ignore_errors=True)
            raise ValueError("Generated question YAML must contain an object")
        errors = validate_question(question)
        question_id = _safe_component(str(question.get("id") or "session-question"))
        draft_path = (self.staging_root / draft_id / question_id).resolve()
        if not draft_path.is_relative_to(self.staging_root):
            raise ValueError("Draft staging path escapes its configured root")
        draft_path.mkdir(parents=True, exist_ok=False)
        output_path.replace(draft_path / "question.yaml")
        shutil.rmtree(invocation_path, ignore_errors=True)
        metadata = {
            "draft_id": draft_id,
            "status": "ready_for_review" if not errors else "invalid",
            "generator_plugin": self.generator.name,
            "template_path": str(self.template_path),
            "template_version": template.get("template_version"),
            "template_sha256": sha256(template_bytes).hexdigest(),
            "template": self.template_metadata,
            "session_schema_version": evidence["schema_version"],
            "source": evidence["source"],
            "evidence": evidence,
            "validation_errors": errors,
            "refinement_count": 0,
            "last_operation": "generate",
            "history": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write_json(draft_path / "generation.json", metadata)
        return GeneratedQuestionDraft(
            draft_id=draft_id,
            status=metadata["status"],
            question=question,
            evidence=evidence,
            validation_errors=errors,
            staging_path=draft_path,
            refinement_count=0,
        )

    def get(self, draft_id: str) -> GeneratedQuestionDraft:
        draft_path, question, metadata = self._load(draft_id)
        return self._draft_from_values(draft_path, question, metadata)

    def stage_data_file(
        self, draft_id: str, declared_path: str, content: bytes
    ) -> GeneratedQuestionDraft:
        draft_path, question, metadata = self._load(draft_id, migrate=True)
        if metadata.get("status") in LOCKED_DRAFT_STATUSES:
            raise ValueError("An exported or published question draft cannot be changed")
        if not isinstance(content, bytes):
            raise ValueError("Question data-file upload must contain bytes")
        declared_paths = self._declared_data_file_paths(question)
        if declared_path not in declared_paths:
            raise ValueError(f"Question data-file path '{declared_path}' is not declared in the draft")
        destination = self._data_file_path(draft_path, declared_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}-{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(content)
            temporary.replace(destination)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
        metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_json(draft_path / "generation.json", metadata)
        return self._draft_from_values(draft_path, question, metadata)

    def update(self, draft_id: str, question_yaml: str) -> GeneratedQuestionDraft:
        draft_path, _question, metadata = self._load(draft_id, migrate=True)
        if metadata.get("status") in LOCKED_DRAFT_STATUSES:
            raise ValueError("An exported or published question draft cannot be edited")
        try:
            question = yaml.safe_load(question_yaml)
        except yaml.YAMLError as exc:
            raise ValueError(f"Question YAML is invalid: {exc}") from exc
        if not isinstance(question, dict):
            raise ValueError("Question YAML must contain an object")
        errors = validate_question(question)
        metadata["status"] = "ready_for_review" if not errors else "invalid"
        metadata["validation_errors"] = errors
        metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_yaml(draft_path / "question.yaml", question)
        self._write_json(draft_path / "generation.json", metadata)
        return self._draft_from_values(draft_path, question, metadata)

    async def refine(
        self, draft_id: str, user_instruction: str | None = None
    ) -> GeneratedQuestionDraft:
        if self.generator is None or self.template_path is None:
            raise RuntimeError("Question generator is not configured")
        draft_path, question, metadata = self._load(draft_id, migrate=True)
        if metadata.get("status") in LOCKED_DRAFT_STATUSES:
            raise ValueError("An exported or published question draft cannot be refined")
        instruction = (user_instruction or "").strip()
        if len(instruction) > 2000:
            raise ValueError("Refinement instruction must be at most 2000 characters")
        previous_errors = validate_question(question)
        iteration = int(metadata.get("refinement_count", 0)) + 1
        invocation_path = (self.staging_root / f".{draft_id}.refining").resolve()
        invocation_path.mkdir(parents=True, exist_ok=False)
        session_path = invocation_path / "session.json"
        output_path = invocation_path / "question.yaml"
        self._write_json(
            session_path,
            {
                "schema_version": "matcreator.session-question-invocation.v1",
                "operation": "refine",
                "iteration": iteration,
                "evidence": metadata.get("evidence", {}),
                "current_question": question,
                "validation_errors": previous_errors,
                "user_instruction": instruction or None,
            },
        )
        try:
            await self.generator.generate(
                template_path=self.template_path,
                session_path=session_path,
                output_path=output_path,
            )
            if not output_path.is_file():
                raise ValueError("Question generator did not produce question.yaml")
            revised = yaml.safe_load(output_path.read_text(encoding="utf-8"))
            if not isinstance(revised, dict):
                raise ValueError("Generated question YAML must contain an object")
        except Exception:
            shutil.rmtree(invocation_path, ignore_errors=True)
            raise
        errors = validate_question(revised)
        previous_hash = self._question_sha256(question)
        revised_hash = self._question_sha256(revised)
        output_path.replace(draft_path / "question.yaml")
        shutil.rmtree(invocation_path, ignore_errors=True)
        now = datetime.now(timezone.utc).isoformat()
        history = list(metadata.get("history", []))[-49:]
        history.append(
            {
                "iteration": iteration,
                "timestamp": now,
                "previous_question_sha256": previous_hash,
                "question_sha256": revised_hash,
                "feedback": previous_errors,
                "validation_errors": errors,
                "status": "ready_for_review" if not errors else "invalid",
                "user_instruction": instruction or None,
            }
        )
        metadata.update(
            {
                "status": "ready_for_review" if not errors else "invalid",
                "validation_errors": errors,
                "refinement_count": iteration,
                "last_operation": "refine",
                "history": history,
                "updated_at": now,
            }
        )
        self._write_json(draft_path / "generation.json", metadata)
        return self._draft_from_values(draft_path, revised, metadata)

    def approve(self, draft_id: str) -> GeneratedQuestionDraft:
        draft_path, question, metadata = self._load(draft_id, migrate=True)
        errors = validate_question(question)
        if errors:
            metadata["status"] = "invalid"
            metadata["validation_errors"] = errors
            self._write_json(draft_path / "generation.json", metadata)
            raise ValueError("Question draft has validation errors and cannot be approved")
        if metadata.get("status") not in {"ready_for_review", "approved"}:
            raise ValueError("Only review-ready question drafts can be approved")
        metadata["status"] = "approved"
        metadata["validation_errors"] = []
        metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_json(draft_path / "generation.json", metadata)
        return self._draft_from_values(draft_path, question, metadata)

    async def publish(
        self,
        draft_id: str,
        client: BenchmarkBankClient,
        bank_id: str,
        *,
        display_name: str | None = None,
    ) -> GeneratedQuestionDraft:
        """Publish an approved draft to a token-owned custom question bank over HTTP."""
        draft_path, question, metadata = self._load(draft_id, migrate=True)
        if metadata.get("status") != "approved":
            raise ValueError("Question draft must be approved before it can be published")
        errors = validate_question(question)
        if errors:
            raise ValueError("Question draft has validation errors and cannot be published")
        declared_paths = self._declared_data_file_paths(question)
        data_files: list[tuple[str, Path]] = []
        for declared_path in declared_paths:
            source = self._data_file_path(draft_path, declared_path)
            if source.is_symlink() or not source.is_file():
                raise ValueError(
                    f"Question data file '{declared_path}' is missing from the staged draft"
                )
            data_files.append((declared_path, source))
        await client.ensure_bank(bank_id, display_name=display_name)
        result = await client.publish_question(bank_id, question=question, data_files=data_files)
        metadata["status"] = "published"
        metadata["published_bank_id"] = bank_id
        metadata["published_question_id"] = str(result.get("question_id") or question.get("id") or "")
        metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_json(draft_path / "generation.json", metadata)
        return self._draft_from_values(draft_path, question, metadata)

    def export(self, draft_id: str, question_bank_root: str | Path) -> GeneratedQuestionDraft:
        draft_path, question, metadata = self._load(draft_id, migrate=True)
        if metadata.get("status") != "approved":
            raise ValueError("Question draft must be approved before export")
        errors = validate_question(question)
        if errors:
            raise ValueError("Question draft has validation errors and cannot be exported")
        declared_paths = self._declared_data_file_paths(question)
        question_id = _safe_component(str(question.get("id") or ""))
        if question_id != question.get("id"):
            raise ValueError("Question id contains unsupported path characters")
        root = Path(question_bank_root).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        target = (root / question_id).resolve()
        if not target.is_relative_to(root):
            raise ValueError("Question export path escapes its configured root")
        if target.exists():
            raise ValueError(f"Question id '{question_id}' already exists in the benchmark bank")
        temporary = root / f".{question_id}-{uuid.uuid4().hex}.tmp"
        try:
            temporary.mkdir()
            self._write_yaml(temporary / "question.yaml", question)
            for declared_path in declared_paths:
                source = self._data_file_path(draft_path, declared_path)
                if source.is_symlink() or not source.is_file():
                    raise ValueError(
                        f"Question data file '{declared_path}' is missing from the staged draft"
                    )
                destination = self._data_file_path(temporary, declared_path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
            temporary.replace(target)
        except (OSError, ValueError):
            shutil.rmtree(temporary, ignore_errors=True)
            raise
        metadata["status"] = "exported"
        metadata["exported_path"] = str(target)
        metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_json(draft_path / "generation.json", metadata)
        return self._draft_from_values(draft_path, question, metadata)
