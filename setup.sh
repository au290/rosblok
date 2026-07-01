#!/data/data/com.termux/files/usr/bin/bash
# setup.sh — one-shot bootstrap for a fresh Termux (e.g. a cloud phone).
#
#   curl -sL https://raw.githubusercontent.com/au290/rosblok/main/setup.sh | bash
#
# Installs deps, clones the repo, asks for the bot token once, and starts the
# bot + auto-updater in tmux. Re-runnable (updates instead of re-cloning).
set -e

echo "[setup] installing packages…"
pkg update -y >/dev/null 2>&1 || true
pkg install -y git python tmux lua

echo "[setup] getting the code…"
cd "$HOME"
if [ -d panen/.git ]; then
    git -C panen fetch -q origin && git -C panen reset --hard -q origin/main
else
    git clone -q https://github.com/au290/rosblok.git panen
fi
cd panen

echo "[setup] python deps…"
pip install -q -U discord.py

if [ ! -f token.txt ]; then
    printf "[setup] paste your Discord bot token: "
    read -r TOK </dev/tty        # /dev/tty so it works even under curl | bash
    printf '%s\n' "$TOK" > token.txt
fi

termux-wake-lock 2>/dev/null || true
tmux kill-session -t farmctl 2>/dev/null || true
tmux new-session -d -s farmctl 'bash ~/panen/autoupdate.sh'

echo
echo "[setup] done — bot + auto-update running in tmux session 'farmctl'."
echo "        watch:   tmux attach -t farmctl      (detach: Ctrl-b then d)"
echo "        then in Discord:  /startall  /status  /live"
