#!/usr/bin/env bash
set -e
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_DIR="$ROOT_DIR/runtime"

process_alive() { kill -0 "$1" >/dev/null 2>&1; }
process_command() { ps -p "$1" -o command= 2>/dev/null || true; }
kill_tree() {
  pid="${1:-}"
  [ -n "$pid" ] || return 0
  process_alive "$pid" || return 0
  if command -v pgrep >/dev/null 2>&1; then
    children="$(pgrep -P "$pid" 2>/dev/null || true)"
    for child in $children; do kill_tree "$child"; done
  fi
  kill "$pid" >/dev/null 2>&1 || true
  sleep 0.5
  process_alive "$pid" && kill -9 "$pid" >/dev/null 2>&1 || true
}
command_matches_any() {
  command_text="$1"
  token_list="$2"
  old_ifs="$IFS"
  IFS=','
  for token in $token_list; do
    case "$command_text" in *"$token"*) IFS="$old_ifs"; return 0 ;; esac
  done
  IFS="$old_ifs"
  return 1
}
stop_file() {
  file="$1"
  expected="$2"
  [ -f "$file" ] || return 0
  pid="$(cat "$file" 2>/dev/null || true)"
  case "$pid" in ''|*[!0-9]*) rm -f "$file"; return 0 ;; esac
  if process_alive "$pid"; then
    cmd="$(process_command "$pid")"
    if command_matches_any "$cmd" "$expected"; then
      echo "[PitGuard] Stopping pid=$pid ($expected)"
      kill_tree "$pid"
    else
      echo "[PitGuard][WARN] $file points to an unrelated process; not stopping it." >&2
    fi
  fi
  rm -f "$file"
}
stop_file "$RUNTIME_DIR/frontend.pid" "npm,vite,node"
stop_file "$RUNTIME_DIR/worker.pid" "run-worker-supervisor.py"
stop_file "$RUNTIME_DIR/worker-supervisor.pid" "run-worker-supervisor.py"
stop_file "$RUNTIME_DIR/backend.pid" "uvicorn"
stop_file "$ROOT_DIR/pitguard_backend.pid" "uvicorn"
rm -f "$RUNTIME_DIR/dev-launcher.pid" "$RUNTIME_DIR/worker-heartbeat.json"
echo "[PitGuard] Managed development services stopped."
