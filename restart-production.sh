#!/usr/bin/env bash
set -euo pipefail
SERVICE_NAME="${PITGUARD_SERVICE_NAME:-pitguard-api}"
WORKER_SERVICE_NAME="${PITGUARD_WORKER_SERVICE_NAME:-pitguard-worker}"
if [ "${EUID:-$(id -u)}" -ne 0 ]; then exec sudo -E bash "$0" "$@"; fi
systemctl restart "$SERVICE_NAME"
systemctl restart "$WORKER_SERVICE_NAME"
systemctl reload nginx
systemctl --no-pager --full status "$SERVICE_NAME"
systemctl --no-pager --full status "$WORKER_SERVICE_NAME"
