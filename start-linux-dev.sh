#!/usr/bin/env bash
# PitGuard V3.87.4 cross-platform development launcher.
# Compatible with macOS Bash 3.2 and modern Linux Bash.
set -euo pipefail

export PITGUARD_WORKER_DEFAULT_HARD_CAP_MB="${PITGUARD_WORKER_DEFAULT_HARD_CAP_MB:-6144}"
export PITGUARD_SUPPORT_CANDIDATE_TRIAL_LIMIT="${PITGUARD_SUPPORT_CANDIDATE_TRIAL_LIMIT:-9}"
export PITGUARD_SUPPORT_CANDIDATE_POOL_LIMIT="${PITGUARD_SUPPORT_CANDIDATE_POOL_LIMIT:-6}"
export PITGUARD_RUNTIME_DIAGNOSTICS="${PITGUARD_RUNTIME_DIAGNOSTICS:-1}"
export PITGUARD_RESOURCE_WATCH_INTERVAL_SECONDS="${PITGUARD_RESOURCE_WATCH_INTERVAL_SECONDS:-1}"
export PITGUARD_BOREHOLE_IMPORT_TASK_TIMEOUT_SECONDS="${PITGUARD_BOREHOLE_IMPORT_TASK_TIMEOUT_SECONDS:-600}"
export PITGUARD_BOREHOLE_IMPORT_MAX_ROWS="${PITGUARD_BOREHOLE_IMPORT_MAX_ROWS:-100000}"
export PITGUARD_BOREHOLE_IMPORT_MAX_COLUMNS="${PITGUARD_BOREHOLE_IMPORT_MAX_COLUMNS:-128}"
export PITGUARD_IMPORT_STAGING_TTL_SECONDS="${PITGUARD_IMPORT_STAGING_TTL_SECONDS:-86400}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="$ROOT_DIR/services/api"
WEB_DIR="$ROOT_DIR/apps/web"
RUNTIME_DIR="$ROOT_DIR/runtime"
DB_PATH="${PITGUARD_DB_PATH:-$RUNTIME_DIR/pitguard.sqlite3}"
PYTHON_BIN="${PYTHON_BIN:-python}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi
BACKEND_PORT="${PITGUARD_BACKEND_PORT:-8002}"
FRONTEND_PORT="${PITGUARD_FRONTEND_PORT:-5173}"
INSTALL_DEPS="${PITGUARD_INSTALL_DEPS:-1}"
NUMERIC_THREADS="${PITGUARD_NUMERIC_THREADS:-1}"
DEV_RELOAD="${PITGUARD_DEV_RELOAD:-0}"
PREFLIGHT_ONLY="${PITGUARD_PREFLIGHT_ONLY:-0}"
BACKEND_LOG="$RUNTIME_DIR/backend.log"
FRONTEND_LOG="$RUNTIME_DIR/frontend.log"
WORKER_LOG="$RUNTIME_DIR/worker.log"
WORKER_HEARTBEAT="$RUNTIME_DIR/worker-heartbeat.json"
BACKEND_PID_FILE="$RUNTIME_DIR/backend.pid"
FRONTEND_PID_FILE="$RUNTIME_DIR/frontend.pid"
WORKER_PID_FILE="$RUNTIME_DIR/worker.pid"
LAUNCHER_PID_FILE="$RUNTIME_DIR/dev-launcher.pid"
ENV_CHECKER="$ROOT_DIR/scripts/check-python-env.py"
INSTALL_HINT=""
BACKEND_PID=""
FRONTEND_PID=""
WORKER_PID=""
CLEANUP_ACTIVE=0

is_true() {
  case "${1:-}" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
    *) return 1 ;;
  esac
}

print_install_hint() {
  if [ -n "${INSTALL_HINT:-}" ]; then
    echo >&2
    echo "[PitGuard] Python dependency installation command:" >&2
    echo "  $INSTALL_HINT" >&2
  fi
}

process_alive() {
  kill -0 "$1" >/dev/null 2>&1
}

process_command() {
  ps -p "$1" -o command= 2>/dev/null || true
}

kill_tree() {
  pid="${1:-}"
  [ -n "$pid" ] || return 0
  process_alive "$pid" || return 0
  if command -v pgrep >/dev/null 2>&1; then
    children="$(pgrep -P "$pid" 2>/dev/null || true)"
    for child in $children; do
      kill_tree "$child"
    done
  fi
  kill "$pid" >/dev/null 2>&1 || true
  for _wait_i in 1 2 3 4 5; do
    process_alive "$pid" || return 0
    sleep 0.2
  done
  kill -9 "$pid" >/dev/null 2>&1 || true
}

command_matches_any() {
  command_text="$1"
  token_list="$2"
  old_ifs="$IFS"
  IFS=','
  for token in $token_list; do
    case "$command_text" in *"$token"*) IFS="$old_ifs"; return 0 ;; esac
  done
  IFS="$old_ifs"
  return 1
}

stop_managed_pid_file() {
  pid_file="$1"
  expected="$2"
  [ -f "$pid_file" ] || return 0
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  case "$pid" in
    ''|*[!0-9]*) rm -f "$pid_file"; return 0 ;;
  esac
  if process_alive "$pid"; then
    cmd="$(process_command "$pid")"
    if command_matches_any "$cmd" "$expected"; then
      echo "[PitGuard] Stopping stale managed process pid=$pid ($expected)"
      kill_tree "$pid"
    else
      echo "[PitGuard][WARN] PID file $pid_file points to an unrelated process; leaving it untouched." >&2
    fi
  fi
  rm -f "$pid_file"
}

cleanup() {
  code=$?
  if [ "$CLEANUP_ACTIVE" = "1" ]; then return "$code"; fi
  CLEANUP_ACTIVE=1
  trap - EXIT INT TERM
  [ -n "${FRONTEND_PID:-}" ] && kill_tree "$FRONTEND_PID"
  [ -n "${WORKER_PID:-}" ] && kill_tree "$WORKER_PID"
  [ -n "${BACKEND_PID:-}" ] && kill_tree "$BACKEND_PID"
  rm -f "$BACKEND_PID_FILE" "$FRONTEND_PID_FILE" "$WORKER_PID_FILE" "$LAUNCHER_PID_FILE"
  if [ "$code" -ne 0 ]; then print_install_hint; fi
  exit "$code"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

require_file() {
  [ -f "$1" ] || { echo "[PitGuard][ERROR] Required file not found: $1" >&2; exit 1; }
}
require_dir() {
  [ -d "$1" ] || { echo "[PitGuard][ERROR] Required directory not found: $1" >&2; exit 1; }
}

port_available() {
  "$PYTHON_BIN" - "$1" "$2" <<'PY' >/dev/null 2>&1
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind((host, port))
finally:
    s.close()
PY
}

show_port_owner() {
  port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
  fi
}

wait_for_http() {
  url="$1"
  process_pid="$2"
  attempts="$3"
  label="$4"
  i=1
  while [ "$i" -le "$attempts" ]; do
    if "$PYTHON_BIN" - "$url" <<'PY' >/dev/null 2>&1
import sys, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=1.5) as response:
    if response.status >= 500:
        raise RuntimeError(response.status)
PY
    then
      return 0
    fi
    if ! process_alive "$process_pid"; then
      echo "[PitGuard][ERROR] $label process exited during startup." >&2
      return 1
    fi
    sleep 1
    i=$((i + 1))
  done
  echo "[PitGuard][ERROR] $label did not become ready: $url" >&2
  return 1
}

mkdir -p "$RUNTIME_DIR"
require_dir "$API_DIR"
require_file "$API_DIR/app/main.py"
require_file "$API_DIR/pyproject.toml"
require_dir "$WEB_DIR"
require_file "$WEB_DIR/package.json"
require_file "$WEB_DIR/package-lock.json"
require_file "$ENV_CHECKER"
require_file "$ROOT_DIR/scripts/run-worker-supervisor.py"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[PitGuard][ERROR] Python was not found. Activate the intended Conda/system environment or set PYTHON_BIN=/path/to/python." >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "[PitGuard][ERROR] Node.js/npm was not found. Install Node.js 20+ or activate the environment that provides npm." >&2
  exit 1
fi

PYTHON_PATH="$($PYTHON_BIN -c 'import sys; print(sys.executable)')"
NODE_PATH="$(command -v node || true)"
NPM_PATH="$(command -v npm || true)"

cat <<INFO
[PitGuard] Cross-platform development startup
[PitGuard] OS: $(uname -s) $(uname -m)
[PitGuard] Shell executable: ${BASH:-bash}
[PitGuard] Project root: $ROOT_DIR
[PitGuard] Backend directory: $API_DIR
[PitGuard] Frontend directory: $WEB_DIR
[PitGuard] Python: $PYTHON_PATH ($($PYTHON_BIN -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'))
[PitGuard] Node: ${NODE_PATH:-not-found} ($(node --version 2>/dev/null || true))
[PitGuard] npm: ${NPM_PATH:-not-found} ($(npm --version 2>/dev/null || true))
[PitGuard] Backend port: $BACKEND_PORT
[PitGuard] Frontend port: $FRONTEND_PORT
[PitGuard] Startup policy: current Python environment only; no private .venv creation.
INFO

if ! "$PYTHON_BIN" "$ENV_CHECKER" --format text; then
  INSTALL_HINT="$($PYTHON_BIN "$ENV_CHECKER" --format install-command || true)"
  EDITABLE_HINT="$($PYTHON_BIN "$ENV_CHECKER" --format editable-command || true)"
  if ! is_true "$INSTALL_DEPS"; then
    echo "[PitGuard][ERROR] PITGUARD_INSTALL_DEPS=0; missing packages will not be installed automatically." >&2
    [ -n "$EDITABLE_HINT" ] && echo "[PitGuard] Locked-project alternative: $EDITABLE_HINT" >&2
    exit 1
  fi
  echo "[PitGuard] Installing missing backend dependencies into the current Python environment..."
  MISSING_REQUIREMENTS="$($PYTHON_BIN "$ENV_CHECKER" --format missing || true)"
  if [ -n "$MISSING_REQUIREMENTS" ]; then
    # Requirements in pyproject.toml contain no spaces. Deliberate word splitting
    # passes each newline-separated requirement as one pip argument and remains
    # compatible with macOS Bash 3.2.
    # shellcheck disable=SC2086
    if ! "$PYTHON_BIN" -m pip install $MISSING_REQUIREMENTS; then
      echo "[PitGuard][ERROR] Installing missing backend dependencies failed." >&2
      [ -n "$EDITABLE_HINT" ] && echo "[PitGuard] Alternative: $EDITABLE_HINT" >&2
      exit 1
    fi
  fi
  # Register the local project only when possible. Runtime imports also use
  # PYTHONPATH=$API_DIR, so a build-isolation/index outage does not block startup.
  "$PYTHON_BIN" -m pip install -e "$API_DIR" --no-deps --no-build-isolation >/dev/null 2>&1 || true
fi

if ! "$PYTHON_BIN" "$ENV_CHECKER" --format text; then
  INSTALL_HINT="$($PYTHON_BIN "$ENV_CHECKER" --format install-command || true)"
  echo "[PitGuard][ERROR] Backend dependency check still fails after installation." >&2
  exit 1
fi
INSTALL_HINT=""

MISSING_FRONTEND=""
for name in vite typescript react react-dom three zustand @vitejs/plugin-react; do
  if [ ! -d "$WEB_DIR/node_modules/$name" ]; then
    if [ -n "$MISSING_FRONTEND" ]; then MISSING_FRONTEND="$MISSING_FRONTEND $name"; else MISSING_FRONTEND="$name"; fi
  fi
done
if [ ! -d "$WEB_DIR/node_modules" ] || [ -n "$MISSING_FRONTEND" ]; then
  [ -n "$MISSING_FRONTEND" ] && echo "[PitGuard] Missing frontend modules: $MISSING_FRONTEND"
  if ! is_true "$INSTALL_DEPS"; then
    echo "[PitGuard][ERROR] Frontend dependencies are incomplete and PITGUARD_INSTALL_DEPS=0." >&2
    echo "  cd '$WEB_DIR' && npm ci" >&2
    exit 1
  fi
  echo "[PitGuard] Installing frontend dependencies with npm ci..."
  (cd "$WEB_DIR" && npm ci)
else
  echo "[PitGuard] Frontend node_modules look complete."
fi

if is_true "$PREFLIGHT_ONLY"; then
  echo "[PitGuard] Preflight passed. No services were started because PITGUARD_PREFLIGHT_ONLY=$PREFLIGHT_ONLY."
  exit 0
fi

# Remove only processes recorded by this same project, including the legacy PID
# file created by the earlier incomplete startup patch.
stop_managed_pid_file "$BACKEND_PID_FILE" "uvicorn"
stop_managed_pid_file "$WORKER_PID_FILE" "run-worker-supervisor.py"
stop_managed_pid_file "$FRONTEND_PID_FILE" "npm,vite,node"
stop_managed_pid_file "$RUNTIME_DIR/worker-supervisor.pid" "run-worker-supervisor.py"
stop_managed_pid_file "$ROOT_DIR/pitguard_backend.pid" "uvicorn"

if ! port_available 127.0.0.1 "$BACKEND_PORT"; then
  echo "[PitGuard][ERROR] Backend port $BACKEND_PORT is already occupied." >&2
  show_port_owner "$BACKEND_PORT" >&2
  echo "Run ./stop-dev.sh or set PITGUARD_BACKEND_PORT to another port." >&2
  exit 1
fi
if ! port_available 127.0.0.1 "$FRONTEND_PORT"; then
  echo "[PitGuard][ERROR] Frontend port $FRONTEND_PORT is already occupied." >&2
  show_port_owner "$FRONTEND_PORT" >&2
  echo "Run ./stop-dev.sh or set PITGUARD_FRONTEND_PORT to another port." >&2
  exit 1
fi

: > "$BACKEND_LOG"
: > "$FRONTEND_LOG"
: > "$WORKER_LOG"
rm -f "$WORKER_HEARTBEAT"
echo "$$" > "$LAUNCHER_PID_FILE"

export PITGUARD_DB_PATH="$DB_PATH"
export PITGUARD_NUMERIC_THREADS="$NUMERIC_THREADS"
export PITGUARD_PRODUCT_MODE="${PITGUARD_PRODUCT_MODE:-core}"
export PYTHONPATH="$API_DIR${PYTHONPATH:+:$PYTHONPATH}"
for variable in OPENBLAS_NUM_THREADS OMP_NUM_THREADS MKL_NUM_THREADS NUMEXPR_NUM_THREADS VECLIB_MAXIMUM_THREADS; do
  export "$variable=$NUMERIC_THREADS"
done

RELOAD_ENABLED=0
is_true "$DEV_RELOAD" && RELOAD_ENABLED=1

echo "[PitGuard] Starting API at http://127.0.0.1:$BACKEND_PORT"
if [ "$RELOAD_ENABLED" = "1" ]; then
  (
    cd "$API_DIR"
    exec env PITGUARD_TASK_EXECUTION_MODE=external PITGUARD_PROCESS_ROLE=api \
      "$PYTHON_BIN" -m uvicorn app.main:app --reload --host 127.0.0.1 --port "$BACKEND_PORT"
  ) >"$BACKEND_LOG" 2>&1 &
else
  (
    cd "$API_DIR"
    exec env PITGUARD_TASK_EXECUTION_MODE=external PITGUARD_PROCESS_ROLE=api \
      "$PYTHON_BIN" -m uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT"
  ) >"$BACKEND_LOG" 2>&1 &
fi
BACKEND_PID=$!
echo "$BACKEND_PID" > "$BACKEND_PID_FILE"
if ! wait_for_http "http://127.0.0.1:$BACKEND_PORT/health" "$BACKEND_PID" 45 "Backend"; then
  echo "[PitGuard] Last backend log lines:" >&2
  tail -n 100 "$BACKEND_LOG" >&2 || true
  INSTALL_HINT="$($PYTHON_BIN "$ENV_CHECKER" --format install-command || true)"
  exit 1
fi

echo "[PitGuard] Starting isolated calculation worker"
(
  cd "$ROOT_DIR"
  exec env PITGUARD_TASK_EXECUTION_MODE=worker PITGUARD_PROCESS_ROLE=worker \
    PITGUARD_WORKER_EXIT_AFTER_TASK=true PITGUARD_WORKER_HEARTBEAT_PATH="$WORKER_HEARTBEAT" \
    PYTHON_BIN="$PYTHON_PATH" "$PYTHON_BIN" "$ROOT_DIR/scripts/run-worker-supervisor.py"
) >"$WORKER_LOG" 2>&1 &
WORKER_PID=$!
echo "$WORKER_PID" > "$WORKER_PID_FILE"
WORKER_OK=0
_worker_i=1
while [ "$_worker_i" -le 60 ]; do
  if [ -f "$WORKER_HEARTBEAT" ]; then WORKER_OK=1; break; fi
  if ! process_alive "$WORKER_PID"; then
    echo "[PitGuard][ERROR] Calculation worker exited during startup." >&2
    tail -n 100 "$WORKER_LOG" >&2 || true
    exit 1
  fi
  sleep 0.5
  _worker_i=$((_worker_i + 1))
done
if [ "$WORKER_OK" != "1" ]; then
  echo "[PitGuard][ERROR] Calculation worker did not publish a heartbeat." >&2
  tail -n 100 "$WORKER_LOG" >&2 || true
  exit 1
fi

echo "[PitGuard] Starting frontend at http://127.0.0.1:$FRONTEND_PORT"
(
  cd "$WEB_DIR"
  exec env VITE_API_BASE_URL="http://127.0.0.1:$BACKEND_PORT" npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT"
) >"$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!
echo "$FRONTEND_PID" > "$FRONTEND_PID_FILE"
if ! wait_for_http "http://127.0.0.1:$FRONTEND_PORT/" "$FRONTEND_PID" 60 "Frontend"; then
  echo "[PitGuard] Last frontend log lines:" >&2
  tail -n 120 "$FRONTEND_LOG" >&2 || true
  exit 1
fi

cat <<INFO

PitGuard is running.
Backend API : http://127.0.0.1:$BACKEND_PORT/health
API docs    : http://127.0.0.1:$BACKEND_PORT/docs
Frontend UI : http://127.0.0.1:$FRONTEND_PORT
Database    : $DB_PATH
Python      : $PYTHON_PATH
Numeric thr.: $NUMERIC_THREADS
Runtime logs: $RUNTIME_DIR

Use ./stop-dev.sh in another terminal or press Ctrl+C here to stop all services.
INFO

# Bash 3.2 has no wait -n. Monitor all child processes and fail fast if one exits.
while :; do
  if ! process_alive "$BACKEND_PID"; then
    echo "[PitGuard][ERROR] Backend stopped unexpectedly." >&2
    tail -n 120 "$BACKEND_LOG" >&2 || true
    exit 1
  fi
  if ! process_alive "$WORKER_PID"; then
    echo "[PitGuard][ERROR] Calculation worker stopped unexpectedly." >&2
    tail -n 120 "$WORKER_LOG" >&2 || true
    exit 1
  fi
  if ! process_alive "$FRONTEND_PID"; then
    echo "[PitGuard][ERROR] Frontend stopped unexpectedly." >&2
    tail -n 120 "$FRONTEND_LOG" >&2 || true
    exit 1
  fi
  sleep 2
done
