#!/usr/bin/env bash
set -euo pipefail

# ═══ Tradingbot Server Setup Script ═══
# Run as root on a fresh Ubuntu 22.04+ / Debian 12+ server
# Usage: sudo bash deploy/setup.sh

APP_DIR="/opt/tradingbot"
APP_USER="tradingbot"

echo "═══ Tradingbot Setup ═══"

# 1. Create system user
if ! id "$APP_USER" &>/dev/null; then
    useradd -r -m -s /bin/bash "$APP_USER"
    echo "✓ Created user: $APP_USER"
fi

# 2. Install Python 3.12
if ! command -v python3.12 &>/dev/null; then
    apt-get update
    apt-get install -y software-properties-common
    add-apt-repository -y ppa:deadsnakes/ppa
    apt-get install -y python3.12 python3.12-venv python3.12-dev
    echo "✓ Python 3.12 installed"
else
    echo "✓ Python 3.12 already installed"
fi

# 3. Setup app directory
mkdir -p "$APP_DIR"/{data,params,deploy}
cp -r src/ "$APP_DIR/src/"
cp -r params/ "$APP_DIR/params/"
cp pyproject.toml "$APP_DIR/"
cp deploy/tradingbot.service "$APP_DIR/deploy/"

# 4. Create venv & install deps
if [ ! -d "$APP_DIR/.venv" ]; then
    python3.12 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -e "$APP_DIR"
echo "✓ Dependencies installed"

# 5. Setup .env if missing
if [ ! -f "$APP_DIR/.env" ]; then
    if [ -f .env.example ]; then
        cp .env.example "$APP_DIR/.env"
        echo "⚠ Copied .env.example → .env — EDIT WITH REAL VALUES"
    fi
fi

# 6. Permissions
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 "$APP_DIR/.env" 2>/dev/null || true

# 7. Install systemd service
cp deploy/tradingbot.service /etc/systemd/system/tradingbot.service
systemctl daemon-reload
systemctl enable tradingbot
echo "✓ Systemd service installed and enabled"

# 8. Logrotate
if [ -f deploy/logrotate.conf ]; then
    cp deploy/logrotate.conf /etc/logrotate.d/tradingbot
    echo "✓ Logrotate configured"
fi

echo ""
echo "═══ Setup Complete ═══"
echo "Next steps:"
echo "  1. Edit /opt/tradingbot/.env with real API keys"
echo "  2. Run preflight check: /opt/tradingbot/.venv/bin/python scripts/preflight.py"
echo "  3. Start: sudo systemctl start tradingbot"
echo "  4. Logs: journalctl -u tradingbot -f"
echo "  5. Health: curl http://localhost:8080/health"
