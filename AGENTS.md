# AGENTS.md

This file provides guidance to Qoder (qoder.com) when working with code in this repository.

## Project Overview

MatCreator is an agentic AI platform for computational materials science, built as an agent harness around the Google ADK runtime, a markdown-based skill library, a workspace, and a persistent knowledge graph. It offers CLI, API, and Web interfaces.

## Commands

### Environment setup

```bash
pip install uv
uv venv .venv --python 3.12        # Python 3.12+ required (.python-version pins 3.12)
source .venv/bin/activate
uv pip install -e .                 # includes git deps (mat-bench, mat-know-base) — needs network
```

Frontend (only needed for Web UI development):

```bash
cd web/vite-frontend
npm ci                              # Node.js >= 24.14.1 required (.nvmrc pins 24.14.1)
```

### Tests

Backend (pytest; no pytest config file — run against `tests/` directly):

```bash
python -m pytest tests/ -v                        # full suite
python -m pytest tests/test_ports_config.py -v    # single file
python -m pytest tests/test_ports_config.py -k "name_pattern" -v   # single test
```

Note: CI (`.github/workflows/test.yml`) runs only a subset of test files plus the frontend check.

Frontend:

```bash
cd web/vite-frontend
npm run check          # runs node --test + vite build (this is what CI enforces)
npm test               # node --test only
npm run build          # vite build only
```

### Running

```bash
bash script/start_matcreator.sh    # Web UI: starts ADK API (:8000) + FastAPI (:8001) + Vite (:5173)
matcreator chat --workspace .      # CLI, Flash mode (direct interaction)
matcreator chat --workspace . --plan   # CLI, Plan mode (thinking agent -> execution graph -> execution agent)
```

The start script writes logs to `~/.matcreator/logs/{api-server,web-main,vite}.log` and resolves hosts/ports with precedence: environment variable > `~/.matcreator/config.yaml` > built-in default (logic in `src/matcreator/ports.py`).

### Configuration

Persistent settings live in `~/.matcreator/config.yaml` (relocatable via `MATCREATOR_HOME` or `MATCREATOR_CONFIG_PATH`):

```bash
matcreator config set llm.model=openai/qwen3-plus
matcreator config set llm.api_key=your-api-key
matcreator config set llm.base_url=https://api.example.com/v1
matcreator config show
```

Precedence rule: in local mode pre-existing environment variables win over `config.yaml`; in server mode (`MATCREATOR_MODE=server`) `config.yaml` wins. Full reference including multi-model `llm.executor_cards`: `docs/configuration.md`.

## Architecture

Request flow (see `docs/architecture_components.md` for details):

```
Vite frontend (web/vite-frontend)  ->  FastAPI middleware (web/main.py)  ->  ADK API server  ->  MatCreator agent (src/matcreator)
```

### Layers

- **Frontend** (`web/vite-frontend`): Vite app (Svelte + React mix, Ketcher, vis-network). Owns UI state, SSE consumption, and browser-reload reconnect to managed runs. Entry: `src/main.js`.
- **FastAPI middleware** (`web/main.py`): the application backend. Two roles: (1) ADK protocol proxy (`/run_sse`, `/apps/*`, `/list-apps`); (2) MatCreator-specific APIs (sessions, workspace files, structures, skills, settings, cancellation). In server mode it also acts as a Docker worker control plane (one worker container per user). Managed runs are process-local — execution does not survive a middleware restart.
- **Agent runtime** (`src/matcreator`): hosted on Google ADK. Entry `src/matcreator/agent.py`; CLI/API-server startup in `src/matcreator/scripts/start_agent.py`.

### Agent internals

- **Orchestrator** (`src/matcreator/agents/orchestrator/agent.py`): every turn starts with planning; if the planning agent approves an execution graph, control moves to the execution agent, which can return to planning within the same ADK invocation. Flash mode bypasses this loop.
- **Thinking/execution agents** (`src/matcreator/agents/thinking_agent/`, `src/matcreator/agents/execution_agent/`): graph planning vs. step executors running tools/skills/remote jobs.
- **Skills** (`src/matcreator/skills/`): modular markdown capabilities. Each skill is a directory with a `SKILL.md` carrying YAML frontmatter (`name`, `description`, `metadata.tools`, `metadata.dependent_skills`, `metadata.tags`). Skills declare dependencies on each other (e.g. `eos` depends on `ase`, `deepmd`, `bohrium`, ...).
- **Knowledge** (`src/matcreator/knowledge/`): self-improving Know-Do Graph (extraction, review, migration, query) that accumulates experience across executions. Extraction/review frequency is configurable (`MATCREATOR_MEMORIZATION_FREQUENCY`, `MATCREATOR_REVIEW_FREQUENCY`; `0` disables).
- **Control plane** (`src/matcreator/control_plane/`): durable remote-job store/service/monitor (E2B sandboxes), benchmark/evaluation runtime, worker supervisor.
- **Workspace** (`src/matcreator/workspace.py`): sessions and artifacts live under the `--workspace` directory; ADK session state under `~/.matcreator/.adk/session.db`.

### Source layout conventions

- Python packages live under `src/matcreator` (`[tool.setuptools.packages.find] where = ["src"]`) — the import root is `src/`, not the repo root. `script/start_matcreator.sh` sets `PYTHONPATH` to `src/` so the checkout, not site-packages, is executed.
- Skill files (`.md`, `.py`, `.yaml` under `skills/`) are shipped via `[tool.setuptools.package-data]` — new skill assets in those formats are packaged automatically.
- Port/host resolution for all services is centralized in `src/matcreator/ports.py`.

## Testing conventions

- `tests/conftest.py` maintains `ALL_CONFIG_ENV_VARS`, the canonical list of configuration environment variables. When adding a new config env var to `src/matcreator/ports.py`, add it there so config-cleanup tests clear it automatically.
- Frontend tests live in `web/vite-frontend/test/` and use the Node built-in test runner (`node --test`).

## Docker

Multi-stage build (`Dockerfile`): `worker` (API only, :8000), `control-plane` (API + built frontend, :8001), `local` (full dev image incl. Node, :8000/:8001/:5173). Port configuration via `.env` (see `.env.example`; copy to `.env` before `docker compose up`). Deployment docs: `docs/deployment/`.
