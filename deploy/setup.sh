#!/usr/bin/env bash
# =============================================================================
# deploy/setup.sh
#
# ONE-TIME server bootstrap for Omni Link Django API on Ubuntu 22.04 EC2.
# Run ONCE as the ubuntu user after the instance first boots.
#
# Usage:
#   chmod +x deploy/setup.sh
#   sudo bash deploy/setup.sh
#
# After this script completes:
#   1. Point your DNS A record:  wms.uncoverskincare.com → <EC2 public IP>
#   2. Run certbot to issue the TLS certificate (instructions printed at end).
#   3. Push to main — the GitHub Actions workflow handles all future deploys.
# =============================================================================
set -euo pipefail

DOMAIN="wms.uncoverskincare.com"
APP_ROOT="/opt/omnilink"
PROJECT_DIR="${APP_ROOT}/project"
VENV_DIR="${APP_ROOT}/venv"
STATIC_ROOT="/var/www/omnilink/staticfiles"
MEDIA_ROOT="/var/www/omnilink/media"
LOG_DIR_GUNICORN="/var/log/gunicorn"
NGINX_CONF_DEST="/etc/nginx/sites-available/omnilink"
SERVICE_NAME="omnilink-gunicorn"
REPO_URL="${1:-}"           # optional: pass repo URL as first arg

echo "================================================================="
echo " Omni Link API — First-Run Server Setup"
echo " Domain : ${DOMAIN}"
echo " App root: ${APP_ROOT}"
echo "================================================================="

# ──────────────────────────────────────────────────────────────────────────────
# 1. System packages
# ──────────────────────────────────────────────────────────────────────────────
echo "[1/9] Installing system packages..."
apt-get update -qq
apt-get install -y --no-install-recommends \
    python3.11 python3.11-venv python3.11-dev \
    build-essential \
    nginx \
    certbot python3-certbot-nginx \
    git \
    postgresql-client \
    curl \
    logrotate

# ──────────────────────────────────────────────────────────────────────────────
# 2. Directory layout
# ──────────────────────────────────────────────────────────────────────────────
echo "[2/9] Creating directory structure..."
mkdir -p "${APP_ROOT}"
mkdir -p "${PROJECT_DIR}"
mkdir -p "${STATIC_ROOT}"
mkdir -p "${MEDIA_ROOT}"
mkdir -p "${LOG_DIR_GUNICORN}"
mkdir -p "/var/www/certbot"

# Ownership: ubuntu runs gunicorn; www-data is Nginx's group
chown -R ubuntu:www-data "${APP_ROOT}"
chown -R ubuntu:www-data "/var/www/omnilink"
chown -R ubuntu:www-data "${LOG_DIR_GUNICORN}"
chmod -R 775 "${STATIC_ROOT}"
chmod -R 775 "${MEDIA_ROOT}"

# ──────────────────────────────────────────────────────────────────────────────
# 3. Python virtual environment
# ──────────────────────────────────────────────────────────────────────────────
echo "[3/9] Creating Python virtual environment..."
if [ ! -d "${VENV_DIR}" ]; then
    sudo -u ubuntu python3.11 -m venv "${VENV_DIR}"
fi
sudo -u ubuntu "${VENV_DIR}/bin/pip" install --quiet --upgrade pip wheel

# ──────────────────────────────────────────────────────────────────────────────
# 4. Clone / pull repository
# ──────────────────────────────────────────────────────────────────────────────
echo "[4/9] Fetching application code..."
if [ -n "${REPO_URL}" ]; then
    if [ ! -d "${PROJECT_DIR}/.git" ]; then
        sudo -u ubuntu git clone "${REPO_URL}" "${PROJECT_DIR}"
    else
        sudo -u ubuntu git -C "${PROJECT_DIR}" pull origin main
    fi
else
    echo "  → No REPO_URL provided; assuming code will be deployed via GitHub Actions."
fi

# ──────────────────────────────────────────────────────────────────────────────
# 5. .env.prod placeholder (operator must fill this in before starting gunicorn)
# ──────────────────────────────────────────────────────────────────────────────
echo "[5/9] Creating .env.prod template..."
ENV_FILE="${APP_ROOT}/.env.prod"
if [ ! -f "${ENV_FILE}" ]; then
    cat > "${ENV_FILE}" <<'ENVEOF'
# =============================================================
# Omni Link – Production Environment Variables
# Fill in every value before starting the service.
# =============================================================

# Django
DJANGO_SECRET_KEY=REPLACE_WITH_LONG_RANDOM_SECRET
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=wms.uncoverskincare.com

# Postgres (Amazon RDS)
DB_NAME=omnilink_prod
DB_USER=omnilink_app_user
DB_PASSWORD=REPLACE_WITH_RDS_PASSWORD
DB_HOST=REPLACE.rds.amazonaws.com
DB_PORT=5432

# Static / Media
STATIC_ROOT=/var/www/omnilink/staticfiles
MEDIA_ROOT=/var/www/omnilink/media

# Google OAuth
GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=

# Shopify
SHOPIFY_WEBHOOK_SECRET=

# Email (SMTP)
DEFAULT_FROM_EMAIL=noreply@uncoverskincare.com
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.yourprovider.com
EMAIL_PORT=587
EMAIL_HOST_USER=noreply@uncoverskincare.com
EMAIL_HOST_PASSWORD=
EMAIL_USE_TLS=True

# Celery / Redis (if Redis is installed locally)
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0
CELERY_TASK_ALWAYS_EAGER=False
ENVEOF
    chown ubuntu:ubuntu "${ENV_FILE}"
    chmod 640 "${ENV_FILE}"
    echo "  → Created ${ENV_FILE}. EDIT THIS FILE before starting the service!"
else
    echo "  → ${ENV_FILE} already exists, skipping."
fi

# ──────────────────────────────────────────────────────────────────────────────
# 6. Gunicorn systemd service
# ──────────────────────────────────────────────────────────────────────────────
echo "[6/9] Installing Gunicorn systemd service..."
SERVICE_SRC="${PROJECT_DIR}/deploy/systemd/gunicorn.service"
SERVICE_DEST="/etc/systemd/system/${SERVICE_NAME}.service"

if [ -f "${SERVICE_SRC}" ]; then
    cp "${SERVICE_SRC}" "${SERVICE_DEST}"
else
    # Inline fallback if repo wasn't cloned yet
    cat > "${SERVICE_DEST}" <<SVCEOF
[Unit]
Description=Gunicorn daemon for Omni Link Django API
After=network.target
Wants=network-online.target

[Service]
User=ubuntu
Group=www-data
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=${APP_ROOT}/.env.prod

ExecStart=${VENV_DIR}/bin/gunicorn \\
    --workers 4 \\
    --worker-class sync \\
    --bind 127.0.0.1:8000 \\
    --timeout 120 \\
    --keep-alive 5 \\
    --max-requests 1000 \\
    --max-requests-jitter 100 \\
    --log-level info \\
    --access-logfile ${LOG_DIR_GUNICORN}/omnilink_access.log \\
    --error-logfile  ${LOG_DIR_GUNICORN}/omnilink_error.log \\
    api.wsgi:application

ExecReload=/bin/kill -s HUP \$MAINPID
Restart=on-failure
RestartSec=5
SyslogIdentifier=omnilink-gunicorn
KillMode=mixed
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
SVCEOF
fi

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"

# ──────────────────────────────────────────────────────────────────────────────
# 7. Nginx configuration
# ──────────────────────────────────────────────────────────────────────────────
echo "[7/9] Installing Nginx configuration..."
NGINX_SRC="${PROJECT_DIR}/deploy/nginx/default.conf"

if [ -f "${NGINX_SRC}" ]; then
    cp "${NGINX_SRC}" "${NGINX_CONF_DEST}"
else
    # Inline fallback — HTTP only until certbot runs
    cat > "${NGINX_CONF_DEST}" <<'NGXEOF'
server {
    listen 80;
    server_name wms.uncoverskincare.com;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location /static/ {
        alias /var/www/omnilink/staticfiles/;
        expires 7d;
        add_header Cache-Control "public, max-age=604800, immutable";
        access_log off;
    }

    location /media/ {
        alias /var/www/omnilink/media/;
        expires 1d;
        access_log off;
    }

    location /healthz {
        return 200 "ok\n";
        add_header Content-Type text/plain;
        access_log off;
    }

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_connect_timeout 60s;
        proxy_send_timeout    120s;
        proxy_read_timeout    120s;
    }
}
NGXEOF
fi

# Enable site, remove default if present
ln -sf "${NGINX_CONF_DEST}" /etc/nginx/sites-enabled/omnilink
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl enable nginx
systemctl reload nginx

# ──────────────────────────────────────────────────────────────────────────────
# 8. Logrotate for Gunicorn
# ──────────────────────────────────────────────────────────────────────────────
echo "[8/9] Configuring log rotation..."
cat > /etc/logrotate.d/omnilink-gunicorn <<'LREOF'
/var/log/gunicorn/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    sharedscripts
    postrotate
        systemctl kill -s USR1 omnilink-gunicorn.service || true
    endscript
}
LREOF

# ──────────────────────────────────────────────────────────────────────────────
# 9. sudoers — allow ubuntu to restart services without a password
#    (needed by the GitHub Actions deploy step)
# ──────────────────────────────────────────────────────────────────────────────
echo "[9/9] Configuring passwordless sudo for service management..."
SUDOERS_FILE="/etc/sudoers.d/omnilink-deploy"
cat > "${SUDOERS_FILE}" <<SUDOEOF
ubuntu ALL=(ALL) NOPASSWD: /bin/systemctl restart omnilink-gunicorn, \\
                            /bin/systemctl reload  omnilink-gunicorn, \\
                            /bin/systemctl reload  nginx, \\
                            /bin/systemctl restart nginx
SUDOEOF
chmod 440 "${SUDOERS_FILE}"
visudo -cf "${SUDOERS_FILE}"

# ──────────────────────────────────────────────────────────────────────────────
# Done
# ──────────────────────────────────────────────────────────────────────────────
echo ""
echo "================================================================="
echo " Setup complete!"
echo "================================================================="
echo ""
echo "NEXT STEPS:"
echo ""
echo "  1. Edit the production env file:"
echo "       sudo nano ${APP_ROOT}/.env.prod"
echo ""
echo "  2. Point your DNS A record:"
echo "       wms.uncoverskincare.com  →  $(curl -sf https://checkip.amazonaws.com || echo '<EC2 public IP>')"
echo ""
echo "  3. Wait for DNS to propagate, then obtain a TLS certificate:"
echo "       sudo certbot --nginx -d ${DOMAIN} --non-interactive --agree-tos -m admin@uncoverskincare.com"
echo "       sudo systemctl reload nginx"
echo ""
echo "  4. After your first code deploy (via GitHub Actions), start Gunicorn:"
echo "       sudo systemctl start ${SERVICE_NAME}"
echo "       sudo systemctl status ${SERVICE_NAME}"
echo ""
echo "  5. Enable auto-renewal (runs twice daily):"
echo "       sudo systemctl enable --now certbot.timer"
echo ""
echo "  6. Test the deployment:"
echo "       curl -I https://${DOMAIN}/healthz"
echo ""
echo "================================================================="

