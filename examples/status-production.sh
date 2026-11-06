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

printf '\nLargest project snapshots:\n'
DB_PATH="${PITGUARD_DB_PATH:-$ROOT_DIR/runtime/pitguard.sqlite3}"
if [ -f "$DB_PATH" ]; then
  python3 - "$DB_PATH" <<'PY' || true
import sqlite3, sys
path=sys.argv[1]
with sqlite3.connect(path, timeout=3.0) as conn:
    columns={row[1] for row in conn.execute('PRAGMA table_info(projects)')}
    if {'payload_bytes','workspace_bytes'} <= columns:
        has_external={'external_bytes','artifact_count'} <= columns
        query='SELECT id,name,payload_bytes,workspace_bytes,revision' + (',external_bytes,artifact_count' if has_external else '') + ' FROM projects ORDER BY payload_bytes DESC LIMIT 10'
        rows=conn.execute(query).fetchall()
        for row in rows:
            project_id,name,full_bytes,workspace_bytes,revision=row[:5]
            external_bytes=row[5] if has_external else 0
            artifact_count=row[6] if has_external else 0
            print(f'  {project_id} R{revision} {name}: core={int(full_bytes or 0)/1048576:.2f} MB workspace={int(workspace_bytes or 0)/1048576:.2f} MB external={int(external_bytes or 0)/1048576:.2f} MB objects={int(artifact_count or 0)}')
    else:
        print('  Workspace projections have not been prepared; run sudo bash start-linux.sh.')
PY
else
  printf '  Database not found: %s\n' "$DB_PATH"
fi
