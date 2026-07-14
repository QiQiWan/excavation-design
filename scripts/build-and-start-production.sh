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
DOMAIN="${PITGUARD_DOMAIN:-designer.eatrice.cn}"
BACKEND_PORT="${PITGUARD_BACKEND_PORT:-8002}"
PYTHON_BIN="${PYTHON_BIN:-}"
CERT_FILE="${PITGUARD_SSL_CERTIFICATE:-/usr/crt/fullchain.pem}"
KEY_FILE="${PITGUARD_SSL_CERTIFICATE_KEY:-/usr/crt/privkey.pem}"
SERVICE_NAME="${PITGUARD_SERVICE_NAME:-pitguard-api}"
ENV_DIR="${PITGUARD_ENV_DIR:-/etc/pitguard}"
ENV_FILE="$ENV_DIR/pitguard.env"
API_KEY_FILE="$ENV_DIR/api-key"
WEB_CREDENTIAL_FILE="$ENV_DIR/web-credentials.txt"
SESSION_SECRET_FILE="$ENV_DIR/session-secret"
NGINX_CONF="${PITGUARD_NGINX_CONF:-/etc/nginx/conf.d/${DOMAIN}.conf}"
SYSTEMD_UNIT="/etc/systemd/system/${SERVICE_NAME}.service"
WEB_USER="${PITGUARD_WEB_USER:-pitguard}"
WEB_PASSWORD="${PITGUARD_WEB_PASSWORD:-}"
NUMERIC_THREADS="${PITGUARD_NUMERIC_THREADS:-1}"

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

mkdir -p "$RUNTIME_DIR/backups" "$RUNTIME_DIR/cache" "$RUNTIME_DIR/matplotlib" \
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
PITGUARD_BACKUP_DIR=$RUNTIME_DIR/backups
PITGUARD_BACKUP_RETENTION=${PITGUARD_BACKUP_RETENTION:-30}
PITGUARD_REVISION_RETENTION=${PITGUARD_REVISION_RETENTION:-100}
PITGUARD_NUMERIC_THREADS=$NUMERIC_THREADS
OPENBLAS_NUM_THREADS=$NUMERIC_THREADS
OMP_NUM_THREADS=$NUMERIC_THREADS
MKL_NUM_THREADS=$NUMERIC_THREADS
NUMEXPR_NUM_THREADS=$NUMERIC_THREADS
VECLIB_MAXIMUM_THREADS=$NUMERIC_THREADS
PITGUARD_CORS_ORIGINS=https://$DOMAIN
PITGUARD_USERS='$PITGUARD_USERS_JSON'
PITGUARD_SESSION_SECRET=$SESSION_SECRET
PITGUARD_SESSION_TTL_SECONDS=${PITGUARD_SESSION_TTL_SECONDS:-28800}
PITGUARD_COOKIE_SECURE=true
PITGUARD_API_KEYS='{"$API_KEY":{"role":"admin","actor":"automation-api","keyId":"automation-1"}}'
ENVEOF
chmod 600 "$ENV_FILE"

cat > "$SYSTEMD_UNIT" <<UNITEOF
[Unit]
Description=PitGuard FastAPI Backend
Wants=network-online.target
After=network-online.target

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
ExecStart=$PYTHON_BIN -m uvicorn app.main:app --host 127.0.0.1 --port $BACKEND_PORT --workers 1 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=on-failure
RestartSec=5s
TimeoutStartSec=60s
TimeoutStopSec=45s
KillSignal=SIGINT
UMask=0027
LimitNOFILE=65535
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=read-only
ReadWritePaths=$RUNTIME_DIR
ReadWritePaths=$API_DIR/exports
ReadWritePaths=$API_DIR/runtime_cache

[Install]
WantedBy=multi-user.target
UNITEOF

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
        proxy_connect_timeout 10s;
        proxy_read_timeout 30s;
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
systemctl enable "$SERVICE_NAME" >/dev/null
systemctl restart "$SERVICE_NAME"

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
Backend       : http://127.0.0.1:$BACKEND_PORT
Frontend dist : $WEB_DIR/dist
Database      : $RUNTIME_DIR/pitguard.sqlite3
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
