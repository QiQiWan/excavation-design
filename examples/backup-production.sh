#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/root/anaconda3/envs/ifc/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then PYTHON_BIN="$(command -v python3)"; fi
DB_PATH="${PITGUARD_DB_PATH:-$ROOT_DIR/runtime/pitguard.sqlite3}"
DESTINATION="${PITGUARD_BACKUP_DIR:-$ROOT_DIR/runtime/backups}"
INCLUDE=()
if [ "${PITGUARD_BACKUP_ARTIFACT_FILES:-0}" = "1" ]; then INCLUDE=(--include-artifacts); fi
PYTHONPATH="$ROOT_DIR/services/api" "$PYTHON_BIN" "$ROOT_DIR/scripts/backup-project-storage.py" --database "$DB_PATH" --destination "$DESTINATION" "${INCLUDE[@]}"
