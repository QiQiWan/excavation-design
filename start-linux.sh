#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="$ROOT_DIR/services/api"
WEB_DIR="$ROOT_DIR/apps/web"
RUNTIME_DIR="$ROOT_DIR/runtime"
DB_PATH="${PITGUARD_DB_PATH:-$RUNTIME_DIR/pitguard.sqlite3}"
PYTHON_BIN="${PYTHON_BIN:-python}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then PYTHON_BIN="python3"; fi
BACKEND_PORT="${PITGUARD_BACKEND_PORT:-8002}"
FRONTEND_PORT="${PITGUARD_FRONTEND_PORT:-5173}"
INSTALL_DEPS="${PITGUARD_INSTALL_DEPS:-1}"
NUMERIC_THREADS="${PITGUARD_NUMERIC_THREADS:-1}"
BACKEND_LOG="$RUNTIME_DIR/backend.log"
FRONTEND_LOG="$RUNTIME_DIR/frontend.log"
ENV_CHECKER="$ROOT_DIR/scripts/check-python-env.py"
INSTALL_HINT=""
BACKEND_PID=""
FRONTEND_PID=""

mkdir -p "$RUNTIME_DIR"
: > "$BACKEND_LOG"
: > "$FRONTEND_LOG"

print_install_hint() {
  if [ -n "${INSTALL_HINT:-}" ]; then
    echo >&2
    echo "[PitGuard] Python dependency installation command:" >&2
    echo "  $INSTALL_HINT" >&2
  fi
}

cleanup() {
  local code=$?
  if [ -n "${BACKEND_PID:-}" ]; then kill "$BACKEND_PID" >/dev/null 2>&1 || true; fi
  if [ -n "${FRONTEND_PID:-}" ]; then kill "$FRONTEND_PID" >/dev/null 2>&1 || true; fi
  if [ "$code" -ne 0 ]; then print_install_hint; fi
  return "$code"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[PitGuard] Python was not found. Activate your Conda/system environment or set PYTHON_BIN=/path/to/python." >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "[PitGuard] Node.js/npm was not found. Install Node.js 20+ or activate the environment that provides npm." >&2
  exit 1
fi

PYTHON_PATH="$($PYTHON_BIN -c 'import sys; print(sys.executable)')"
echo "[PitGuard] Using current Python: $PYTHON_PATH"
echo "[PitGuard] Python version: $($PYTHON_BIN -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
echo "[PitGuard] Backend port: $BACKEND_PORT"
echo "[PitGuard] Startup policy: use the current Python environment only; do not create or activate services/api/.venv."

if ! "$PYTHON_BIN" "$ENV_CHECKER" --format text; then
  INSTALL_HINT="$($PYTHON_BIN "$ENV_CHECKER" --format install-command || true)"
  EDITABLE_HINT="$($PYTHON_BIN "$ENV_CHECKER" --format editable-command || true)"
  if [ "$INSTALL_DEPS" = "0" ]; then
    echo "[PitGuard] PITGUARD_INSTALL_DEPS=0; missing packages will not be installed automatically." >&2
    [ -n "$EDITABLE_HINT" ] && echo "[PitGuard] Locked-project alternative: $EDITABLE_HINT" >&2
    exit 1
  fi
  echo "[PitGuard] Installing locked backend dependencies into the CURRENT Python environment..."
  if ! "$PYTHON_BIN" -m pip install -e "$API_DIR" 2>&1 | tee -a "$BACKEND_LOG"; then
    echo "[PitGuard] pip install failed." >&2
    exit 1
  fi
fi

if ! "$PYTHON_BIN" "$ENV_CHECKER" --format text; then
  INSTALL_HINT="$($PYTHON_BIN "$ENV_CHECKER" --format install-command || true)"
  echo "[PitGuard] Backend dependency check still fails after installation." >&2
  exit 1
fi
INSTALL_HINT=""

check_frontend_deps() {
  local required=(vite typescript react react-dom three zustand @vitejs/plugin-react)
  local missing=()
  local name
  for name in "${required[@]}"; do [ -d "$WEB_DIR/node_modules/$name" ] || missing+=("$name"); done
  printf '%s\n' "${missing[@]}"
}
mapfile -t MISSING_FRONTEND < <(check_frontend_deps)
if [ ! -d "$WEB_DIR/node_modules" ] || [ "${#MISSING_FRONTEND[@]}" -gt 0 ]; then
  [ "${#MISSING_FRONTEND[@]}" -gt 0 ] && echo "[PitGuard] Missing frontend modules: ${MISSING_FRONTEND[*]}"
  echo "[PitGuard] Installing frontend dependencies with npm ci..."
  (cd "$WEB_DIR" && npm ci 2>&1 | tee -a "$FRONTEND_LOG")
else
  echo "[PitGuard] Frontend node_modules look complete."
fi

export PITGUARD_DB_PATH="$DB_PATH"
export PITGUARD_NUMERIC_THREADS="$NUMERIC_THREADS"
export PYTHONPATH="$API_DIR${PYTHONPATH:+:$PYTHONPATH}"

for variable in OPENBLAS_NUM_THREADS OMP_NUM_THREADS MKL_NUM_THREADS NUMEXPR_NUM_THREADS VECLIB_MAXIMUM_THREADS; do
  export "$variable=$NUMERIC_THREADS"
done

echo "[PitGuard] Starting API at http://127.0.0.1:$BACKEND_PORT"
(
  cd "$API_DIR"
  "$PYTHON_BIN" -m uvicorn app.main:app --reload --host 127.0.0.1 --port "$BACKEND_PORT" 2>&1 | tee -a "$BACKEND_LOG"
) &
BACKEND_PID=$!

HEALTH_OK=0
for _ in $(seq 1 30); do
  if "$PYTHON_BIN" - <<PY >/dev/null 2>&1
import urllib.request
urllib.request.urlopen('http://127.0.0.1:$BACKEND_PORT/health', timeout=1).read()
PY
  then HEALTH_OK=1; break; fi
  if ! kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
    echo "[PitGuard] Backend process exited during startup. Last log lines:" >&2
    tail -80 "$BACKEND_LOG" >&2 || true
    INSTALL_HINT="$($PYTHON_BIN "$ENV_CHECKER" --format install-command || true)"
    exit 1
  fi
  sleep 1
done
if [ "$HEALTH_OK" != "1" ]; then
  echo "[PitGuard] Backend did not pass health check." >&2
  tail -80 "$BACKEND_LOG" >&2 || true
  INSTALL_HINT="$($PYTHON_BIN "$ENV_CHECKER" --format install-command || true)"
  exit 1
fi

(
  cd "$WEB_DIR"
  VITE_API_BASE_URL="http://127.0.0.1:$BACKEND_PORT" npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT" 2>&1 | tee -a "$FRONTEND_LOG"
) &
FRONTEND_PID=$!

cat <<EOF

PitGuard is running.
Backend API : http://127.0.0.1:$BACKEND_PORT/health
API docs    : http://127.0.0.1:$BACKEND_PORT/docs
Frontend UI : http://127.0.0.1:$FRONTEND_PORT
Database    : $DB_PATH
Python      : $PYTHON_PATH
Numeric thr.: $NUMERIC_THREADS

Press Ctrl+C to stop both services.
EOF
wait "$BACKEND_PID" "$FRONTEND_PID"
