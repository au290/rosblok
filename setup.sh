#!/data/data/com.termux/files/usr/bin/bash
# setup.sh — prep a Termux phone for the MANUAL Download-folder layout.
#
#   curl -sL https://raw.githubusercontent.com/au290/rosblok/main/setup.sh | bash
#
# This does NOT clone the repo. Everything lives in /storage/emulated/0/Download,
# which you manage by hand in the file manager: drop master_bot.py (single-phone) OR
# agent.py (VPS mode) + hopper*.lua + link.txt + servers.txt there yourself.
# setup.sh just installs deps, grants storage, makes the folders, writes config,
# and launches whichever entry file it finds.
set -e

DIR="/storage/emulated/0/Download"

echo "[setup] installing packages…"
pkg update -y >/dev/null 2>&1 || true
pkg install -y python tmux lua54              # Termux's Lua package is lua54 (binary: lua5.4)
command -v lua >/dev/null || ln -sf "$(command -v lua5.4)" "$PREFIX/bin/lua"   # so `lua` works everywhere

echo "[setup] granting shared-storage access (tap Allow if prompted)…"
termux-setup-storage 2>/dev/null || true
mkdir -p "$DIR/cmd"

echo "[setup] python deps…"
pip install -q -U discord.py aiohttp || pip install -q -U discord.py

cd "$DIR"

# token.txt — only master_bot.py (single-phone) needs it; agent.py (VPS mode) has no token.
if [ -f master_bot.py ] && [ ! -f token.txt ]; then
    printf "[setup] Discord bot token: "
    read -r TOK </dev/tty        # /dev/tty so it works even under curl | bash
    printf '%s\n' "$TOK" > token.txt
fi

# config.txt — edit by hand for everything else (HOPPERS, LUA, VPS_URL, KEY, PHONE…).
if [ ! -f config.txt ]; then
    printf "[setup] phone label [A]: "; read -r PH </dev/tty
    [ -z "$PH" ] && PH=A
    if [ -f agent.py ]; then
        printf "[setup] VPS URL (e.g. http://1.2.3.4:8080): "; read -r VU </dev/tty
        printf "[setup] shared KEY: ";                          read -r KY </dev/tty
        { echo "PHONE=$PH"; echo "VPS_URL=$VU"; echo "KEY=$KY"; } > config.txt
    else
        printf "[setup] server (guild) ID: "; read -r GID </dev/tty
        { echo "GUILD_ID=$GID"; echo "PHONE=$PH"; } > config.txt
    fi
fi

# pick the entry file (agent.py wins if both are present)
ENTRY=""
[ -f master_bot.py ] && ENTRY="master_bot.py"
[ -f agent.py ] && ENTRY="agent.py"

if [ -z "$ENTRY" ]; then
    echo
    echo "[setup] deps + folders ready in  $DIR"
    echo "        Now drop master_bot.py (or agent.py) + hopper*.lua + link.txt + servers.txt there,"
    echo "        then re-run this to launch, or:  cd '$DIR' && python master_bot.py"
    exit 0
fi

termux-wake-lock 2>/dev/null || true
tmux kill-session -t farmctl 2>/dev/null || true
if [ -f autoupdate.sh ]; then
    tmux new-session -d -s farmctl "bash '$DIR/autoupdate.sh'"     # auto-pulls $ENTRY + restarts
    LAUNCHED="autoupdate.sh (auto-updates + runs $ENTRY)"
else
    tmux new-session -d -s farmctl "cd '$DIR' && python $ENTRY 2>&1 | tee bot.log"
    LAUNCHED="$ENTRY (no autoupdate.sh — curl it in for auto-updates)"
fi

echo
echo "[setup] launched $LAUNCHED from $DIR in tmux session 'farmctl'."
echo "        watch:   tmux attach -t farmctl      (detach: Ctrl-b then d)"
echo "        config:  edit $DIR/config.txt by hand for HOPPERS/LUA/etc."
