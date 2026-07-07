#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cat <<MSG
Start backend:
  cd "$ROOT_DIR/services/api" && uvicorn app.main:app --reload

Start frontend:
  cd "$ROOT_DIR/apps/web" && npm install && npm run dev
MSG
