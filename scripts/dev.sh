#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cat <<MSG
Start backend:
  cd "$ROOT_DIR/services/api" && PITGUARD_PRODUCT_MODE=core uvicorn app.main:app --reload --host 127.0.0.1 --port "${PITGUARD_BACKEND_PORT:-8002}"

Start frontend:
  cd "$ROOT_DIR/apps/web" && npm install && npm run dev
MSG
