#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    exec sudo -E bash "$0" "$@"
  fi
  echo "[PitGuard] Run this script as root." >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT_DIR/services/api"
WEB_DIR="$ROOT_DIR/apps/web"
RUNTIME_DIR="$ROOT_DIR/runtime"
ARTIFACT_DIR="${PITGUARD_ARTIFACT_ROOT:-$RUNTIME_DIR/artifacts}"
DOMAIN="${PITGUARD_DOMAIN:-designer.eatrice.cn}"
BACKEND_PORT="${PITGUARD_BACKEND_PORT:-8002}"
PYTHON_BIN="${PYTHON_BIN:-}"
CERT_FILE="${PITGUARD_SSL_CERTIFICATE:-/usr/crt/fullchain.pem}"
KEY_FILE="${PITGUARD_SSL_CERTIFICATE_KEY:-/usr/crt/privkey.pem}"
SERVICE_NAME="${PITGUARD_SERVICE_NAME:-pitguard-api}"
WORKER_SERVICE_NAME="${PITGUARD_WORKER_SERVICE_NAME:-pitguard-worker}"
ENV_DIR="${PITGUARD_ENV_DIR:-/etc/pitguard}"
ENV_FILE="$ENV_DIR/pitguard.env"
API_KEY_FILE="$ENV_DIR/api-key"
WEB_CREDENTIAL_FILE="$ENV_DIR/web-credentials.txt"
SESSION_SECRET_FILE="$ENV_DIR/session-secret"
NGINX_CONF="${PITGUARD_NGINX_CONF:-/etc/nginx/conf.d/${DOMAIN}.conf}"
SYSTEMD_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
WORKER_SYSTEMD_UNIT="/etc/systemd/system/${WORKER_SERVICE_NAME}.service"
WEB_USER="${PITGUARD_WEB_USER:-pitguard}"
WEB_PASSWORD="${PITGUARD_WEB_PASSWORD:-}"
NUMERIC_THREADS="${PITGUARD_NUMERIC_THREADS:-1}"
TASK_WORKERS="${PITGUARD_TASK_WORKERS:-2}"
HEAVY_TASK_CONCURRENCY="${PITGUARD_HEAVY_TASK_CONCURRENCY:-1}"
TASK_TIMEOUT_SECONDS="${PITGUARD_TASK_TIMEOUT_SECONDS:-1800}"
CANDIDATE_WORKERS="${PITGUARD_CANDIDATE_WORKERS:-1}"
CALC_RESULT_RETENTION="${PITGUARD_CALCULATION_RESULT_RETENTION:-1}"
API_MEMORY_HIGH="${PITGUARD_API_MEMORY_HIGH:-2G}"
API_MEMORY_MAX="${PITGUARD_API_MEMORY_MAX:-4G}"
API_FULL_PROJECT_LIMIT_MB="${PITGUARD_API_FULL_PROJECT_LIMIT_MB:-96}"
WORKER_CPU_QUOTA="${PITGUARD_WORKER_CPU_QUOTA:-300%}"

if [ -z "$PYTHON_BIN" ]; then
  if [ -x /root/anaconda3/envs/ifc/bin/python ]; then
    PYTHON_BIN=/root/anaconda3/envs/ifc/bin/python
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python)"
  fi
fi

for command_name in npm nginx openssl systemctl curl; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "[PitGuard] Required command is missing: $command_name" >&2
    exit 1
  fi
done
if [ -z "$PYTHON_BIN" ] || [ ! -x "$PYTHON_BIN" ]; then
  echo "[PitGuard] Python executable was not found. Set PYTHON_BIN=/path/to/python." >&2
  exit 1
fi
if [ ! -f "$CERT_FILE" ] || [ ! -f "$KEY_FILE" ]; then
  echo "[PitGuard] SSL certificate files were not found:" >&2
  echo "  $CERT_FILE" >&2
  echo "  $KEY_FILE" >&2
  exit 1
fi

TOTAL_MEMORY_MB="$(awk '/MemTotal:/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo 8192)"
if [ -z "$TOTAL_MEMORY_MB" ] || [ "$TOTAL_MEMORY_MB" -lt 4096 ]; then TOTAL_MEMORY_MB=4096; fi
AVAILABLE_MEMORY_MB="$(awk '/MemAvailable:/ {print int($2/1024)}' /proc/meminfo 2>/dev/null || echo $((TOTAL_MEMORY_MB * 70 / 100)))"
if [ -z "$AVAILABLE_MEMORY_MB" ] || [ "$AVAILABLE_MEMORY_MB" -lt 2048 ]; then AVAILABLE_MEMORY_MB=2048; fi
SYSTEM_MEMORY_RESERVE_MB="${PITGUARD_SYSTEM_MEMORY_RESERVE_MB:-$((TOTAL_MEMORY_MB * 12 / 100))}"
if [ "$SYSTEM_MEMORY_RESERVE_MB" -lt 2048 ]; then SYSTEM_MEMORY_RESERVE_MB=2048; fi
if [ "$SYSTEM_MEMORY_RESERVE_MB" -gt 8192 ]; then SYSTEM_MEMORY_RESERVE_MB=8192; fi

# Size the worker from memory that is actually available at deployment time.
# A large host may already run databases, dashboards or other services; using a
# percentage of total RAM alone allowed PitGuard to push the entire node into
# reclaim/OOM even though its own cgroup limit had not been reached.
DEFAULT_WORKER_MEMORY_MAX_MB=$((TOTAL_MEMORY_MB * 50 / 100))
AVAILABLE_WORKER_BUDGET_MB=$((AVAILABLE_MEMORY_MB - SYSTEM_MEMORY_RESERVE_MB))
if [ "$AVAILABLE_WORKER_BUDGET_MB" -lt "$DEFAULT_WORKER_MEMORY_MAX_MB" ]; then
  DEFAULT_WORKER_MEMORY_MAX_MB=$AVAILABLE_WORKER_BUDGET_MB
fi
if [ "$DEFAULT_WORKER_MEMORY_MAX_MB" -gt 32768 ]; then DEFAULT_WORKER_MEMORY_MAX_MB=32768; fi
if [ "$DEFAULT_WORKER_MEMORY_MAX_MB" -lt 2048 ]; then DEFAULT_WORKER_MEMORY_MAX_MB=2048; fi
WORKER_MEMORY_MAX_MB="${PITGUARD_WORKER_MEMORY_MAX_MB:-$DEFAULT_WORKER_MEMORY_MAX_MB}"
WORKER_MEMORY_HIGH_MB="${PITGUARD_WORKER_MEMORY_HIGH_MB:-$((WORKER_MEMORY_MAX_MB * 75 / 100))}"
TASK_MEMORY_SOFT_LIMIT_MB="${PITGUARD_TASK_MEMORY_SOFT_LIMIT_MB:-$((WORKER_MEMORY_HIGH_MB - 256))}"
if [ "$TASK_MEMORY_SOFT_LIMIT_MB" -lt 2048 ]; then TASK_MEMORY_SOFT_LIMIT_MB=2048; fi
WORKER_RSS_HARD_LIMIT_MB="${PITGUARD_WORKER_RSS_HARD_LIMIT_MB:-$((WORKER_MEMORY_MAX_MB * 90 / 100))}"
RESOURCE_WATCH_INTERVAL_SECONDS="${PITGUARD_RESOURCE_WATCH_INTERVAL_SECONDS:-3}"

mkdir -p "$RUNTIME_DIR/backups" "$RUNTIME_DIR/cache" "$RUNTIME_DIR/matplotlib" "$ARTIFACT_DIR" \
  "$RUNTIME_DIR/cache-worker" "$RUNTIME_DIR/matplotlib-worker" \
  "$API_DIR/exports" "$API_DIR/runtime_cache" "$ENV_DIR"
chmod 750 "$ENV_DIR"

PYTHON_BIN="$PYTHON_BIN" PITGUARD_INSTALL_DEPS="${PITGUARD_INSTALL_DEPS:-1}" \
  bash "$ROOT_DIR/scripts/build-production.sh"

if [ ! -s "$API_KEY_FILE" ]; then
  openssl rand -hex 32 > "$API_KEY_FILE"
fi
API_KEY="$(tr -d '\r\n' < "$API_KEY_FILE")"
chmod 600 "$API_KEY_FILE"

if [ ! -s "$SESSION_SECRET_FILE" ]; then
  openssl rand -hex 48 > "$SESSION_SECRET_FILE"
fi
SESSION_SECRET="$(tr -d '\r\n' < "$SESSION_SECRET_FILE")"
chmod 600 "$SESSION_SECRET_FILE"

if [ -z "$WEB_PASSWORD" ]; then
  if [ -s "$WEB_CREDENTIAL_FILE" ]; then
    WEB_PASSWORD="$(awk -F= '$1=="password" {print substr($0, index($0,"=")+1)}' "$WEB_CREDENTIAL_FILE")"
  else
    WEB_PASSWORD="$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)"
  fi
fi
PASSWORD_HASH="$($PYTHON_BIN - "$WEB_PASSWORD" <<'PYHASH'
import base64, hashlib, os, sys
password = sys.argv[1].encode('utf-8')
salt = os.urandom(18)
iterations = 240000
digest = hashlib.pbkdf2_hmac('sha256', password, salt, iterations)
b64 = lambda value: base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')
print(f"pbkdf2_sha256${iterations}${b64(salt)}${b64(digest)}")
PYHASH
)"
PITGUARD_USERS_JSON="$($PYTHON_BIN - "$WEB_USER" "$PASSWORD_HASH" <<'PYUSERS'
import json, sys
username, password_hash = sys.argv[1], sys.argv[2]
print(json.dumps({username: {"passwordHash": password_hash, "role": "admin", "actor": username, "userId": "primary-admin"}}, separators=(',', ':')))
PYUSERS
)"
cat > "$WEB_CREDENTIAL_FILE" <<CREDEOF
url=https://$DOMAIN
username=$WEB_USER
password=$WEB_PASSWORD
auth_mode=application_login_page
CREDEOF
chmod 600 "$WEB_CREDENTIAL_FILE"

cat > "$ENV_FILE" <<ENVEOF
PITGUARD_DB_PATH=$RUNTIME_DIR/pitguard.sqlite3
PITGUARD_ARTIFACT_ROOT=$ARTIFACT_DIR
PITGUARD_ARTIFACT_THRESHOLD_MB=${PITGUARD_ARTIFACT_THRESHOLD_MB:-1}
PITGUARD_STAGE_RESULT_CHUNK_SIZE=${PITGUARD_STAGE_RESULT_CHUNK_SIZE:-100}
PITGUARD_GEOLOGY_PREVIEW_AXIS=${PITGUARD_GEOLOGY_PREVIEW_AXIS:-36}
PITGUARD_BACKUP_DIR=$RUNTIME_DIR/backups
PITGUARD_BACKUP_RETENTION=${PITGUARD_BACKUP_RETENTION:-30}
PITGUARD_REVISION_RETENTION=${PITGUARD_REVISION_RETENTION:-30}
PITGUARD_NUMERIC_THREADS=$NUMERIC_THREADS
OPENBLAS_NUM_THREADS=$NUMERIC_THREADS
OMP_NUM_THREADS=$NUMERIC_THREADS
MKL_NUM_THREADS=$NUMERIC_THREADS
NUMEXPR_NUM_THREADS=$NUMERIC_THREADS
VECLIB_MAXIMUM_THREADS=$NUMERIC_THREADS
MALLOC_ARENA_MAX=2
PITGUARD_TASK_WORKERS=$TASK_WORKERS
PITGUARD_HEAVY_TASK_CONCURRENCY=$HEAVY_TASK_CONCURRENCY
PITGUARD_TASK_MEMORY_SOFT_LIMIT_MB=$TASK_MEMORY_SOFT_LIMIT_MB
PITGUARD_WORKER_MEMORY_MAX_MB=$WORKER_MEMORY_MAX_MB
PITGUARD_WORKER_RSS_HARD_LIMIT_MB=$WORKER_RSS_HARD_LIMIT_MB
PITGUARD_SYSTEM_MEMORY_RESERVE_MB=$SYSTEM_MEMORY_RESERVE_MB
PITGUARD_RESOURCE_WATCH_INTERVAL_SECONDS=$RESOURCE_WATCH_INTERVAL_SECONDS
PITGUARD_TASK_TIMEOUT_SECONDS=$TASK_TIMEOUT_SECONDS
PITGUARD_WORKER_POLL_SECONDS=1.0
PITGUARD_WORKER_HEARTBEAT_PATH=$RUNTIME_DIR/worker-heartbeat.json
PITGUARD_WORKER_EXIT_AFTER_TASK=true
PITGUARD_CANDIDATE_WORKERS=$CANDIDATE_WORKERS
PITGUARD_CALCULATION_RESULT_RETENTION=$CALC_RESULT_RETENTION
PITGUARD_SUPPORT_CANDIDATE_TRIAL_LIMIT=${PITGUARD_SUPPORT_CANDIDATE_TRIAL_LIMIT:-36}
PITGUARD_MAX_SUPPORT_ELEMENTS=${PITGUARD_MAX_SUPPORT_ELEMENTS:-2400}
PITGUARD_API_FULL_PROJECT_LIMIT_MB=$API_FULL_PROJECT_LIMIT_MB
PITGUARD_MIGRATE_ON_FULL_LOAD=0
PITGUARD_CORS_ORIGINS=https://$DOMAIN
PITGUARD_USERS='$PITGUARD_USERS_JSON'
PITGUARD_SESSION_SECRET=$SESSION_SECRET
PITGUARD_SESSION_TTL_SECONDS=${PITGUARD_SESSION_TTL_SECONDS:-28800}
PITGUARD_COOKIE_SECURE=true
PITGUARD_API_KEYS='{"$API_KEY":{"role":"admin","actor":"automation-api","keyId":"automation-1"}}'
ENVEOF
chmod 600 "$ENV_FILE"

if [ -s "$RUNTIME_DIR/pitguard.sqlite3" ]; then
  "$PYTHON_BIN" - "$RUNTIME_DIR/pitguard.sqlite3" "$RUNTIME_DIR/backups" <<'PYBACKUP'
import sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path
source_path = Path(sys.argv[1])
backup_dir = Path(sys.argv[2])
backup_dir.mkdir(parents=True, exist_ok=True)
destination = backup_dir / f"pre_v331_artifact_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.sqlite3"
with sqlite3.connect(source_path, timeout=60.0) as source, sqlite3.connect(destination, timeout=60.0) as target:
    source.backup(target)
    target.commit()
print(f"[PitGuard] pre-migration database backup: {destination}")
PYBACKUP
fi

PYTHONPATH="$API_DIR" PITGUARD_DB_PATH="$RUNTIME_DIR/pitguard.sqlite3" \
  "$PYTHON_BIN" "$ROOT_DIR/scripts/prepare-project-workspace-storage.py" \
  --database "$RUNTIME_DIR/pitguard.sqlite3"

PYTHONPATH="$API_DIR" PITGUARD_DB_PATH="$RUNTIME_DIR/pitguard.sqlite3" PITGUARD_ARTIFACT_ROOT="$ARTIFACT_DIR" \
  "$PYTHON_BIN" "$ROOT_DIR/scripts/prepare-project-artifact-storage.py" \
  --database "$RUNTIME_DIR/pitguard.sqlite3"

PYTHONPATH="$API_DIR" PITGUARD_DB_PATH="$RUNTIME_DIR/pitguard.sqlite3" PITGUARD_ARTIFACT_ROOT="$ARTIFACT_DIR" \
  "$PYTHON_BIN" "$ROOT_DIR/scripts/garbage-collect-artifacts.py" \
  --database "$RUNTIME_DIR/pitguard.sqlite3" --delete

cat > "$SYSTEMD_UNIT" <<UNITEOF
[Unit]
Description=PitGuard FastAPI Backend
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=120
StartLimitBurst=8

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$API_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONPATH=$API_DIR
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=MPLCONFIGDIR=$RUNTIME_DIR/matplotlib
Environment=XDG_CACHE_HOME=$RUNTIME_DIR/cache
Environment=PITGUARD_TASK_EXECUTION_MODE=external
Environment=PITGUARD_PROCESS_ROLE=api
Environment=PITGUARD_SLOW_REQUEST_MS=1200
ExecStart=$PYTHON_BIN -m uvicorn app.main:app --host 127.0.0.1 --port $BACKEND_PORT --workers 1 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=5s
TimeoutStartSec=60s
TimeoutStopSec=45s
KillSignal=SIGINT
UMask=0027
LimitNOFILE=65535
MemoryHigh=$API_MEMORY_HIGH
MemoryMax=$API_MEMORY_MAX
MemorySwapMax=0
CPUQuota=200%
CPUWeight=100
IOWeight=100
TasksMax=256
OOMScoreAdjust=-500
OOMPolicy=stop
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$RUNTIME_DIR
ReadWritePaths=$ARTIFACT_DIR
ReadWritePaths=$API_DIR/exports
ReadWritePaths=$API_DIR/runtime_cache

[Install]
WantedBy=multi-user.target
UNITEOF

cat > "$WORKER_SYSTEMD_UNIT" <<WORKERUNITEOF
[Unit]
Description=PitGuard Isolated Calculation Worker
Wants=network-online.target
After=network-online.target ${SERVICE_NAME}.service
StartLimitIntervalSec=120
StartLimitBurst=30

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=$API_DIR
EnvironmentFile=$ENV_FILE
Environment=PYTHONPATH=$API_DIR
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=MPLCONFIGDIR=$RUNTIME_DIR/matplotlib-worker
Environment=XDG_CACHE_HOME=$RUNTIME_DIR/cache-worker
Environment=PITGUARD_TASK_EXECUTION_MODE=worker
Environment=PITGUARD_PROCESS_ROLE=worker
Environment=PITGUARD_MIGRATE_ON_FULL_LOAD=1
ExecStart=$PYTHON_BIN -m app.tasks.worker_daemon
Restart=always
RestartSec=1s
TimeoutStartSec=60s
TimeoutStopSec=30s
KillSignal=SIGTERM
UMask=0027
LimitNOFILE=65535
MemoryHigh=${WORKER_MEMORY_HIGH_MB}M
MemoryMax=${WORKER_MEMORY_MAX_MB}M
MemorySwapMax=0
CPUQuota=$WORKER_CPU_QUOTA
CPUWeight=20
IOWeight=20
Nice=5
IOSchedulingClass=idle
IOSchedulingPriority=7
OOMScoreAdjust=800
TasksMax=128
LimitNPROC=128
OOMPolicy=stop
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$RUNTIME_DIR
ReadWritePaths=$ARTIFACT_DIR
ReadWritePaths=$API_DIR/exports
ReadWritePaths=$API_DIR/runtime_cache

[Install]
WantedBy=multi-user.target
WORKERUNITEOF

"$PYTHON_BIN" "$ROOT_DIR/scripts/cleanup-nginx-domain.py" --domain "$DOMAIN" --exclude "$NGINX_CONF"

cat > "$NGINX_CONF" <<NGINXEOF
upstream pitguard_api {
    server 127.0.0.1:$BACKEND_PORT;
    keepalive 16;
}

server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $DOMAIN;
    auth_basic off;

    root $WEB_DIR/dist;
    index index.html;

    ssl_certificate $CERT_FILE;
    ssl_certificate_key $KEY_FILE;
    ssl_session_timeout 1d;
    ssl_session_cache shared:PitGuardSSL:10m;
    ssl_session_tickets off;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305';
    ssl_prefer_server_ciphers off;

    client_max_body_size 512m;
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;


    access_log /var/log/nginx/pitguard_access.log;
    error_log /var/log/nginx/pitguard_error.log warn;

    location = /health {
        proxy_pass http://pitguard_api/health;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 2s;
        proxy_read_timeout 3s;
    }

    location = /health/live {
        proxy_pass http://pitguard_api/health/live;
        proxy_connect_timeout 1s;
        proxy_read_timeout 2s;
    }

    location = /health/ready {
        proxy_pass http://pitguard_api/health/ready;
        proxy_connect_timeout 1s;
        proxy_read_timeout 2s;
    }

    location = /api/auth/status {
        auth_basic off;
        proxy_pass http://pitguard_api/api/auth/status;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 2s;
        proxy_send_timeout 3s;
        proxy_read_timeout 3s;
        proxy_next_upstream error timeout http_502 http_503 http_504;
        add_header Cache-Control "no-store" always;
    }

    location /api/ {
        auth_basic off;
        proxy_pass http://pitguard_api;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Port \$server_port;
        proxy_connect_timeout 10s;
        proxy_send_timeout 600s;
        proxy_read_timeout 600s;
        proxy_buffering off;
    }

    location = /backend-docs {
        proxy_pass http://pitguard_api/docs;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location = /openapi.json {
        proxy_pass http://pitguard_api/openapi.json;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    location = /redoc {
        proxy_pass http://pitguard_api/redoc;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # Authenticated API responses use X-Accel-Redirect so large
    # engineering datasets are transferred by Nginx/sendfile without entering
    # the Python heap. This location cannot be requested directly.
    location /protected-artifacts/ {
        internal;
        alias $ARTIFACT_DIR/;
        sendfile on;
        tcp_nopush on;
        directio 8m;
        output_buffers 2 1m;
        add_header X-Content-Type-Options nosniff always;
    }

    location /assets/ {
        try_files \$uri =404;
        expires 1y;
        add_header Cache-Control "public, immutable";
        access_log off;
    }

    location = /login {
        auth_basic off;
        expires -1;
        add_header Cache-Control "no-store, no-cache, must-revalidate";
        try_files /index.html =404;
    }

    location = /index.html {
        expires -1;
        add_header Cache-Control "no-store, no-cache, must-revalidate";
        try_files \$uri =404;
    }

    location / {
        auth_basic off;
        try_files \$uri \$uri/ /index.html;
    }
}
NGINXEOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME" "$WORKER_SERVICE_NAME" >/dev/null
systemctl restart "$SERVICE_NAME"
systemctl restart "$WORKER_SERVICE_NAME"

BACKEND_READY=0
for _ in $(seq 1 45); do
  if curl -fsS "http://127.0.0.1:$BACKEND_PORT/health" >/dev/null 2>&1; then
    BACKEND_READY=1
    break
  fi
  sleep 1
done
if [ "$BACKEND_READY" != "1" ]; then
  echo "[PitGuard] Backend health check failed." >&2
  journalctl -u "$SERVICE_NAME" -n 120 --no-pager >&2 || true
  journalctl -u "$WORKER_SERVICE_NAME" -n 120 --no-pager >&2 || true
  exit 1
fi

if ! systemctl is-active --quiet "$WORKER_SERVICE_NAME"; then
  echo "[PitGuard] Isolated calculation worker failed to start." >&2
  journalctl -u "$WORKER_SERVICE_NAME" -n 120 --no-pager >&2 || true
  exit 1
fi

AUTH_LOGIN_REQUIRED="$(curl -fsS "http://127.0.0.1:$BACKEND_PORT/api/auth/status" | "$PYTHON_BIN" -c 'import json,sys; print("true" if json.load(sys.stdin).get("loginRequired") else "false")')"
if [ "$AUTH_LOGIN_REQUIRED" != "true" ]; then
  echo "[PitGuard] Production login is not active. Check PITGUARD_USERS in $ENV_FILE." >&2
  exit 1
fi

rm -f /etc/nginx/.htpasswd-pitguard 2>/dev/null || true
nginx -t
systemctl reload nginx


ROOT_HEADERS="$(curl -kIsS --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/" || true)"
if printf '%s\n' "$ROOT_HEADERS" | grep -qi '^WWW-Authenticate:'; then
  echo "[PitGuard] Legacy HTTP Basic Auth is still active for the local Nginx virtual host https://$DOMAIN/." >&2
  echo "[PitGuard] Inspect: nginx -T | grep -n -C 4 -E 'server_name.*$DOMAIN|auth_basic'" >&2
  exit 1
fi
PUBLIC_ROOT_HEADERS="$(curl -kIsS --connect-timeout 8 "https://$DOMAIN/" || true)"
if printf '%s\n' "$PUBLIC_ROOT_HEADERS" | grep -qi '^WWW-Authenticate:'; then
  echo "[PitGuard] The public domain still returns a Basic Auth challenge. Check an upstream proxy/CDN or stale Nginx node." >&2
  exit 1
fi

PUBLIC_HEALTH="unverified"
LOGIN_ROUTE="unverified"
if curl -kfsS --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/health" >/dev/null 2>&1; then
  PUBLIC_HEALTH="ok"
fi
if curl -kfsS --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/login" >/dev/null 2>&1; then
  LOGIN_ROUTE="ok"
fi

cat <<OUTEOF

PitGuard production deployment is ready.
System URL    : https://$DOMAIN
Login URL     : https://$DOMAIN/login ($LOGIN_ROUTE)
Health        : https://$DOMAIN/health ($PUBLIC_HEALTH)
API docs      : https://$DOMAIN/backend-docs
Backend       : http://127.0.0.1:$BACKEND_PORT (HTTP only)
Calc worker    : $WORKER_SERVICE_NAME (isolated process, MemoryMax=${WORKER_MEMORY_MAX_MB}M, CPUQuota=$WORKER_CPU_QUOTA)
Frontend dist : $WEB_DIR/dist
Database      : $RUNTIME_DIR/pitguard.sqlite3
Artifact store : $ARTIFACT_DIR (Nginx direct transfer)
Service       : $SERVICE_NAME.service
Backend log   : journalctl -u $SERVICE_NAME -f
Nginx log     : /var/log/nginx/pitguard_error.log
Port 5173     : not used and not checked
OUTEOF

cat <<OUTEOF
Login mode    : application login page
Web username  : $WEB_USER
Web password  : $WEB_PASSWORD
Credentials   : $WEB_CREDENTIAL_FILE
Automation key: $API_KEY_FILE
OUTEOF
