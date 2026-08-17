#!/data/data/com.termux/files/usr/bin/bash
# setup.sh - one-command phone setup for Termux. Downloads the agent, prompts for
# config, and launches it (with auto-update) - everything in the Download folder.
#
# SAFE bootstrap (download to a file, confirm it's the script, THEN run) - use this
# on cloud phones, whose shared IPs hit GitHub's 429 rate limit a lot:
#   pkg upgrade -y && curl -sL https://raw.githubusercontent.com/au290/rosblok/main/setup.sh -o setup.sh && head -1 setup.sh | grep -q '^#!' && bash setup.sh || echo "bad download (GitHub 429?) - wait a few minutes and retry"
#
# The quick `curl -sL ... | bash` also works but pipes a 429 error page straight into
# bash if you're rate-limited (you'll see '429: command not found'); prefer the above.
# `pkg upgrade -y` first heals a common broken curl (openssl/QUIC symbol mismatch).
# Default mode is 'agent' (VPS mode - polls server.py). Type 'master' for a standalone
# single-phone bot with its own token.
set -e

DIR="/storage/emulated/0/Download"
RAW="https://raw.githubusercontent.com/au290/rosblok/main"

echo "[setup] updating packages (also heals a broken curl/openssl)..."
pkg update -y  >/dev/null 2>&1 || true
pkg upgrade -y >/dev/null 2>&1 || true          # fixes curl 'CANNOT LINK EXECUTABLE' (QUIC symbol)
pkg install -y python tmux lua54 || true
command -v lua >/dev/null || ln -sf "$(command -v lua5.4)" "$PREFIX/bin/lua"   # so `lua` works everywhere

# download via python (always present in Termux) so a broken curl can't block setup.
# Retries with backoff on GitHub's 429 (cloud phones share IPs that get rate-limited a
# lot), sends a browser UA, and only writes the file once the whole body is in hand — so
# a 429/abuse page never lands on disk as if it were code.
dl() {
    python - "$1" "$2" <<'PY'
import sys, time, urllib.request
url, out = sys.argv[1], sys.argv[2]
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (hopperbot setup)"})
for i in range(6):
    try:
        data = urllib.request.urlopen(req, timeout=30).read()
        open(out, "wb").write(data)
        sys.exit(0)
    except Exception as e:
        sys.stderr.write("[dl] %s attempt %d failed: %s\n" % (url, i + 1, e))
        time.sleep(min(60, 10 * (i + 1)))          # back off on 429 / abuse throttling
sys.stderr.write("[dl] gave up on %s (GitHub 429? wait a few minutes)\n" % url)
sys.exit(1)
PY
}

echo "[setup] granting shared-storage access (tap Allow if prompted)..."
termux-setup-storage 2>/dev/null || true
mkdir -p "$DIR/cmd"
cd "$DIR"

# Read and update individual config values without replacing the rest of the file.
config_get() {
    [ -f config.txt ] || return 0
    sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" config.txt | head -n 1
}

config_set() {
    local key="$1" value="$2" tmp=".config.txt.tmp"
    awk -v key="$key" -v value="$value" '
        BEGIN { found = 0 }
        $0 ~ "^[[:space:]]*" key "[[:space:]]*=" {
            if (!found) print key "=" value
            found = 1
            next
        }
        { print }
        END { if (!found) print key "=" value }
    ' config.txt > "$tmp"
    mv "$tmp" config.txt
}

ensure_config() {
    local key="$1" label="$2" default="$3" required="$4" value
    value="$(config_get "$key")"
    [ -n "$(printf '%s' "$value" | tr -d '[:space:]')" ] && return 0
    while true; do
        if [ -n "$default" ]; then
            printf "[setup] %s [%s]: " "$label" "$default"
        else
            printf "[setup] %s: " "$label"
        fi
        read -r value </dev/tty
        [ -z "$value" ] && value="$default"
        if [ -n "$value" ] || [ "$required" != "required" ]; then
            config_set "$key" "$value"
            return 0
        fi
        echo "[setup] $label cannot be empty."
    done
}

default_config() {
    local key="$1" value="$2"
    [ -n "$(config_get "$key" | tr -d '[:space:]')" ] || config_set "$key" "$value"
}

if [ -f config.txt ]; then                        # re-run: infer mode and repair only missing values
    if grep -q '^[[:space:]]*VPS_URL[[:space:]]*=' config.txt; then
        MODE=agent
    elif grep -q '^[[:space:]]*GUILD_ID[[:space:]]*=' config.txt; then
        MODE=master
    elif [ -f agent.py ]; then
        MODE=agent
    elif [ -f master_bot.py ]; then
        MODE=master
    else
        MODE=agent
    fi
    echo "[setup] existing config.txt found -> $MODE mode; keeping existing values"
else
    : > config.txt
    printf "[setup] mode - 'agent' (VPS) or 'master' (standalone) [agent]: "
    read -r MODE </dev/tty; [ -z "$MODE" ] && MODE=agent
fi

if [ "$MODE" = "master" ]; then
    ENTRY=master_bot.py
    dl "$RAW/$ENTRY" "$ENTRY"
    echo "[setup] python deps..."; pip install -q -U discord.py
    if [ ! -s token.txt ]; then
        while true; do
            printf "[setup] Discord bot token: "; read -r T </dev/tty
            [ -n "$T" ] && break
            echo "[setup] Discord bot token cannot be empty."
        done
        printf '%s\n' "$T" > token.txt
    fi
    ensure_config GUILD_ID "server (guild) ID" "" required
    ensure_config PHONE "phone label" "A" required
    ensure_config HOPPERS "hoppers" "1,2,3,4,5" required
else
    ENTRY=agent.py
    dl "$RAW/$ENTRY" "$ENTRY"                     # agent needs no pip deps (stdlib only)
    # Move installs that still use the retired public default to the current endpoint.
    if [ "$(config_get VPS_URL)" = "https://api.kqing.web.id" ]; then
        config_set VPS_URL "http://agent.kqing.web.id"
    fi
    ensure_config VPS_URL "VPS URL" "http://agent.kqing.web.id" required
    ensure_config KEY "shared KEY" "" required
    ensure_config PHONE "phone label" "A" required
    ensure_config HOPPERS "hoppers" "1,2,3,4,5" required
    default_config PLACE_ID "920587237"
    default_config WINDOW_MODE "auto"
    default_config START_ON_BOOT "false"
    default_config AUTO_DETECT_PACKAGES "true"
    default_config TRADE_STALE_SECONDS "40"
fi

dl "$RAW/autoupdate.sh" autoupdate.sh              # keeps $ENTRY current + restarts it

termux-wake-lock 2>/dev/null || true
tmux kill-session -t farmctl 2>/dev/null || true
tmux new-session -d -s farmctl "bash '$DIR/autoupdate.sh'"

echo
echo "[setup] $ENTRY running (auto-updating) in tmux session 'farmctl'."
echo "        watch:   tmux attach -t farmctl      (detach: Ctrl-b then d)"
echo "        config:  edit $DIR/config.txt by hand to tweak later"
echo
if [ "$MODE" = "master" ]; then
    echo "[setup] STILL MANUAL: drop hopper*.lua + link.txt + servers.txt into $DIR"
    echo "        (they're generated on a PC, not in the repo)."
else
    echo "[setup] agent.py controls Roblox packages directly; hopper*.lua is not required."
    echo "        Configure rotations in the web dashboard or provide link.txt + servers.txt."
fi
