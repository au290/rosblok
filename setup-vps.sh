#!/usr/bin/env bash
# setup-vps.sh - install/update the Hopper Fleet web control plane.
# Safe one-line bootstrap:
#   curl -fsSL https://raw.githubusercontent.com/au290/rosblok/main/setup-vps.sh -o /tmp/panen-vps.sh && bash /tmp/panen-vps.sh
set -euo pipefail

RAW="https://raw.githubusercontent.com/au290/rosblok/main"
APP_DIR="${PANEN_DIR:-$HOME/panen}"
CONFIG="$APP_DIR/web/config.txt"

die() { echo "[vps-setup] error: $*" >&2; exit 1; }

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    command -v sudo >/dev/null 2>&1 || die "run as root or install sudo"
    SUDO="sudo"
fi

install_packages() {
    if command -v apt-get >/dev/null 2>&1; then
        $SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -y
        $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y curl python3 python3-venv python3-pip
    elif command -v dnf >/dev/null 2>&1; then
        $SUDO dnf install -y curl python3 python3-pip
    elif command -v yum >/dev/null 2>&1; then
        $SUDO yum install -y curl python3 python3-pip
    elif command -v apk >/dev/null 2>&1; then
        $SUDO apk add --no-cache curl python3 py3-pip
    fi
}

install_packages
command -v curl >/dev/null 2>&1 || die "curl is required"
PYTHON="$(command -v python3 || true)"
[ -n "$PYTHON" ] || die "python3 is required"

mkdir -p "$APP_DIR/web/assets"

fetch() {
    local relative="$1" target="$APP_DIR/$1"
    mkdir -p "$(dirname "$target")"
    curl -fsSL --retry 4 --retry-delay 2 "$RAW/$relative" -o "$target"
}

echo "[vps-setup] downloading web control plane into $APP_DIR"
fetch web/server.py
fetch web/requirements.txt
fetch web/config.example.txt
fetch web/hoppers.example.json
fetch web/assets/index.html
fetch web/assets/app.js
fetch web/assets/styles.css

if [ ! -f "$CONFIG" ]; then
    cp "$APP_DIR/web/config.example.txt" "$CONFIG"
fi

config_get() {
    sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$CONFIG" | head -n 1
}

config_set() {
    local key="$1" value="$2" tmp="$CONFIG.tmp"
    awk -v key="$key" -v value="$value" '
        BEGIN { found = 0 }
        $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
            if (!found) print key "=" value
            found = 1
            next
        }
        { print }
        END { if (!found) print key "=" value }
    ' "$CONFIG" > "$tmp"
    mv "$tmp" "$CONFIG"
}

new_secret() {
    "$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(32))'
}

KEY_VALUE="$(config_get KEY)"
case "$KEY_VALUE" in
    ""|CHANGE_ME_SHARED_SECRET) config_set KEY "$(new_secret)" ;;
esac
TOKEN_VALUE="$(config_get WEB_TOKEN)"
case "$TOKEN_VALUE" in
    ""|CHANGE_ME_WEB_TOKEN) config_set WEB_TOKEN "$(new_secret)" ;;
esac
chmod 600 "$CONFIG"

if ! grep -q '^HOST[[:space:]]*=' "$CONFIG"; then config_set HOST "0.0.0.0"; fi
if ! grep -q '^PORT[[:space:]]*=' "$CONFIG"; then config_set PORT "8090"; fi

echo "[vps-setup] creating Python virtualenv"
"$PYTHON" -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/python" -m pip install --upgrade pip >/dev/null
"$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/web/requirements.txt"

if [ "$(id -u)" -eq 0 ] && [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
    cat > /etc/systemd/system/panen-web.service <<EOF
[Unit]
Description=Hopper Fleet web control plane
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python $APP_DIR/web/server.py
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now panen-web.service
    echo "[vps-setup] service active: panen-web.service"
else
    PID_FILE="$APP_DIR/web/server.pid"
    if [ -f "$PID_FILE" ]; then
        OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
        if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then kill "$OLD_PID" || true; fi
    fi
    nohup "$APP_DIR/.venv/bin/python" "$APP_DIR/web/server.py" \
        >"$APP_DIR/web/server.log" 2>&1 < /dev/null &
    echo $! > "$PID_FILE"
    echo "[vps-setup] server started (pid $(cat "$PID_FILE"))"
fi

echo
echo "[vps-setup] dashboard: http://agent.kqing.web.id/"
echo "[vps-setup] local dashboard: http://127.0.0.1:8090/"
echo "[vps-setup] phone KEY: $(config_get KEY)"
echo "[vps-setup] browser WEB_TOKEN: $(config_get WEB_TOKEN)"
