#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR/services/api"
PYTHONPATH=. pytest -q

cd "$ROOT_DIR"
if command -v npm >/dev/null 2>&1 && [ -d "$ROOT_DIR/apps/web/node_modules" ]; then
  npm --prefix apps/web run build
  npm --prefix apps/web test
else
  echo "Frontend build/test skipped: run 'cd apps/web && npm install' first."
fi
