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

if [ -f config.txt ]; then                        # re-run (e.g. after reboot): infer mode, no prompts
    grep -q '^VPS_URL=' config.txt && MODE=agent || MODE=master
    echo "[setup] existing config.txt found -> $MODE mode (no prompts)"
else
    printf "[setup] mode - 'agent' (VPS) or 'master' (standalone) [agent]: "
    read -r MODE </dev/tty; [ -z "$MODE" ] && MODE=agent
fi

if [ "$MODE" = "master" ]; then
    ENTRY=master_bot.py
    dl "$RAW/$ENTRY" "$ENTRY"
    echo "[setup] python deps..."; pip install -q -U discord.py
    if [ ! -f token.txt ]; then
        printf "[setup] Discord bot token: "; read -r T </dev/tty
        printf '%s\n' "$T" > token.txt
    fi
    if [ ! -f config.txt ]; then                  # keep existing config on re-run (e.g. after reboot)
        printf "[setup] server (guild) ID: ";     read -r GID </dev/tty
        printf "[setup] phone label [A]: ";       read -r PH  </dev/tty; [ -z "$PH" ] && PH=A
        printf "[setup] hoppers [1,2,3,4,5]: ";    read -r HP  </dev/tty; [ -z "$HP" ] && HP=1,2,3,4,5
        { echo "GUILD_ID=$GID"; echo "PHONE=$PH"; echo "HOPPERS=$HP"; } > config.txt
    fi
else
    ENTRY=agent.py
    dl "$RAW/$ENTRY" "$ENTRY"                     # agent needs no pip deps (stdlib only)
    if [ ! -f config.txt ]; then                  # keep existing config on re-run (e.g. after reboot)
        printf "[setup] VPS URL [https://api.kqing.web.id]: "; read -r VU </dev/tty; [ -z "$VU" ] && VU=https://api.kqing.web.id
        printf "[setup] shared KEY: ";                read -r KY </dev/tty
        printf "[setup] phone label [A]: ";           read -r PH </dev/tty; [ -z "$PH" ] && PH=A
        printf "[setup] hoppers [1,2,3,4,5]: ";        read -r HP </dev/tty; [ -z "$HP" ] && HP=1,2,3,4,5
        { echo "PHONE=$PH"; echo "VPS_URL=$VU"; echo "KEY=$KY"; echo "HOPPERS=$HP"; } > config.txt
    fi
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
