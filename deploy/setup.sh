#!/bin/bash
# Setup script for Order System on Ubuntu VPS
# Usage: sudo bash deploy/setup.sh
set -e

APP_DIR="/opt/order"
APP_USER="www-data"

echo "=== Order System Setup ==="

# 1. Install system dependencies
echo "[1/8] Installing system packages..."
apt-get update
apt-get install -y python3 python3-venv python3-pip postgresql postgresql-contrib nginx

# 2. Create app directory
echo "[2/8] Setting up app directory..."
mkdir -p "$APP_DIR/logs" "$APP_DIR/media"

# 3. Copy project files (assumes you've cloned/copied to $APP_DIR)
if [ ! -f "$APP_DIR/manage.py" ]; then
    echo "ERROR: Copy project files to $APP_DIR first!"
    echo "  e.g.: rsync -av --exclude='.git' --exclude='db.sqlite3' --exclude='media/' ./ $APP_DIR/"
    exit 1
fi

# 4. Setup Python venv
echo "[3/8] Creating Python virtual environment..."
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

# 5. Setup PostgreSQL
echo "[4/8] Setting up PostgreSQL..."
echo "Create database and user with these commands:"
echo "  sudo -u postgres psql"
echo "  CREATE USER order_user WITH PASSWORD 'your-password';"
echo "  CREATE DATABASE order_db OWNER order_user;"
echo "  \\q"
echo ""
read -p "Have you created the database? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Please create the database first, then run this script again."
    exit 1
fi

# 6. Setup .env
echo "[5/8] Setting up environment..."
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    # Generate secret key
    SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")
    sed -i "s|your-secret-key-here-generate-a-new-one|$SECRET|" "$APP_DIR/.env"
    echo "IMPORTANT: Edit $APP_DIR/.env and set DB_PASSWORD!"
    read -p "Press enter after editing .env..."
fi

# 7. Django setup
echo "[6/8] Running Django setup..."
cd "$APP_DIR"
"$APP_DIR/venv/bin/python" manage.py migrate
"$APP_DIR/venv/bin/python" manage.py collectstatic --noinput
"$APP_DIR/venv/bin/python" manage.py createsuperuser --noinput 2>/dev/null || echo "Superuser already exists or DJANGO_SUPERUSER_* env vars not set"

# 8. Set permissions
echo "[7/8] Setting permissions..."
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# 9. Setup systemd service
echo "[8/8] Setting up systemd service..."
cp "$APP_DIR/deploy/order.service" /etc/systemd/system/order.service
systemctl daemon-reload
systemctl enable order
systemctl start order

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo "  1. Add nginx config:  cp $APP_DIR/deploy/nginx.conf /etc/nginx/sites-available/order"
echo "     If you already have a server block for dr89.cloud,"
echo "     paste the 'location' blocks inside it instead."
echo "  2. Enable site:       ln -s /etc/nginx/sites-available/order /etc/nginx/sites-enabled/"
echo "  3. Test & reload:     nginx -t && systemctl reload nginx"
echo "  4. Visit:             https://dr89.cloud/order/"
echo ""
echo "Useful commands:"
echo "  systemctl status order     # check service status"
echo "  journalctl -u order -f     # view logs"
echo "  systemctl restart order    # restart after code changes"
