#!/data/data/com.termux/files/usr/bin/bash
# setup.sh - one-command phone setup for Termux. Downloads the agent, prompts for
# config, and launches it (with auto-update) - everything in the Download folder.
#
#   pkg upgrade -y && curl -sL https://raw.githubusercontent.com/au290/rosblok/main/setup.sh | bash
#
# The `pkg upgrade -y` first heals a common broken curl (openssl/QUIC symbol
# mismatch) so the download works. Default mode is 'agent' (VPS mode - polls
# server.py). Type 'master' for a standalone single-phone bot with its own token.
set -e

DIR="/storage/emulated/0/Download"
RAW="https://raw.githubusercontent.com/au290/rosblok/main"

echo "[setup] updating packages (also heals a broken curl/openssl)..."
pkg update -y  >/dev/null 2>&1 || true
pkg upgrade -y >/dev/null 2>&1 || true          # fixes curl 'CANNOT LINK EXECUTABLE' (QUIC symbol)
pkg install -y python tmux lua54 || true
command -v lua >/dev/null || ln -sf "$(command -v lua5.4)" "$PREFIX/bin/lua"   # so `lua` works everywhere

# download via python (always present in Termux) so a broken curl can't block setup
dl() { python -c "import sys,urllib.request; open(sys.argv[2],'wb').write(urllib.request.urlopen(sys.argv[1]).read())" "$1" "$2"; }

echo "[setup] granting shared-storage access (tap Allow if prompted)..."
termux-setup-storage 2>/dev/null || true
mkdir -p "$DIR/cmd"
cd "$DIR"

printf "[setup] mode - 'agent' (VPS) or 'master' (standalone) [agent]: "
read -r MODE </dev/tty; [ -z "$MODE" ] && MODE=agent

if [ "$MODE" = "master" ]; then
    ENTRY=master_bot.py
    dl "$RAW/$ENTRY" "$ENTRY"
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
    dl "$RAW/$ENTRY" "$ENTRY"                     # agent needs no pip deps (stdlib only)
    printf "[setup] VPS URL [https://api.kqing.web.id]: "; read -r VU </dev/tty; [ -z "$VU" ] && VU=https://api.kqing.web.id
    printf "[setup] shared KEY: ";                read -r KY </dev/tty
    printf "[setup] phone label [A]: ";           read -r PH </dev/tty; [ -z "$PH" ] && PH=A
    printf "[setup] hoppers [1,2,3,4,5]: ";        read -r HP </dev/tty; [ -z "$HP" ] && HP=1,2,3,4,5
    { echo "PHONE=$PH"; echo "VPS_URL=$VU"; echo "KEY=$KY"; echo "HOPPERS=$HP"; } > config.txt
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
echo "[setup] STILL MANUAL: drop hopper*.lua + link.txt + servers.txt into $DIR"
echo "        (they're generated on a PC, not in the repo)."
