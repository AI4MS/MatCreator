from __future__ import annotations

import asyncio
from pathlib import Path

from matcreator.control_plane.evaluation_runtime import LocalEvaluationRuntimeLauncher, RuntimeSpec


class _FakeProcess:
    returncode = 0

    def __init__(self) -> None:
        self.terminated = False

    async def communicate(self):
        return b'{"answer": "done", "num_turns": 2}', b""

    def terminate(self) -> None:
        self.terminated = True


def test_local_launcher_uses_isolated_workspace_and_runtime_home(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    async def fake_create_subprocess_exec(*command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setenv("MAT_BENCH_SERVER_URL", "https://bench.example")
    monkeypatch.setenv("MAT_BENCH_TOKEN", "must-not-reach-agent")
    workspace = tmp_path / "workspace"
    runtime_home = tmp_path / "runtime"
    prompt_path = tmp_path / "prompt.txt"
    prompt_path.write_text("Solve the question", encoding="utf-8")

    async def exercise() -> None:
        outcome = await LocalEvaluationRuntimeLauncher().run(
            RuntimeSpec(
                workspace=workspace,
                runtime_home=runtime_home,
                prompt_path=prompt_path,
                session_id="eval-session-1",
                max_turns=25,
                timeout_seconds=30,
                event_log_path=runtime_home / "events.jsonl",
                environment={"LLM_MODEL": "test-model"},
            )
        )
        assert outcome.result == {"answer": "done", "num_turns": 2}
        assert outcome.error is None

    asyncio.run(exercise())

    assert captured["command"] == (
        "matcreator", "run", "--workspace", str(workspace), "--prompt-file", str(prompt_path),
        "--session-id", "eval-session-1", "--max-turns", "25", "--output-format", "json",
        "--event-log", str((runtime_home / "events.jsonl").resolve()),
    )
    assert captured["kwargs"]["cwd"] == str(workspace)
    assert captured["kwargs"]["env"]["MATCLAW_WORKSPACE"] == str(workspace)
    assert captured["kwargs"]["env"]["MATCLAW_SESSION_DIR"] == str(workspace)
    assert captured["kwargs"]["env"]["MATCREATOR_HOME"] == str(runtime_home)
    assert captured["kwargs"]["env"]["LLM_MODEL"] == "test-model"
    assert "MAT_BENCH_SERVER_URL" not in captured["kwargs"]["env"]
    assert "MAT_BENCH_TOKEN" not in captured["kwargs"]["env"]
    assert (workspace / Path()).is_dir()
    assert runtime_home.is_dir()