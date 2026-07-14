#!/usr/bin/env bash
set -euo pipefail
SERVICE_NAME="${PITGUARD_SERVICE_NAME:-pitguard-api}"
WORKER_SERVICE_NAME="${PITGUARD_WORKER_SERVICE_NAME:-pitguard-worker}"
BACKEND_PORT="${PITGUARD_BACKEND_PORT:-8002}"
systemctl --no-pager --full status "$SERVICE_NAME" || true
printf '\nCalculation worker:\n'
systemctl --no-pager --full status "$WORKER_SERVICE_NAME" || true
printf '\nBackend health:\n'
curl -fsS "http://127.0.0.1:$BACKEND_PORT/health" || true
printf '\n\nListening production ports:\n'
ss -lntp 2>/dev/null | grep -E ':80 |:443 |:'"$BACKEND_PORT"' ' || true
printf '\nPort 5173 is outside the production architecture and is not inspected.\n'
