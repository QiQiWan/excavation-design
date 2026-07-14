#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT_DIR/services/api"
WEB_DIR="$ROOT_DIR/apps/web"
RUNTIME_DIR="$ROOT_DIR/runtime"
PYTHON_BIN="${PYTHON_BIN:-}"
INSTALL_DEPS="${PITGUARD_INSTALL_DEPS:-1}"

if [ -z "$PYTHON_BIN" ]; then
  if [ -x /root/anaconda3/envs/ifc/bin/python ]; then
    PYTHON_BIN=/root/anaconda3/envs/ifc/bin/python
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  fi
fi

if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
  echo "[PitGuard] Python executable was not found. Set PYTHON_BIN=/path/to/python." >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "[PitGuard] npm was not found. Install Node.js 20.19+ or 22.12+." >&2
  exit 1
fi

mkdir -p "$RUNTIME_DIR" "$API_DIR/exports" "$API_DIR/runtime_cache"

echo "[PitGuard] Application root : $ROOT_DIR"
echo "[PitGuard] Python          : $PYTHON_BIN"
echo "[PitGuard] Installing backend dependencies into the current Python environment..."
if [ "$INSTALL_DEPS" = "1" ]; then
  "$PYTHON_BIN" -m pip install -e "$API_DIR"
else
  "$PYTHON_BIN" "$ROOT_DIR/scripts/check-python-env.py" --format text
fi

echo "[PitGuard] Installing locked frontend dependencies..."
(
  cd "$WEB_DIR"
  npm ci
)

echo "[PitGuard] Building production frontend with same-origin /api access..."
(
  cd "$WEB_DIR"
  VITE_API_BASE_URL="" npm run build
)

if grep -R "http://127.0.0.1:8002" "$WEB_DIR/dist" >/dev/null 2>&1; then
  echo "[PitGuard] Production build still contains the local API address." >&2
  exit 1
fi

"$PYTHON_BIN" -m compileall -q "$API_DIR/app"

echo
printf '%s\n' "[PitGuard] Production build completed." \
  "Frontend dist : $WEB_DIR/dist" \
  "Backend module: $API_DIR/app" \
  "No Vite service or port 5173 is used."
