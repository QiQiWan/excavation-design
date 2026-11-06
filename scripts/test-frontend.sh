#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR/apps/web"
if [[ ! -d node_modules ]]; then
  npm ci
fi
npm test
npm run build
