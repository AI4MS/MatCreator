#!/usr/bin/env bash
set -euo pipefail

PROJ_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Run the checkout being launched, not a potentially stale site-packages copy.
# This keeps the FastAPI layer and the ADK subprocess on the same MatCreator
# version during local development.
export PYTHONPATH="$PROJ_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
MATCREATOR_HOME="${MATCREATOR_HOME:-$HOME/.matcreator}"
MATCREATOR_HOME="${MATCREATOR_HOME/#\~/$HOME}"
MATCREATOR_HOME="$(python -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$MATCREATOR_HOME")"
export MATCREATOR_HOME
# New instance state, credentials and logs must not be world-readable.
umask 077
LOG_DIR="${MATCREATOR_LOG_DIR:-$MATCREATOR_HOME/logs}"
LOG_DIR="${LOG_DIR/#\~/$HOME}"
mkdir -p "$LOG_DIR"

# Configurable host/port variables

# Pre-flight: verify that matcreator is importable in the current Python
# environment before attempting resolution.  Without this check, a missing
# package produces a confusing "unbound variable" error from ``set -u``.
if ! python -c "from matcreator.ports import get_adk_port" 2>/dev/null; then
    echo "ERROR: Cannot import matcreator.ports. Is matcreator installed in the current Python environment?" >&2
    exit 1
fi

# Hosts and ports are resolved with precedence:
#   environment variable > ~/.matcreator/config.yaml > built-in default
# A single Python invocation resolves all values at once for efficiency.
# Variables are read via a safe KEY=VALUE loop (no eval) to avoid
# injection risk from config.yaml values containing shell metacharacters.
while IFS='=' read -r key value; do
    printf -v "$key" '%s' "$value"
done < <(python -c "
from matcreator.ports import (
    get_adk_host, get_adk_port,
    get_web_host, get_web_port,
    get_frontend_host, get_frontend_port,
)
for k, v in (
    ('MATCREATOR_ADK_HOST', str(get_adk_host())),
    ('MATCREATOR_ADK_PORT', str(get_adk_port())),
    ('MATCREATOR_WEB_HOST', str(get_web_host())),
    ('MATCREATOR_WEB_PORT', str(get_web_port())),
    ('MATCREATOR_FRONTEND_HOST', str(get_frontend_host())),
    ('MATCREATOR_FRONTEND_PORT', str(get_frontend_port())),
):
    print(f'{k}={v}')
")

# Export env vars for child processes
export MATCREATOR_ADK_HOST
export MATCREATOR_WEB_HOST
export MATCREATOR_FRONTEND_HOST
export MATCREATOR_ADK_PORT
export ADK_LOCAL_PORT="$MATCREATOR_ADK_PORT"
export MATCREATOR_WEB_PORT
export MATCREATOR_FRONTEND_PORT
export MATCREATOR_WEB_TARGET="http://${MATCREATOR_WEB_HOST}:${MATCREATOR_WEB_PORT}"
if [[ "$MATCREATOR_WEB_HOST" == "0.0.0.0" || "$MATCREATOR_WEB_HOST" == "::" ]]; then
    export MATCREATOR_WEB_TARGET="http://127.0.0.1:${MATCREATOR_WEB_PORT}"
fi
MATCREATOR_BROWSER_HOST="$(python -c 'import os; from pathlib import Path; from matcreator.local_access import ensure_local_access; print(ensure_local_access(Path(os.environ["MATCREATOR_HOME"])).browser_host)')"
MATCREATOR_LOCAL_PROFILE_WORKER="${MATCREATOR_LOCAL_PROFILE_WORKER:-0}"
export MATCREATOR_LOCAL_PROFILE_WORKER
if [[ "$MATCREATOR_LOCAL_PROFILE_WORKER" == "1" ]]; then
    export MATCREATOR_LOCAL_ACCOUNT_ROOT="${MATCREATOR_LOCAL_ACCOUNT_ROOT:-$MATCREATOR_HOME}"
    if [[ -z "${MATCREATOR_LOCAL_ACCOUNT_ID:-}" ]]; then
        MATCREATOR_LOCAL_ACCOUNT_ID="$(python -c 'import os,uuid; from pathlib import Path; from matcreator.local_access import ensure_local_access; print(uuid.UUID(hex=ensure_local_access(Path(os.environ["MATCREATOR_HOME"])).instance_id))')"
        export MATCREATOR_LOCAL_ACCOUNT_ID
    fi
else
    unset MATCREATOR_LOCAL_ACCOUNT_ROOT MATCREATOR_LOCAL_ACCOUNT_ID
    unset MATCREATOR_LOCAL_SRT_WORKER
fi

# Explicit isolation backends fail closed. SRT does not require a Docker
# installation, and missing isolation never falls back to shared OS privileges.
export MATCREATOR_LOCAL_RUNTIME="${MATCREATOR_LOCAL_RUNTIME:-process}"
if [[ "$MATCREATOR_LOCAL_PROFILE_WORKER" != "1" ]]; then
    python - <<'PY'
import os
from pathlib import Path
from matcreator.local_account_backends import create_local_runtime_manager
manager = create_local_runtime_manager(Path(os.environ["MATCREATOR_HOME"]), Path.cwd())
if hasattr(manager, "preflight"):
    manager.preflight()
PY
fi

# Refuse an occupied endpoint before spawning anything. In particular, do not
# leave a new frontend pointing at somebody else's pre-existing API server.
python - <<'PY'
import os, socket
names = ('ADK', 'WEB', 'FRONTEND') if os.environ.get('MATCREATOR_LOCAL_PROFILE_WORKER') == '1' else ('WEB', 'FRONTEND')
ports = [int(os.environ[f'MATCREATOR_{name}_PORT']) for name in names]
if len(set(ports)) != len(ports):
    raise SystemExit('ERROR: Active services need distinct ports.')
for name in names:
    host = os.environ[f'MATCREATOR_{name}_HOST']
    port = int(os.environ[f'MATCREATOR_{name}_PORT'])
    address = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0]
    with socket.socket(address[0], address[1], address[2]) as listener:
        try:
            listener.bind(address[4])
        except OSError as exc:
            raise SystemExit(f'ERROR: {name} endpoint {host}:{port} is unavailable: {exc}. No existing service was reused.')
PY

cleanup() {
    echo ""
    echo "Shutting down services..."
    [[ -n "${PID_API:-}" ]] && kill "$PID_API" 2>/dev/null || true
    [[ -n "${PID_WEB:-}" ]] && kill "$PID_WEB" 2>/dev/null || true
    [[ -n "${PID_VITE:-}" ]] && kill "$PID_VITE" 2>/dev/null || true
    wait 2>/dev/null || true
    echo "All services stopped."
}
trap cleanup EXIT
trap 'exit 130' SIGINT
trap 'exit 143' SIGTERM

cd "$PROJ_ROOT"

if [[ "$MATCREATOR_LOCAL_PROFILE_WORKER" == "1" ]]; then
    echo "Starting internal ADK API server (port $MATCREATOR_ADK_PORT)..."
    MATCREATOR_API_LOG_LEVEL="${MATCREATOR_API_LOG_LEVEL:-info}"
    python -m matcreator.scripts.start_agent api-server --host "$MATCREATOR_ADK_HOST" --port "$MATCREATOR_ADK_PORT" --log-level "$MATCREATOR_API_LOG_LEVEL" >"$LOG_DIR/api-server.log" 2>&1 &
    PID_API=$!
    sleep 1
fi

echo "Starting FastAPI middle layer (port $MATCREATOR_WEB_PORT)..."
MATCREATOR_WEB_LOG_LEVEL="${MATCREATOR_WEB_LOG_LEVEL:-info}"
python -m uvicorn web.main:app --host "$MATCREATOR_WEB_HOST" --port "$MATCREATOR_WEB_PORT" --log-level "$MATCREATOR_WEB_LOG_LEVEL" >"$LOG_DIR/web-main.log" 2>&1 &
PID_WEB=$!
sleep 1

echo "Starting Vite frontend (port $MATCREATOR_FRONTEND_PORT)..."
cd "$PROJ_ROOT/web/vite-frontend"
node node_modules/vite/bin/vite.js --host "$MATCREATOR_FRONTEND_HOST" --port "$MATCREATOR_FRONTEND_PORT" --strictPort >"$LOG_DIR/vite.log" 2>&1 &
PID_VITE=$!

echo ""
echo "All services running:"
if [[ -n "${PID_API:-}" ]]; then
    echo "  Internal ADK   : http://$MATCREATOR_ADK_HOST:$MATCREATOR_ADK_PORT"
else
    echo "  Local accounts : each account starts its own private backend"
fi
echo "  FastAPI layer  : http://$MATCREATOR_WEB_HOST:$MATCREATOR_WEB_PORT"
echo "  Frontend       : http://$MATCREATOR_BROWSER_HOST:$MATCREATOR_FRONTEND_PORT"
echo ""
echo "Logs: $LOG_DIR/{api-server,web-main,vite}.log"
echo "Data directory: $MATCREATOR_HOME"
echo 'Open the frontend to register or sign in to a local account.'
echo 'For a private setup link to claim existing installation data, run `matcreator access` in the same environment.'
echo "Press Ctrl+C to stop all services."

# If any component exits (including a late bind failure), terminate the exact
# children started here rather than silently retaining a partial stack.
WAIT_PIDS=("$PID_WEB" "$PID_VITE")
[[ -n "${PID_API:-}" ]] && WAIT_PIDS+=("$PID_API")
wait -n "${WAIT_PIDS[@]}"
