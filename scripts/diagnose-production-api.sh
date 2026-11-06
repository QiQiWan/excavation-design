#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$ROOT_DIR/apps/web"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "[PitGuard] Production API configuration diagnosis"
echo "Root: $ROOT_DIR"

echo
echo "Environment files:"
found=0
for file in "$WEB_DIR"/.env "$WEB_DIR"/.env.local "$WEB_DIR"/.env.production "$WEB_DIR"/.env.production.local; do
  if [ -f "$file" ]; then
    found=1
    printf '  %s: ' "$file"
    grep -E '^VITE_API_BASE_URL=' "$file" || echo '<not set>'
  fi
done
if [ "$found" = "0" ]; then
  echo "  <none>"
fi

echo
echo "Source references:"
grep -RInE '127\.0\.0\.1:8002|localhost:8002|0\.0\.0\.0:8002|VITE_API_BASE_URL' \
  "$WEB_DIR/src" "$WEB_DIR"/.env* 2>/dev/null || true

if [ -d "$WEB_DIR/dist" ]; then
  echo
echo "Built bundle verification:"
  node "$ROOT_DIR/scripts/verify-production-build.mjs" "$WEB_DIR/dist"
else
  echo
echo "Built bundle: not present"
fi

"$PYTHON_BIN" "$ROOT_DIR/scripts/verify-release-consistency.py"
