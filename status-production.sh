#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${PITGUARD_SERVICE_NAME:-pitguard-api}"
WORKER_SERVICE_NAME="${PITGUARD_WORKER_SERVICE_NAME:-pitguard-worker}"
BACKEND_PORT="${PITGUARD_BACKEND_PORT:-8002}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HEARTBEAT_PATH="${PITGUARD_WORKER_HEARTBEAT_PATH:-$ROOT_DIR/runtime/worker-heartbeat.json}"

printf 'PitGuard API service:\n'
systemctl --no-pager --full status "$SERVICE_NAME" || true
printf '\nCalculation worker:\n'
systemctl --no-pager --full status "$WORKER_SERVICE_NAME" || true

printf '\nAPI liveness:\n'
curl --max-time 3 -fsS "http://127.0.0.1:$BACKEND_PORT/health/live" || true
printf '\n\nAPI readiness:\n'
curl --max-time 3 -fsS "http://127.0.0.1:$BACKEND_PORT/health/ready" || true
printf '\n\nCompatibility health endpoint:\n'
curl --max-time 3 -fsS "http://127.0.0.1:$BACKEND_PORT/health" || true

printf '\n\nWorker heartbeat:\n'
if [ -s "$HEARTBEAT_PATH" ]; then
  cat "$HEARTBEAT_PATH"
else
  printf 'No heartbeat file at %s\n' "$HEARTBEAT_PATH"
fi

printf '\n\nCgroup resource snapshot:\n'
systemctl show "$SERVICE_NAME" "$WORKER_SERVICE_NAME" \
  -p ActiveState -p SubState -p MainPID -p MemoryCurrent -p MemoryPeak \
  -p MemoryHigh -p MemoryMax -p CPUUsageNSec --no-pager || true

printf '\nListening production ports:\n'
ss -lntp 2>/dev/null | grep -E ':80 |:443 |:'"$BACKEND_PORT"' ' || true
printf '\nPort 5173 is outside the production architecture and is not inspected.\n'
