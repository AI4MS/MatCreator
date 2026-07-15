# Personal Application Deployment

Personal application mode runs MatCreator for one user on a local machine or single-user server. It does not require Docker, authentication, or a control plane.

## Install And Configure

Install MatCreator from the repository root:

```bash
pip install uv
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -e .
```

Configure LLM access with the CLI:

```bash
matcreator config set llm.model=openai/qwen3-plus
matcreator config set llm.api_key=your-api-key
matcreator config set llm.base_url=https://api.example.com/v1
```

## CLI Usage

Start an interactive session:

```bash
matcreator chat --workspace .
```

Run in full planning mode:

```bash
matcreator chat --workspace . --plan
```

Run a one-shot prompt:

```bash
matcreator run -p "Build a silicon FCC structure"
```

## Web UI

Start all local web services:

```bash
bash script/start_matcreator.sh
```

This starts:

- ADK API server on `http://localhost:8000`
- FastAPI middle layer on `http://localhost:8001`
- Vite frontend on `http://localhost:5173`

Logs are written to `~/.matcreator/logs/{api-server,web-main,vite}.log` by default. Set `MATCREATOR_LOG_DIR=/path/to/logs` to override this location.

## Data Locations

| Data | Default location |
| --- | --- |
| User configuration | `~/.matcreator/config.yaml` |
| ADK sessions and graph database | `~/.matcreator/.adk/` |
| Workspace artifacts | selected `--workspace` directory |
| Local logs | `~/.matcreator/logs/` |

## Port Configuration

MatCreator services use these default ports:

| Service | Default Port | Environment Variable |
|---------|-------------|---------------------|
| ADK API Server | 8000 | `MATCREATOR_ADK_PORT` |
| FastAPI Middle Layer | 8001 | `MATCREATOR_WEB_PORT` |
| Vite Frontend | 5173 | `MATCREATOR_FRONTEND_PORT` |

To change ports, set environment variables before starting:

```bash
export MATCREATOR_ADK_PORT=8100
export MATCREATOR_WEB_PORT=8101
export MATCREATOR_FRONTEND_PORT=5174
bash script/start_matcreator.sh
```

### Running alongside Hermes (or other agent systems)

If you need to avoid port conflicts with Hermes or other local services, configure alternative ports:

```bash
MATCREATOR_ADK_PORT=8100
MATCREATOR_WEB_PORT=8101
MATCREATOR_FRONTEND_PORT=5174
bash script/start_matcreator.sh
```

Ports can also be persisted in `~/.matcreator/config.yaml`:

```yaml
ports:
  adk: 8100
  web: 8101
  frontend: 5174
```

Or via CLI:

```bash
matcreator config set ports.adk=8100
matcreator config set ports.web=8101
```

Port configuration precedence: environment variables > ~/.matcreator/config.yaml > defaults.

## Skill Root Override

By default, MatCreator loads bundled skills from the installed module's `skills/`
directory. To run with a curated default skill directory without modifying those
centrally maintained bundled skills, set `MATCREATOR_MODULE_SKILLS_ROOT` before
starting MatCreator:

```bash
export MATCREATOR_MODULE_SKILLS_ROOT=/path/to/selected-skills
bash script/start_matcreator.sh
```

You can also persist the same override in `~/.matcreator/config.yaml`:

```yaml
skills:
  module_root: /path/to/selected-skills
```
