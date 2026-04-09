#!/usr/bin/env bash
# Install the danbooru-ml-classifier API server as a systemd user service
# and configure Nginx with a Let's Encrypt SSL certificate.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
ASDF_SHIMS_DIR="$HOME/.asdf/shims"
ASDF_BIN_DIR="$HOME/.asdf/bin"
API_DOMAIN="danbooru-api.matrix.hakatashi.com"
NGINX_AVAILABLE="/etc/nginx/sites-available/$API_DOMAIN"
NGINX_ENABLED="/etc/nginx/sites-enabled/$API_DOMAIN"

# ── 1. Install Python dependencies ────────────────────────────────────────────
echo "Installing Python dependencies ..."
"$REPO_DIR/worker/venv/bin/pip" install -q \
    "fastapi>=0.110.0" \
    "uvicorn[standard]>=0.29.0"

# ── 2. Install systemd user service ───────────────────────────────────────────
mkdir -p "$SYSTEMD_USER_DIR"

sed \
    -e "s|REPO_DIR|$REPO_DIR|g" \
    -e "s|ASDF_SHIMS_DIR|$ASDF_SHIMS_DIR|g" \
    -e "s|ASDF_BIN_DIR|$ASDF_BIN_DIR|g" \
    "$SCRIPT_DIR/danbooru-ml-api.service" \
    > "$SYSTEMD_USER_DIR/danbooru-ml-api.service"

echo "Installed $SYSTEMD_USER_DIR/danbooru-ml-api.service"

systemctl --user daemon-reload
systemctl --user enable --now danbooru-ml-api.service
echo "Service status:"
systemctl --user status danbooru-ml-api.service --no-pager || true

# ── 3. Install Nginx config ────────────────────────────────────────────────────
echo ""
echo "Installing Nginx config for $API_DOMAIN ..."
sudo cp "$SCRIPT_DIR/danbooru-ml-api.nginx.conf" "$NGINX_AVAILABLE"
sudo ln -sf "$NGINX_AVAILABLE" "$NGINX_ENABLED"
sudo nginx -t
sudo systemctl reload nginx
echo "Nginx configured."

# ── 4. Obtain Let's Encrypt certificate ────────────────────────────────────────
echo ""
echo "Obtaining SSL certificate for $API_DOMAIN ..."
sudo certbot --nginx -d "$API_DOMAIN" --non-interactive --agree-tos \
    --email "$(git config user.email 2>/dev/null || echo 'admin@hakatashi.com')" \
    --redirect
echo "SSL certificate obtained and Nginx updated."

echo ""
echo "Done! API is accessible at https://$API_DOMAIN"
echo "API docs: https://$API_DOMAIN/docs"
