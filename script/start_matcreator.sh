#!/usr/bin/env bash
set -euo pipefail

PROJ_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MATCREATOR_HOME="${MATCREATOR_HOME:-$HOME/.matcreator}"
MATCREATOR_HOME="${MATCREATOR_HOME/#\~/$HOME}"
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

cleanup() {
    echo ""
    echo "Shutting down services..."
    [[ -n "${PID_API:-}" ]] && kill "$PID_API" 2>/dev/null || true
    [[ -n "${PID_WEB:-}" ]] && kill "$PID_WEB" 2>/dev/null || true
    [[ -n "${PID_VITE:-}" ]] && kill "$PID_VITE" 2>/dev/null || true
    wait 2>/dev/null || true
    echo "All services stopped."
}
trap cleanup SIGINT SIGTERM

cd "$PROJ_ROOT"

echo "Starting ADK API server (port $MATCREATOR_ADK_PORT)..."
MATCREATOR_API_LOG_LEVEL="${MATCREATOR_API_LOG_LEVEL:-info}"
matcreator api-server --host "$MATCREATOR_ADK_HOST" --port "$MATCREATOR_ADK_PORT" --log-level "$MATCREATOR_API_LOG_LEVEL" >"$LOG_DIR/api-server.log" 2>&1 &
PID_API=$!
sleep 1

echo "Starting FastAPI middle layer (port $MATCREATOR_WEB_PORT)..."
MATCREATOR_WEB_LOG_LEVEL="${MATCREATOR_WEB_LOG_LEVEL:-info}"
python -m uvicorn web.main:app --host "$MATCREATOR_WEB_HOST" --port "$MATCREATOR_WEB_PORT" --log-level "$MATCREATOR_WEB_LOG_LEVEL" >"$LOG_DIR/web-main.log" 2>&1 &
PID_WEB=$!
sleep 1

echo "Starting Vite frontend (port $MATCREATOR_FRONTEND_PORT)..."
cd "$PROJ_ROOT/web/vite-frontend"
npm run dev >"$LOG_DIR/vite.log" 2>&1 &
PID_VITE=$!

echo ""
echo "All services running:"
echo "  ADK API server : http://$MATCREATOR_ADK_HOST:$MATCREATOR_ADK_PORT"
echo "  FastAPI layer  : http://$MATCREATOR_WEB_HOST:$MATCREATOR_WEB_PORT"
echo "  Frontend       : http://$MATCREATOR_FRONTEND_HOST:$MATCREATOR_FRONTEND_PORT"
echo ""
echo "Logs: $LOG_DIR/{api-server,web-main,vite}.log"
echo "Press Ctrl+C to stop all services."

wait