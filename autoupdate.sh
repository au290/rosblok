#!/data/data/com.termux/files/usr/bin/bash
# autoupdate.sh — poll the git repo; on every new commit, pull and restart the bot.
#
# One-time on the phone:
#   tmux new-window -n update 'bash ~/panen/autoupdate.sh'
# (or add to Termux:Boot so it survives reboots — see notes)

cd "$(dirname "$0")" || exit 1
termux-wake-lock 2>/dev/null        # keep Android from killing Termux

BRANCH=main                          # <-- set to the branch you push from

restart_bot() {
    pkill -f master_bot.py 2>/dev/null
    sleep 1
    nohup python master_bot.py > bot.log 2>&1 &
    echo "[autoupdate] $(date '+%H:%M:%S') bot (re)started"
}

restart_bot                          # launch once at start
while true; do
    git fetch -q origin "$BRANCH" 2>/dev/null
    if [ "$(git rev-parse HEAD)" != "$(git rev-parse "origin/$BRANCH" 2>/dev/null)" ]; then
        echo "[autoupdate] $(date '+%H:%M:%S') new commit — updating"
        git reset --hard "origin/$BRANCH" -q   # match repo exactly; leaves gitignored files alone
        restart_bot
    fi
    sleep 60
done
