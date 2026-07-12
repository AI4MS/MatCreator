# MatCreator server mode deployment

This guide describes the multi-user Docker deployment for a lightweight group
server. Local single-user mode is separate: it does not use Docker workers,
does not require auth, and stores settings/data in `~/.matcreator`.

## Architecture

Server mode starts two long-lived services:

| Service | Purpose |
| --- | --- |
| `proxy` | nginx entrypoint on port 80. Routes UI/API/SSE traffic to the control plane. |
| `control-plane` | FastAPI app for auth, settings, session browsing, admin APIs, and worker lifecycle. |

Each registered user gets a lazy-created worker container:

```text
matcreator-worker-<user_id>
  /root/.matcreator  ->  <host-data-root>/users/<user_id>/.matcreator
```

The control plane reads user session databases from:

```text
<host-data-root>/users/<user_id>/.matcreator/.adk/session.db
```

Workers are disposable. User data is persistent because it is host-mounted.

## Prerequisites

1. Docker Engine and Docker Compose plugin.
2. A built MatCreator image.
3. Server defaults in a config file. By default, server mode uses `./config.yaml` from the repository root.

Example control-plane `config.yaml` entries:

```yaml
llm:
  model: openai/your-model
  api_key: your-key
  base_url: https://your-compatible-api/v1
  embedding_model: openai/your-embedding-model
  executor_cards:
    default: standard
    cards:
      standard:
        model: openai/your-model
        description: Default executor model.

env:
  MP_API_KEY: your-default-materials-project-key
```

Create the config file before starting the service:

```bash
touch config.yaml
$EDITOR config.yaml
```

To use a different host-side config path, set:

```bash
export MATCREATOR_HOST_CONFIG_PATH=/path/to/config.yaml
```

The selected host file is mounted into the control-plane container as
`/app/config.yaml`, and the container receives `MATCREATOR_CONFIG_PATH=/app/config.yaml`.

Per-user overrides live at:

```text
server-data/users/<user_id>/.matcreator/config.yaml
```

Users can also edit those overrides from the frontend settings UI. MatCreator
application settings are not read from `agents/MatCreator/.env`; use process
environment variables only for deployment/runtime knobs.

## Quick start

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

Register a user and log in. The first login/register starts a dedicated worker.

## Data layout

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

## Resource controls

The control plane can stop workers and apply Docker resource limits when it
creates worker containers.

### Stop workers on logout

The frontend calls:

```text
POST /api/auth/logout
```

In server mode this stops the current user's worker container while preserving
their mounted data under `server-data/users/<user_id>/.matcreator`.

### Stop idle workers

`docker-compose.server.yml` enables idle shutdown by default:

```env
MATCREATOR_WORKER_IDLE_TIMEOUT_SECONDS=1800
```

Set it to `0` to disable idle shutdown:

```bash
MATCREATOR_WORKER_IDLE_TIMEOUT_SECONDS=0 \
docker compose -f docker-compose.server.yml up -d
```

## Stop the service

Stop and remove the Compose-managed services:

```bash
docker compose -f docker-compose.server.yml down
```

Remove dynamically created workers as well:

```bash
docker ps -a --filter "name=matcreator-worker-" --format "{{.Names}}" | xargs -r docker rm -f
```

Persistent data remains under `server-data/`. Remove it only if you want to
delete all users, sessions, workspaces, and server defaults:

```bash
rm -rf server-data
```

### CPU, memory, and PID limits

Set these before starting the control plane:

```bash
export MATCREATOR_WORKER_MEM_LIMIT=4g
export MATCREATOR_WORKER_CPUS=2
export MATCREATOR_WORKER_PIDS_LIMIT=512
docker compose -f docker-compose.server.yml up -d
```

Empty values use Docker defaults.

### Shared host directories in workers

To make a host directory available inside every worker, set
`MATCREATOR_WORKER_SHARED_MOUNTS` before starting the control plane. Entries use
the Docker bind-mount format:

```text
host_path:container_path[:ro|rw]
```

For example, to expose a repository-local `./share` directory as read-only
`/share` in each worker:

```bash
mkdir -p share
export MATCREATOR_WORKER_SHARED_MOUNTS="$(pwd)/share:/share:ro"
docker compose -f docker-compose.server.yml up -d
```

Use comma-separated entries for multiple mounts. The host path must be the path
as seen by the Docker daemon, not the control-plane container's internal path.

## Admin users

By default, the display name `admin` has admin privileges. To customize:

```bash
export MATCREATOR_ADMIN_USERS=admin,alice
docker compose -f docker-compose.server.yml up -d
```

Admin users can view aggregated sessions across per-user session databases.

## Useful commands

List services and workers:

```bash
docker ps --filter "name=matcreator"
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

Stop one worker manually:

```bash
docker stop matcreator-worker-<user_id>
```

Remove one worker container without deleting user data:

```bash
docker rm -f matcreator-worker-<user_id>
```

## Troubleshooting

### Frontend says session creation failed

Check the control plane and worker logs:

```bash
docker logs --tail=200 pfd-agent-control-plane-1
docker logs --tail=200 matcreator-worker-<user_id>
```

A healthy session-create request should reach the worker as:

```text
POST /apps/MatCreator/users/<user_id>/sessions/<session_id> 200 OK
```

### Worker exists but browser requests return 403

The control plane strips browser `Origin` headers before forwarding to workers.
If you still see `Forbidden: origin not allowed`, rebuild and recreate the stack
so the latest control-plane code is running:

```bash
docker compose build
docker compose -f docker-compose.server.yml up -d --force-recreate
```

### Worker is not created on login/register

Check Docker socket access from the control plane:

```bash
docker exec -it pfd-agent-control-plane-1 python - <<'PY'
import docker
print(docker.from_env().ping())
PY
```

The compose file mounts `/var/run/docker.sock` so the control plane can create,
start, stop, and remove workers.

## Security notes

- Mounting `/var/run/docker.sock` gives the control plane high host privileges.
  Only expose the control-plane UI to trusted users or put it behind additional
  network/auth controls.
- Use HTTPS for real deployments. The included nginx config is plain HTTP for
  local or internal-server setup.
- Back up `MATCREATOR_HOST_DATA_ROOT`; worker containers should be considered
  replaceable.
