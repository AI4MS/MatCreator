# Server Deployment

Server mode is intended for a shared group server. It runs a control plane behind nginx and starts one isolated Docker worker per registered user. Local single-user mode is separate and does not require Docker.

## Architecture

| Service | Purpose |
| --- | --- |
| `proxy` | nginx entrypoint on port 80. Routes UI, API, and SSE traffic to the control plane. |
| `control-plane` | FastAPI app for auth, settings, session browsing, admin APIs, and worker lifecycle. |
| `matcreator-worker-<user_id>` | Per-user worker container with that user's mounted MatCreator home. |

Each worker sees:

```text
/root/.matcreator -> <host-data-root>/users/<user_id>/.matcreator
```

Workers are disposable. User data persists because it is mounted from the host.

## Prerequisites

1. Docker Engine and Docker Compose plugin.
2. A built MatCreator image.
3. Server defaults in a config file. By default, server mode uses `./config.yaml` from the repository root.

## Server Defaults

Server-wide MatCreator settings are read from a host config file mounted into
the control plane. By default, the host file is:

```text
./config.yaml
```

Override it by setting:

```bash
export MATCREATOR_HOST_CONFIG_PATH=/path/to/config.yaml
```

Inside the control-plane container, the selected file is mounted as:

```text
/app/config.yaml
```

and the container receives:

```text
MATCREATOR_CONFIG_PATH=/app/config.yaml
```

Create it before starting the service:

```bash
touch config.yaml
$EDITOR config.yaml
```

Example:

```yaml
llm:
  model: openai/qwen3-plus
  api_key: sk-your-server-default-key
  base_url: https://your-compatible-api/v1
  embedding_model: openai/text-embedding-v3
  graph_agent_model: openai/qwen3-plus
  review_agent_model: openai/qwen3-plus
  executor_cards:
    default: standard
    cards:
      standard:
        model: openai/qwen3-plus
        description: Default executor model for routine tool use.
        skills:
          - filesystem
          - python
        cost_tier: medium
        latency_tier: medium

env:
  MP_API_KEY: your-default-materials-project-key
```

These defaults are injected into newly created worker containers. Each user can
override them from the frontend settings UI or by editing that user's mounted
config file:

```text
server-data/users/<user_id>/.matcreator/config.yaml
```

Environment variables are no longer read from `agents/MatCreator/.env` for
MatCreator application settings. Use process environment variables only for
deployment/runtime knobs such as ports, data roots, and worker limits.

## Quick Start

From the repository root:

```bash
docker compose build

export MATCREATOR_HOST_DATA_ROOT="$(pwd)/server-data"
touch config.yaml
docker compose -f docker-compose.server.yml up -d
```

Open:

```text
http://localhost
```

Register a user and log in. The first login or register request starts a dedicated worker for that user.

## Data Layout

With `MATCREATOR_HOST_DATA_ROOT="$(pwd)/server-data"`:

```text
config.yaml
server-data/
  control-plane/
    .matcreator/
      users.db
  users/
    <user_id>/
      .matcreator/
        .adk/
          session.db
          agent_graphs/
          know_do_graph.db
        workspace/
        config.yaml
```

Use this tree for backups, admin inspection, and quota management.

## Resource Controls

Stop idle workers by setting:

```bash
export MATCREATOR_WORKER_IDLE_TIMEOUT_SECONDS=1800
```

Apply Docker limits before starting the control plane:

```bash
export MATCREATOR_WORKER_MEM_LIMIT=4g
export MATCREATOR_WORKER_CPUS=2
export MATCREATOR_WORKER_PIDS_LIMIT=512
docker compose -f docker-compose.server.yml up -d
```

## Admin Users

By default, the display name `admin` has admin privileges. To customize:

```bash
export MATCREATOR_ADMIN_USERS=admin,alice
docker compose -f docker-compose.server.yml up -d
```

## Useful Commands

List Compose services:

```bash
docker compose -f docker-compose.server.yml ps
```

List workers:

```bash
docker ps -a --filter "name=matcreator-worker-"
```

Read control-plane logs:

```bash
docker logs --tail=200 pfd-agent-control-plane-1
```

Read a worker's logs:

```bash
docker logs --tail=200 matcreator-worker-<user_id>
```

Enter a worker shell:

```bash
docker exec -it matcreator-worker-<user_id> bash
```

Rebuild and redeploy after code changes:

```bash
docker build -t matcreator:latest .
docker compose -f docker-compose.server.yml up -d --force-recreate control-plane proxy
```

Worker containers are recreated automatically when the control plane detects
that their image ID differs from the current `matcreator:latest`. To force all
workers to be recreated immediately:

```bash
docker ps -a --filter "name=matcreator-worker-" --format "{{.Names}}" | xargs -r docker rm -f
```

## Stop the Service

Stop and remove the Compose-managed services:

```bash
docker compose -f docker-compose.server.yml down
```

Dynamically created workers are not Compose services, so remove them separately:

```bash
docker ps -a --filter "name=matcreator-worker-" --format "{{.Names}}" | xargs -r docker rm -f
```

Persistent data remains under `server-data/`. Remove it only if you want to
delete all users, sessions, workspaces, and server defaults:

```bash
rm -rf server-data
```

## Security Notes

- Mounting `/var/run/docker.sock` gives the control plane high host privileges.
- Use HTTPS for real deployments. The included nginx config is plain HTTP for local or internal-server setup.
- Back up `MATCREATOR_HOST_DATA_ROOT`; worker containers should be considered replaceable.

## Shared Worker Mounts

Set `MATCREATOR_WORKER_SHARED_MOUNTS` to bind host directories into every worker
container. Entries use `host_path:container_path[:ro|rw]` and are separated by
commas.

Example: expose a repository-local `./share` directory inside workers as
read-only `/share`:

```bash
mkdir -p share
export MATCREATOR_WORKER_SHARED_MOUNTS="$(pwd)/share:/share:ro"
docker compose -f docker-compose.server.yml up -d
```

The host path must be visible to the Docker daemon.

## Configurable Ports

Server-mode deployment supports configurable host-facing ports via environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `MATCREATOR_SERVER_PROXY_HOST_PORT` | 80 | Nginx proxy host port |
| `MATCREATOR_SERVER_PROXY_PORT` | 80 | Nginx proxy container port |
| `MATCREATOR_WEB_HOST_PORT` | 8001 | Control-plane host port |
| `MATCREATOR_WEB_PORT` | 8001 | Control-plane container port |
| `MATCREATOR_ADK_PORT` | 8000 | ADK API (internal) |
| `MATCREATOR_WORKER_BASE_PORT` | 9001 | Worker container base port |

Example: run server mode on custom ports:

```bash
MATCREATOR_SERVER_PROXY_HOST_PORT=8080 \
MATCREATOR_WEB_HOST_PORT=8101 \
docker compose -f docker-compose.server.yml up
```

For personal Docker deployment:

```bash
MATCREATOR_ADK_HOST_PORT=8100 \
MATCREATOR_WEB_HOST_PORT=8101 \
MATCREATOR_FRONTEND_HOST_PORT=5174 \
docker compose up
```