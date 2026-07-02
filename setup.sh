#!/data/data/com.termux/files/usr/bin/bash
# setup.sh - one-command phone setup for Termux. Downloads the agent, prompts for
# config, and launches it (with auto-update) - everything in the Download folder.
#
#   curl -sL https://raw.githubusercontent.com/au290/rosblok/main/setup.sh | bash
#
# Default mode is 'agent' (VPS mode - polls server.py). Type 'master' for a
# standalone single-phone bot with its own Discord token.
set -e

DIR="/storage/emulated/0/Download"
RAW="https://raw.githubusercontent.com/au290/rosblok/main"

echo "[setup] installing packages..."
pkg update -y >/dev/null 2>&1 || true
pkg install -y python tmux lua54 curl
command -v lua >/dev/null || ln -sf "$(command -v lua5.4)" "$PREFIX/bin/lua"   # so `lua` works everywhere

echo "[setup] granting shared-storage access (tap Allow if prompted)..."
termux-setup-storage 2>/dev/null || true
mkdir -p "$DIR/cmd"
cd "$DIR"

printf "[setup] mode - 'agent' (VPS) or 'master' (standalone) [agent]: "
read -r MODE </dev/tty; [ -z "$MODE" ] && MODE=agent

if [ "$MODE" = "master" ]; then
    ENTRY=master_bot.py
    curl -fsSL "$RAW/$ENTRY" -o "$ENTRY"
    echo "[setup] python deps..."; pip install -q -U discord.py
    if [ ! -f token.txt ]; then
        printf "[setup] Discord bot token: "; read -r T </dev/tty
        printf '%s\n' "$T" > token.txt
    fi
    printf "[setup] server (guild) ID: ";     read -r GID </dev/tty
    printf "[setup] phone label [A]: ";       read -r PH  </dev/tty; [ -z "$PH" ] && PH=A
    printf "[setup] hoppers [1,2,3,4,5]: ";    read -r HP  </dev/tty; [ -z "$HP" ] && HP=1,2,3,4,5
    { echo "GUILD_ID=$GID"; echo "PHONE=$PH"; echo "HOPPERS=$HP"; } > config.txt
else
    ENTRY=agent.py
    curl -fsSL "$RAW/$ENTRY" -o "$ENTRY"          # agent needs no pip deps (stdlib only)
    printf "[setup] VPS URL [https://api.kqing.web.id]: "; read -r VU </dev/tty; [ -z "$VU" ] && VU=https://api.kqing.web.id
    printf "[setup] shared KEY: ";                read -r KY </dev/tty
    printf "[setup] phone label [A]: ";           read -r PH </dev/tty; [ -z "$PH" ] && PH=A
    printf "[setup] hoppers [1,2,3,4,5]: ";        read -r HP </dev/tty; [ -z "$HP" ] && HP=1,2,3,4,5
    { echo "PHONE=$PH"; echo "VPS_URL=$VU"; echo "KEY=$KY"; echo "HOPPERS=$HP"; } > config.txt
fi

curl -fsSL "$RAW/autoupdate.sh" -o autoupdate.sh   # keeps $ENTRY current + restarts it

termux-wake-lock 2>/dev/null || true
tmux kill-session -t farmctl 2>/dev/null || true
tmux new-session -d -s farmctl "bash '$DIR/autoupdate.sh'"

echo
echo "[setup] $ENTRY running (auto-updating) in tmux session 'farmctl'."
echo "        watch:   tmux attach -t farmctl      (detach: Ctrl-b then d)"
echo "        config:  edit $DIR/config.txt by hand to tweak later"
echo
echo "[setup] STILL MANUAL: drop hopper*.lua + link.txt + servers.txt into $DIR"
echo "        (they're generated on a PC, not in the repo)."
