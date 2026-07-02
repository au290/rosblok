#!/usr/bin/env bash
# vps_update.sh — keep server.py current from raw GitHub, no git needed (mirrors the
# phone's autoupdate.sh). Run it from the folder that holds server.py / token.txt /
# config.txt. Every 60s it re-curls server.py; if it changed, overwrites + restarts.
#
#   nohup bash vps_update.sh > update.log 2>&1 &     # or run in tmux/screen/systemd
# Watch:  tail -f update.log

cd "$(dirname "$0")" || exit 1                 # run wherever server.py actually lives
RAW="https://raw.githubusercontent.com/au290/rosblok/main"
ENTRY="server.py"
PY="$(command -v python3 || command -v python)"

restart_bot() {
    pkill -f "$ENTRY" 2>/dev/null
    sleep 1
    nohup "$PY" "$ENTRY" > bot.log 2>&1 &
    echo "[vps_update] $(date '+%H:%M:%S') $ENTRY (re)started"
}

restart_bot                                    # launch once at start
while true; do
    if curl -fsSL "$RAW/$ENTRY" -o ".$ENTRY.new" 2>/dev/null && [ -s ".$ENTRY.new" ]; then
        # md5sum < file → hash only (no filename), so first run (no local file) also updates
        if [ "$(md5sum < "$ENTRY" 2>/dev/null)" != "$(md5sum < ".$ENTRY.new")" ]; then
            mv ".$ENTRY.new" "$ENTRY"
            echo "[vps_update] $(date '+%H:%M:%S') $ENTRY changed — updating"
            restart_bot
        else
            rm -f ".$ENTRY.new"
        fi
    fi
    sleep 60
done
