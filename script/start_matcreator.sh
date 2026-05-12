#!/usr/bin/env bash
set -euo pipefail

PROJ_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MATCREATOR_HOME="${MATCREATOR_HOME:-$HOME/.matcreator}"
MATCREATOR_HOME="${MATCREATOR_HOME/#\~/$HOME}"
LOG_DIR="${MATCREATOR_LOG_DIR:-$MATCREATOR_HOME/logs}"
LOG_DIR="${LOG_DIR/#\~/$HOME}"
mkdir -p "$LOG_DIR"

# cleanup() {
#     echo ""
#     echo "Shutting down services..."
#     [[ -n "${PID_API:-}" ]] && kill "$PID_API" 2>/dev/null || true
#     [[ -n "${PID_WEB:-}" ]] && kill "$PID_WEB" 2>/dev/null || true
#     [[ -n "${PID_VITE:-}" ]] && kill "$PID_VITE" 2>/dev/null || true
#     wait 2>/dev/null || true
#     echo "All services stopped."
# }
# trap cleanup SIGINT SIGTERM

cd "$PROJ_ROOT"

# echo "Starting ADK API server (port 8000)..."
# MATCREATOR_API_LOG_LEVEL="${MATCREATOR_API_LOG_LEVEL:-info}"
# nohup matcreator api-server --host 0.0.0.0 --port 8000 >"$LOG_DIR/api-server.log" 2>&1 &
# PID_API=$!
# sleep 1

echo "Starting ADK API server (port 8000)..."
MATCREATOR_API_LOG_LEVEL="${MATCREATOR_API_LOG_LEVEL:-info}"
nohup matcreator api-server --host 0.0.0.0 --port 8000 --log-level "$MATCREATOR_API_LOG_LEVEL" >"$LOG_DIR/api-server.log" 2>&1 &
PID_API=$!
sleep 1

echo "Starting FastAPI middle layer (port 8001)..."
nohup python web/main.py >"$LOG_DIR/web-main.log" 2>&1 &
PID_WEB=$!
sleep 1

echo "Starting Vite frontend (port 5173)..."
cd "$PROJ_ROOT/web/vite-frontend"
nohup npm run dev -- --host 0.0.0.0 >"$LOG_DIR/vite.log" 2>&1 &
PID_VITE=$!

echo ""
echo "All services running:"
echo "  ADK API server : http://0.0.0.0:8000"
echo "  FastAPI layer  : http://0.0.0.0:8001"
echo "  Frontend       : http://0.0.0.0:5173"
echo ""
echo "Logs: $LOG_DIR/{api-server,web-main,vite}.log"
echo "Press Ctrl+C to stop all services."

# wait
