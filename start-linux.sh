#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_DIR="$ROOT_DIR/services/api"
WEB_DIR="$ROOT_DIR/apps/web"
RUNTIME_DIR="$ROOT_DIR/runtime"
DB_PATH="${PITGUARD_DB_PATH:-$RUNTIME_DIR/pitguard.sqlite3}"
PYTHON_BIN="${PYTHON_BIN:-python}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi
BACKEND_PORT="${PITGUARD_BACKEND_PORT:-8000}"
FRONTEND_PORT="${PITGUARD_FRONTEND_PORT:-5173}"
INSTALL_DEPS="${PITGUARD_INSTALL_DEPS:-1}"
BACKEND_LOG="$RUNTIME_DIR/backend.log"
FRONTEND_LOG="$RUNTIME_DIR/frontend.log"
CHECK_SCRIPT="$RUNTIME_DIR/check_backend_modules.py"

mkdir -p "$RUNTIME_DIR"
: > "$BACKEND_LOG"
: > "$FRONTEND_LOG"

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
echo "[PitGuard] Startup policy: use the current Python environment only; do not create or activate services/api/.venv."

LOCAL_VENV="$API_DIR/.venv"
case "${PYTHON_PATH,,}" in
  "${LOCAL_VENV,,}"*) echo "[PitGuard] Warning: current Python points to services/api/.venv. The script did not activate it; deactivate it if this is not intended." ;;
esac

cat > "$CHECK_SCRIPT" <<'PY'
modules = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn[standard]",
    "pydantic": "pydantic",
    "multipart": "python-multipart",
    "numpy": "numpy",
    "shapely": "shapely",
    "docx": "python-docx",
    "openpyxl": "openpyxl",
    "matplotlib": "matplotlib",
    "meshio": "meshio",
}
missing = []
for import_name, package_name in modules.items():
    try:
        __import__(import_name)
    except Exception:
        missing.append(package_name)
print(" ".join(missing))
PY

check_missing_modules() {
  "$PYTHON_BIN" "$CHECK_SCRIPT"
}

MISSING_MODULES="$(check_missing_modules)"
if [ -n "$MISSING_MODULES" ]; then
  echo "[PitGuard] Missing backend modules: $MISSING_MODULES"
  if [ "$INSTALL_DEPS" = "0" ]; then
    echo "[PitGuard] PITGUARD_INSTALL_DEPS=0, so dependencies will not be installed automatically." >&2
    echo "[PitGuard] Run manually: $PYTHON_BIN -m pip install fastapi 'uvicorn[standard]' pydantic python-multipart numpy shapely python-docx openpyxl matplotlib meshio" >&2
    exit 1
  fi
  echo "[PitGuard] Installing backend dependencies into the CURRENT Python environment. No virtual environment will be created."
  read -r -a MISSING_PACKAGES <<< "$MISSING_MODULES"
  "$PYTHON_BIN" -m pip install "${MISSING_PACKAGES[@]}" 2>&1 | tee -a "$BACKEND_LOG"
else
  echo "[PitGuard] Backend Python modules look available in the current environment."
fi

POST_MISSING_MODULES="$(check_missing_modules)"
if [ -n "$POST_MISSING_MODULES" ]; then
  echo "[PitGuard] Backend dependency check still failed: $POST_MISSING_MODULES" >&2
  echo "[PitGuard] Check pip permissions or activate the intended Python environment, then rerun this script." >&2
  exit 1
fi

if [ ! -d "$WEB_DIR/node_modules" ]; then
  echo "[PitGuard] Installing frontend dependencies with npm ci..."
  (cd "$WEB_DIR" && npm ci 2>&1 | tee -a "$FRONTEND_LOG")
else
  echo "[PitGuard] Frontend node_modules found."
fi

cleanup() {
  echo
  echo "[PitGuard] Stopping services..."
  if [ -n "${BACKEND_PID:-}" ]; then kill "$BACKEND_PID" >/dev/null 2>&1 || true; fi
  if [ -n "${FRONTEND_PID:-}" ]; then kill "$FRONTEND_PID" >/dev/null 2>&1 || true; fi
}
trap cleanup INT TERM EXIT

export PITGUARD_DB_PATH="$DB_PATH"
export PYTHONPATH="$API_DIR${PYTHONPATH:+:$PYTHONPATH}"

echo "[PitGuard] Starting API at http://127.0.0.1:$BACKEND_PORT"
(
  cd "$API_DIR"
  "$PYTHON_BIN" -m uvicorn app.main:app --reload --host 127.0.0.1 --port "$BACKEND_PORT" 2>&1 | tee -a "$BACKEND_LOG"
) &
BACKEND_PID=$!

echo "[PitGuard] Waiting for backend health check..."
HEALTH_OK=0
for _ in $(seq 1 25); do
  if "$PYTHON_BIN" - <<PY >/dev/null 2>&1
import urllib.request
urllib.request.urlopen('http://127.0.0.1:$BACKEND_PORT/health', timeout=1).read()
PY
  then
    HEALTH_OK=1
    break
  fi
  if ! kill -0 "$BACKEND_PID" >/dev/null 2>&1; then
    echo "[PitGuard] Backend process exited during startup. Last backend log lines:" >&2
    tail -80 "$BACKEND_LOG" >&2 || true
    exit 1
  fi
  sleep 1
done

if [ "$HEALTH_OK" != "1" ]; then
  echo "[PitGuard] Backend did not pass health check. Last backend log lines:" >&2
  tail -80 "$BACKEND_LOG" >&2 || true
  exit 1
fi

echo "[PitGuard] Starting web UI at http://127.0.0.1:$FRONTEND_PORT"
(
  cd "$WEB_DIR"
  VITE_API_BASE_URL="http://127.0.0.1:$BACKEND_PORT" npm run dev -- --host 127.0.0.1 --port "$FRONTEND_PORT" 2>&1 | tee -a "$FRONTEND_LOG"
) &
FRONTEND_PID=$!

cat <<EOF

PitGuard is running with the current shell environment.
Backend API : http://127.0.0.1:$BACKEND_PORT/health
Diagnostics : http://127.0.0.1:$BACKEND_PORT/api/system/diagnostics
Frontend UI : http://127.0.0.1:$FRONTEND_PORT
Database    : $DB_PATH
Backend log : $BACKEND_LOG
Frontend log: $FRONTEND_LOG

Press Ctrl+C in this terminal to stop both services.
EOF

wait "$BACKEND_PID" "$FRONTEND_PID"
