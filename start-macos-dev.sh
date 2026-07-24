#!/usr/bin/env bash
set -e
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "$ROOT_DIR/start-linux-dev.sh" "$@"
