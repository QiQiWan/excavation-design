#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_MODE="${1:-fast}"
"$ROOT_DIR/scripts/test-backend.sh" "$BACKEND_MODE"
"$ROOT_DIR/scripts/test-frontend.sh"
