#!/usr/bin/env bash
set -euo pipefail
SERVICE_NAME="${PITGUARD_SERVICE_NAME:-pitguard-api}"
if [ "${EUID:-$(id -u)}" -ne 0 ]; then exec sudo -E bash "$0" "$@"; fi
systemctl restart "$SERVICE_NAME"
systemctl reload nginx
systemctl --no-pager --full status "$SERVICE_NAME"
