# Server Deployment

Server mode is intended for a shared group server. It runs a control plane behind nginx and starts one isolated Docker worker per registered user. Local single-user mode is separate and does not require Docker.

## Architecture

| Service | Purpose |
| --- | --- |
| `proxy` | nginx entrypoint on port 80. Routes UI, API, and SSE traffic to the control plane. |
| `control-plane` | FastAPI app, built frontend, auth, settings, session browsing, admin APIs, and worker lifecycle. |
| `matcreator-worker-<user_id>` | Per-user Python-only agent runtime with that user's mounted MatCreator home. |

Each worker sees:

```text
/root/.matcreator -> <host-data-root>/users/<user_id>/.matcreator
```

Workers are disposable. User data persists because it is mounted from the host.

## Prerequisites

1. Docker Engine and Docker Compose plugin.
2. Built control-plane and worker images.
3. Server defaults in a config file. By default, server mode uses `./config.yaml` from the repository root.

Server mode uses two images:

| Image | Default tag | Contents |
| --- | --- | --- |
| Control plane | `matcreator-control-plane:latest` | FastAPI application and compiled frontend bundle. |
| Worker | `matcreator-worker:latest` | MatCreator agent runtime; no Node.js or frontend bundle. |

The Compose `control-plane` service builds the Dockerfile's `control-plane`
target. The profile-gated `worker-image` service builds the `worker` target
for dynamically provisioned user workers; it is not started by normal `up`
commands.

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

To use a curated default skill bundle in server mode, set the module skill root
as a deployment-level value. The path must be valid inside the control-plane and
worker containers, for example a directory baked into the image:

```bash
export MATCREATOR_MODULE_SKILLS_ROOT=/app/selected-skills
docker compose -f docker-compose.server.yml up -d
```

or in the server-wide `config.yaml`:

```yaml
skills:
  module_root: /app/selected-skills
```

If the selected skills live on the host, mount that directory into the
control-plane container and workers at the same container path, then set
`MATCREATOR_MODULE_SKILLS_ROOT` to that container path. To bake them into the
image instead, copy the directory in the Dockerfile and set the same `ENV` there.

Environment variables are no longer read from `agents/MatCreator/.env` for
MatCreator application settings. Use process environment variables only for
deployment/runtime knobs such as ports, data roots, and worker limits.

## Benchmark Configuration

Configure the `mat-agent-bench` service in the server-wide `config.yaml`:

```yaml
benchmark:
  server_url: http://host.docker.internal:8080/bench
  token: your-benchmark-api-token
```

`benchmark.server_url` and `benchmark.token` are defaults for the control
plane and every user worker. Resolution is environment variable, then a user's
mounted config, then this server-wide config. `MAT_BENCH_SERVER_URL` and
`MAT_BENCH_TOKEN` are therefore optional deployment overrides, not required
when the YAML values are set.

The URL must be reachable from Docker containers. On Docker Desktop, use
`host.docker.internal` for a benchmark service running on the host. On a Linux
Docker Engine deployment, use a host address reachable from the worker network
or place the benchmark service on a shared Docker network. `0.0.0.0` is a
server bind address, not a client destination. Start the benchmark service with
an externally reachable bind, for example:

```bash
mat-bench serve --host 0.0.0.0 --port 8080
```

Existing workers retain the environment from their creation time. After
changing benchmark settings, recreate active workers so their injected
`MAT_BENCH_SERVER_URL` is updated:

```bash
docker ps -a --filter "name=matcreator-worker-" --format "{{.Names}}" | xargs -r docker rm -f
```

## Quick Start

From the repository root:

```bash
export MATCREATOR_HOST_DATA_ROOT="$(pwd)/server-data"
touch config.yaml
docker compose -f docker-compose.server.yml --profile build build control-plane worker-image
docker compose -f docker-compose.server.yml up -d
```

Open:

```text
http://localhost
```

Register a user and log in. The first login or register request starts a dedicated worker for that user.
The `worker-image` Compose service is build-only and does not start a shared
worker container during normal `up` operations.

Override either image tag when publishing images to a registry:

```bash
export MATCREATOR_CONTROL_PLANE_IMAGE=registry.example/matcreator-control-plane:v2
export MATCREATOR_WORKER_IMAGE=registry.example/matcreator-worker:v2
docker compose -f docker-compose.server.yml up -d
```

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
docker compose -f docker-compose.server.yml --profile build build control-plane worker-image
docker compose -f docker-compose.server.yml up -d --force-recreate control-plane proxy
```

Worker containers are recreated automatically when the control plane detects
that their image ID differs from the configured `MATCREATOR_WORKER_IMAGE`. To
force all workers to be recreated immediately:

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