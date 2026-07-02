#!/data/data/com.termux/files/usr/bin/bash
# autoupdate.sh — keep the bot's code current WITHOUT git, for the manual Download
# layout. Every 60s it re-curls the entry file from raw GitHub; if it changed, it
# overwrites the local copy and restarts the bot. The bot itself just keeps listening.
#
# setup.sh launches this in tmux. Everything lives in the Download folder.
#   watch it:  tmux attach -t farmctl   (detach: Ctrl-b then d)

DIR="/storage/emulated/0/Download"
RAW="https://raw.githubusercontent.com/au290/rosblok/main"
cd "$DIR" || exit 1
termux-wake-lock 2>/dev/null

# entry file: agent.py (VPS mode) wins if present, else master_bot.py (single-phone)
ENTRY="master_bot.py"; [ -f agent.py ] && ENTRY="agent.py"

restart_bot() {
    pkill -f "$ENTRY" 2>/dev/null
    sleep 1
    nohup python "$ENTRY" > bot.log 2>&1 &
    echo "[autoupdate] $(date '+%H:%M:%S') $ENTRY (re)started"
}

restart_bot                                    # launch once at start
while true; do
    if curl -fsSL "$RAW/$ENTRY" -o ".$ENTRY.new" 2>/dev/null && [ -s ".$ENTRY.new" ]; then
        # md5sum < file → hash only (no filename), so first run (no local file) also updates
        if [ "$(md5sum < "$ENTRY" 2>/dev/null)" != "$(md5sum < ".$ENTRY.new")" ]; then
            mv ".$ENTRY.new" "$ENTRY"
            echo "[autoupdate] $(date '+%H:%M:%S') $ENTRY changed — updating"
            restart_bot
        else
            rm -f ".$ENTRY.new"
        fi
    fi
    sleep 60
done
