"""Campaign lifecycle coordination for benchmark evaluations."""
from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path
from typing import Any, Protocol

from .evaluation_runtime import EvaluationRuntime, LocalEvaluationRuntimeLauncher, RuntimeOutcome, RuntimeSpec
from .evaluations import EvaluationStore


class BenchmarkRunClient(Protocol):
    async def create_session(self, model_name: str) -> dict[str, Any]: ...

    async def create_run(self, session_id: str, selection: dict[str, Any]) -> dict[str, Any]: ...


class BenchmarkExecutionClient(BenchmarkRunClient, Protocol):
    async def get_task(self, run_id: str, question_id: str) -> dict[str, Any]: ...

    async def download_data_file(self, question_id: str, filename: str, destination: Path) -> Path: ...

    async def submit_attempt(
        self,
        *,
        run_id: str,
        question_id: str,
        idempotency_key: str,
        meta: dict[str, Any],
        artifacts: list[Path],
    ) -> dict[str, Any]: ...


def _safe_path_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")[:80]
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{cleaned or 'question'}-{digest}"


class EvaluationService:
    """Owns campaign creation and server-side question-set freezing."""

    def __init__(
        self,
        store: EvaluationStore,
        workspace_root: str | Path,
        *,
        launcher: EvaluationRuntime | None = None,
        runtime_environment: dict[str, str] | None = None,
    ) -> None:
        self.store = store
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.launcher = launcher or LocalEvaluationRuntimeLauncher()
        self.runtime_environment = runtime_environment or {}

    def create_campaign(
        self,
        *,
        owner_id: str,
        model_name: str,
        question_ids: list[str],
        max_parallelism: int = 1,
        max_turns: int = 50,
        timeout_seconds: int = 600,
        flash: bool = False,
    ) -> dict[str, Any]:
        selected = list(dict.fromkeys(question_id.strip() for question_id in question_ids if question_id.strip()))
        if not selected:
            raise ValueError("At least one question_id is required")
        if max_parallelism < 1:
            raise ValueError("max_parallelism must be positive")
        if max_turns < 1 or timeout_seconds < 1:
            raise ValueError("max_turns and timeout_seconds must be positive")
        return self.store.create_campaign(
            owner_id=owner_id,
            model_name=model_name,
            configuration={
                "question_ids": selected,
                "max_parallelism": max_parallelism,
                "max_turns": max_turns,
                "timeout_seconds": timeout_seconds,
                "flash": flash,
            },
        )

    async def start_campaign(self, campaign_id: str, client: BenchmarkRunClient) -> dict[str, Any]:
        campaign = self.store.get_campaign(campaign_id)
        if campaign is None:
            raise KeyError(f"Evaluation campaign '{campaign_id}' was not found")
        if campaign["status"] != "draft":
            raise ValueError("Only draft evaluation campaigns can be started")

        self.store.transition_campaign(campaign_id, "starting")
        try:
            remote_session = await client.create_session(campaign["model_name"])
            remote_session_id = str(remote_session["session_id"])
            remote_run = await client.create_run(
                remote_session_id,
                {"question_ids": campaign["configuration"]["question_ids"]},
            )
            remote_run_id = str(remote_run["run_id"])
            question_ids = [str(question_id) for question_id in remote_run.get("question_ids", [])]
            if not question_ids:
                raise ValueError("Benchmark server returned a run without questions")

            for index, question_id in enumerate(question_ids, start=1):
                runtime_session_id = f"eval_{campaign_id[:12]}_{index:04d}"
                workspace = (
                    self.workspace_root
                    / campaign_id
                    / _safe_path_component(remote_run_id)
                    / _safe_path_component(question_id)
                    / runtime_session_id
                )
                self.store.create_attempt(
                    campaign_id=campaign_id,
                    question_id=question_id,
                    runtime_session_id=runtime_session_id,
                    idempotency_key=uuid.uuid4().hex,
                    workspace_path=str(workspace),
                )
            return self.store.transition_campaign(
                campaign_id,
                "active",
                benchmark_session_id=remote_session_id,
                benchmark_run_id=remote_run_id,
            )
        except Exception as exc:
            self.store.transition_campaign(campaign_id, "failed", error=str(exc))
            raise

    @staticmethod
    def _safe_data_filename(value: str) -> str:
        candidate = Path(value).name
        if not candidate or candidate in {".", ".."}:
            raise ValueError("Benchmark task includes an invalid data filename")
        return candidate

    @staticmethod
    def _artifacts(workspace: Path) -> list[Path]:
        excluded_roots = {"inputs", ".runtime"}
        artifacts = []
        for path in sorted(workspace.rglob("*")):
            if not path.is_file() or path.name == "prompt.txt":
                continue
            relative = path.relative_to(workspace)
            if relative.parts[0] in excluded_roots:
                continue
            if path.stat().st_size > 25 * 1024 * 1024:
                raise ValueError(f"Artifact exceeds 25 MiB limit: {relative}")
            artifacts.append(path)
        if len(artifacts) > 100:
            raise ValueError("Attempt produced more than 100 artifacts")
        return artifacts

    async def execute_attempt(
        self,
        *,
        campaign_id: str,
        attempt_id: str,
        client: BenchmarkExecutionClient,
    ) -> dict[str, Any]:
        campaign = self.store.get_campaign(campaign_id)
        attempt = self.store.get_attempt(attempt_id)
        if campaign is None or attempt is None or attempt["campaign_id"] != campaign_id:
            raise KeyError("Evaluation attempt was not found")
        if campaign["status"] != "active" or attempt["status"] != "queued":
            raise ValueError("Only queued attempts in active campaigns can be executed")
        if not campaign["benchmark_run_id"]:
            raise ValueError("Evaluation campaign has no benchmark run")

        workspace = Path(attempt["workspace_path"] or "").resolve()
        if not attempt["workspace_path"] or not workspace.is_relative_to(self.workspace_root):
            raise ValueError("Attempt workspace is outside the evaluation workspace root")
        self.store.transition_attempt(attempt_id, "runtime_starting")
        try:
            task = await client.get_task(campaign["benchmark_run_id"], attempt["question_id"])
            task_prompt = str(task.get("prompt") or "")
            task_files = [
                str(descriptor.get("filename") or descriptor.get("key") or "")
                for descriptor in task.get("data_files", [])
                if isinstance(descriptor, dict)
                and str(descriptor.get("filename") or descriptor.get("key") or "")
            ]
            self.store.set_task_payload(
                attempt_id,
                {"prompt": task_prompt, "data_files": task_files},
            )
            workspace.mkdir(parents=True, exist_ok=True)
            input_dir = workspace / "inputs"
            downloaded = []
            for descriptor in task.get("data_files", []):
                if not isinstance(descriptor, dict):
                    continue
                raw_name = str(descriptor.get("filename") or descriptor.get("key") or "")
                if not raw_name:
                    continue
                filename = self._safe_data_filename(raw_name)
                destination = input_dir / filename
                await client.download_data_file(attempt["question_id"], raw_name, destination)
                downloaded.append(destination.relative_to(workspace).as_posix())

            prompt = task_prompt
            if not prompt:
                raise ValueError("Benchmark task has no prompt")
            if downloaded:
                prompt += "\n\nInput files are available in this workspace:\n" + "\n".join(
                    f"- {path}" for path in downloaded
                )
            prompt += "\n\nComplete the task and leave requested deliverables in the current workspace."
            prompt_path = workspace / "prompt.txt"
            prompt_path.write_text(prompt, encoding="utf-8")
            runtime_home = workspace.parent / ".runtime" / attempt["runtime_session_id"]
            event_log_path = runtime_home / "events.jsonl"
            self.store.transition_attempt(attempt_id, "running")
            outcome: RuntimeOutcome = await self.launcher.run(
                RuntimeSpec(
                    workspace=workspace,
                    runtime_home=runtime_home,
                    prompt_path=prompt_path,
                    session_id=attempt["runtime_session_id"],
                    max_turns=int(campaign["configuration"]["max_turns"]),
                    timeout_seconds=int(campaign["configuration"]["timeout_seconds"]),
                    event_log_path=event_log_path,
                    flash=bool(campaign["configuration"].get("flash")),
                    environment=self.runtime_environment,
                    on_managed_run_started=lambda run_id: self._link_managed_run(attempt_id, run_id),
                )
            )
            if outcome.error:
                current = self.store.get_attempt(attempt_id)
                if current and current["status"] == "cancelling":
                    return self.store.transition_attempt(
                        attempt_id,
                        "cancelled",
                        result={"stdout": outcome.stdout, "stderr": outcome.stderr},
                        error="Evaluation cancelled while the agent runtime was active.",
                    )
                terminal_status = "timed_out" if outcome.exit_code is None else "failed"
                return self.store.transition_attempt(
                    attempt_id,
                    terminal_status,
                    result={"stdout": outcome.stdout, "stderr": outcome.stderr},
                    error=outcome.error,
                )

            current_campaign = self.store.get_campaign(campaign_id)
            current_attempt = self.store.get_attempt(attempt_id)
            if current_campaign and current_campaign["status"] == "cancelling" or current_attempt and current_attempt["status"] == "cancelling":
                return self.store.transition_attempt(
                    attempt_id,
                    "cancelled",
                    error="Evaluation cancelled before benchmark submission.",
                )
            artifacts = self._artifacts(workspace)
            self.store.transition_attempt(attempt_id, "submitting")
            submission = await client.submit_attempt(
                run_id=campaign["benchmark_run_id"],
                question_id=attempt["question_id"],
                idempotency_key=attempt["idempotency_key"],
                meta={
                    "answer": str(outcome.result.get("answer") or ""),
                    "num_turns": int(outcome.result.get("num_turns") or 0),
                    "is_error": bool(outcome.result.get("is_error", False)),
                    "usage": {},
                    "tool_calls": [],
                },
                artifacts=artifacts,
            )
            return self.store.transition_attempt(
                attempt_id,
                "grading",
                benchmark_attempt_id=str(submission.get("attempt_id") or ""),
                grading_job_id=str(submission.get("job_id") or ""),
                result={"runtime": outcome.result, "artifacts": [path.name for path in artifacts]},
            )
        except Exception as exc:
            current = self.store.get_attempt(attempt_id)
            if current and current["status"] in {"runtime_starting", "running", "submitting"}:
                return self.store.transition_attempt(attempt_id, "failed", error=str(exc))
            raise

    async def _link_managed_run(self, attempt_id: str, managed_run_id: str) -> None:
        self.store.set_managed_run_id(attempt_id, managed_run_id)
