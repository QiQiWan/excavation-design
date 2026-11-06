#!/usr/bin/env bash
set +e
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
API_DIR="$ROOT_DIR/services/api"
WEB_DIR="$ROOT_DIR/apps/web"
RUNTIME_DIR="$ROOT_DIR/runtime"
PYTHON_BIN="${PYTHON_BIN:-python}"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || PYTHON_BIN=python3
BACKEND_PORT="${PITGUARD_BACKEND_PORT:-8002}"
FRONTEND_PORT="${PITGUARD_FRONTEND_PORT:-5173}"

echo "=== PitGuard startup diagnosis ==="
echo "Date: $(date)"
echo "OS: $(uname -a)"
echo "Shell: ${SHELL:-unknown}"
echo "Bash: ${BASH_VERSION:-not-running-under-bash}"
echo "Root: $ROOT_DIR"
echo "Backend directory: $API_DIR"
echo "Frontend directory: $WEB_DIR"
echo "Backend main exists: $([ -f "$API_DIR/app/main.py" ] && echo yes || echo no)"
echo "Frontend package exists: $([ -f "$WEB_DIR/package.json" ] && echo yes || echo no)"
echo "Python: $(command -v "$PYTHON_BIN" 2>/dev/null)"
"$PYTHON_BIN" --version 2>&1
echo "Node: $(command -v node 2>/dev/null)"
node --version 2>&1
echo "npm: $(command -v npm 2>/dev/null)"
npm --version 2>&1
if [ -f "$ROOT_DIR/scripts/check-python-env.py" ]; then
  "$PYTHON_BIN" "$ROOT_DIR/scripts/check-python-env.py" --format text
fi
for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  echo "--- listeners on port $port ---"
  if command -v lsof >/dev/null 2>&1; then lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null; else echo "lsof unavailable"; fi
done
for file in "$RUNTIME_DIR/backend.pid" "$RUNTIME_DIR/worker.pid" "$RUNTIME_DIR/frontend.pid" "$RUNTIME_DIR/worker-supervisor.pid" "$ROOT_DIR/pitguard_backend.pid"; do
  [ -f "$file" ] && echo "PID file $file: $(cat "$file")"
done
for log in "$RUNTIME_DIR/backend.log" "$RUNTIME_DIR/worker.log" "$RUNTIME_DIR/frontend.log"; do
  if [ -f "$log" ]; then
    echo "--- tail $log ---"
    tail -n 80 "$log"
  fi
done
