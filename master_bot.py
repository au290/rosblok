"""
master_bot.py — Discord control for the tmux hoppers (1-5), runs ON the phone.

Setup (per phone, in Termux):
    pkg install python tmux
    pip install -U discord.py
    python master_bot.py

You only edit the CONFIG block below. No env vars.
For 2 phones: copy this file to each phone and change TOKEN + PHONE on phone B.
"""

import re
import json
import discord
from discord.ext import commands, tasks
from pathlib import Path
import subprocess
import asyncio
import urllib.request

# ─────────────────────────── CONFIG — EDIT THIS ───────────────────────────
TOKEN    = "PASTE_YOUR_BOT_TOKEN_HERE"   # better: leave this, put the token in token.txt (gitignored)
GUILD_ID = 1257246647830974515                             # your server ID (Copy Server ID) → slash cmds appear instantly. 0 = global (slow)
PHONE    = "A"                           # label so you can tell phone A from phone B in replies
LUA      = "lua"                         # change to "lua5.4" if `which lua` shows that
SESSION  = "farm"                        # tmux session name
HOPPERS  = [1, 2, 3, 4, 5]               # this phone's hoppers
# ───────────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.resolve()
LUA_CMDS = {"lua", "lua5.4", "lua5.3", "luajit"}

# token.txt (gitignored) wins over the inline TOKEN — so master_bot.py is safe to push
_tok = BASE_DIR / "token.txt"
if _tok.exists():
    TOKEN = _tok.read_text().strip()

# config.txt (gitignored, written by setup.sh) overrides CONFIG — survives auto-update's reset --hard
_cfg = BASE_DIR / "config.txt"
if _cfg.exists():
    for _line in _cfg.read_text().splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = (s.strip() for s in _line.split("=", 1))
            if   _k == "GUILD_ID" and _v: GUILD_ID = int(_v)
            elif _k == "PHONE"    and _v: PHONE = _v
            elif _k == "LUA"      and _v: LUA = _v
            elif _k == "HOPPERS"  and _v: HOPPERS = [int(x) for x in _v.split(",") if x.strip()]


def tmux(*args):
    return subprocess.run(["tmux", *args], cwd=str(BASE_DIR), capture_output=True, text=True, errors="replace")


def session_exists() -> bool:
    return tmux("has-session", "-t", SESSION).returncode == 0


def windows() -> set:
    if not session_exists():
        return set()
    return set(tmux("list-windows", "-t", SESSION, "-F", "#{window_name}").stdout.split())


def tgt(n: int) -> str:
    return f"{SESSION}:h{n}"


def is_running(n: int) -> bool:
    if f"h{n}" not in windows():
        return False
    r = tmux("display-message", "-p", "-t", tgt(n), "#{pane_current_command}")
    return r.stdout.strip() in LUA_CMDS


def ensure_window(n: int):
    if not session_exists():
        tmux("new-session", "-d", "-s", SESSION, "-n", f"h{n}")
    elif f"h{n}" not in windows():
        tmux("new-window", "-d", "-t", SESSION, "-n", f"h{n}")


def start_hopper(n: int) -> str:
    if is_running(n):
        return f"hopper{n} already running"
    if not (BASE_DIR / f"hopper{n}.lua").exists():
        return f"hopper{n}.lua not found"
    ensure_window(n)
    tmux("send-keys", "-t", tgt(n), f"cd '{BASE_DIR}' && {LUA} hopper{n}.lua", "Enter")
    return f"started hopper{n}"


def stop_hopper(n: int) -> str:
    if f"h{n}" not in windows():
        return f"hopper{n} not started"
    tmux("send-keys", "-t", tgt(n), "C-c")   # Ctrl-C the lua; the tmux window/shell stays alive
    return f"stopped hopper{n}"


def pane_tail(n: int, lines: int = 1) -> str:
    if f"h{n}" not in windows():
        return "—"
    rows = [l for l in tmux("capture-pane", "-p", "-t", tgt(n)).stdout.splitlines() if l.strip()]
    return "\n".join(rows[-lines:]) if rows else "—"


# link.txt / servers.txt live in the phone's Download folder (change if you keep them elsewhere)
DATA_DIR  = Path("/storage/emulated/0/Download")
MAP_FILE  = DATA_DIR / "servers.txt"
POOL_FILE = DATA_DIR / "link.txt"
# Folder the in-game monitor writefile()s Adopt Me dumps into.
# Point this at your executor's workspace, e.g. Path("/storage/emulated/0/Delta/workspace/inv")
INV_DIR   = BASE_DIR / "inv"
# Delta executor autoexec folder — scripts placed here run on injection. Edit if yours differs.
AUTOEXEC  = Path("/storage/emulated/0/Delta/autoexec")


def pool() -> list:
    if not POOL_FILE.exists():
        return []
    return [l for l in POOL_FILE.read_text().splitlines() if l.strip() and not l.startswith("#")]


def ranges() -> dict:
    d = {}
    if MAP_FILE.exists():
        for line in MAP_FILE.read_text().splitlines():
            m = re.match(r"\s*(\d+)\s*:\s*(\d+)\s*-\s*(\d+)", line)
            if m:
                d[int(m.group(1))] = (int(m.group(2)), int(m.group(3)))
    return d


def set_range(n: int, first: int, last: int):
    d = ranges()
    d[n] = (first, last)
    body = ["# hopper : firstLink-lastLink  (line numbers in link.txt, 1-based)"]
    body += [f"{k}: {d[k][0]}-{d[k][1]}" for k in sorted(d)]
    MAP_FILE.write_text("\n".join(body) + "\n")


def hopper_links(n: int) -> list:
    first, last = ranges().get(n, (1, 0))
    return pool()[first - 1:last]


def write_cmd(n: int, c: str):
    d = BASE_DIR / "cmd"
    d.mkdir(exist_ok=True)
    (d / f"h{n}.txt").write_text(c)


def clear_cmd(n: int):
    f = BASE_DIR / "cmd" / f"h{n}.txt"
    if f.exists():
        f.unlink()


def _script(name: str) -> Path:
    return AUTOEXEC / Path(name).name        # strip path parts — no escaping the autoexec folder


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "hopperbot"})
    return urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace")


def _lua_list(csv: str) -> str:
    xs = [x.strip() for x in csv.split(",") if x.strip()]
    return "{" + ", ".join(f'"{x}"' for x in xs) + "}"


SRV_RE  = re.compile(r"RF\d+")
PROG_RE = re.compile(r"(\d+)s\s*/\s*(\d+)s")


def _parse(n: int):
    """Pull (server, elapsed, total) out of the hopper's last pane line."""
    line = pane_tail(n)
    s, p = SRV_RE.search(line), PROG_RE.search(line)
    return (s.group() if s else None,
            int(p.group(1)) if p else 0,
            int(p.group(2)) if p else 0)


def _bar(el: int, tot: int, w: int = 10) -> str:
    f = min(w, int(w * el / tot)) if tot else 0
    return "█" * f + "░" * (w - f)


def device_health() -> str:
    def sh(c):
        return subprocess.run(c, shell=True, capture_output=True, text=True, errors="replace").stdout
    m    = re.search(r"Mem:\s+(\d+)\s+\d+\s+(\d+)", sh("free -m"))          # total, free (MB)
    load = re.search(r"[\d.]+", sh("cat /proc/loadavg"))                    # 1-min load avg
    disk = re.search(r"\s(\d+)\s+\d+%\s", sh("df /data 2>/dev/null | tail -1"))  # avail KB
    ram  = f"{m.group(2)}/{m.group(1)}MB" if m else "?"
    cpu  = load.group() if load else "?"
    gb   = f"{int(disk.group(1)) / 1048576:.1f}G" if disk else "?"
    return f"🧠 {ram} free · ⚙️ load {cpu} · 💾 {gb} free"


def render() -> discord.Embed:
    rows, up = [], 0
    for n in HOPPERS:
        if not is_running(n):
            rows.append(f"{n:>2}  {'—':<5} stopped")
            continue
        if "PINNED" in pane_tail(n):
            up += 1
            rows.append(f"{n:>2}  📌    held")
            continue
        srv, el, tot = _parse(n)
        up += 1
        prog = f"{_bar(el, tot)} {el:>3}/{tot}s" if tot else "starting…"
        rows.append(f"{n:>2}  {srv or '??':<5} {prog}")
    body = "```\n #  srv   progress\n" + "\n".join(rows) + "\n```"
    e = discord.Embed(title=f"🐎 {PHONE} · hopper feed", description=body,
                      color=0x2ecc71 if up else 0x95a5a6)
    e.set_footer(text=f"{device_health()} · {up}/{len(HOPPERS)} running")
    e.timestamp = discord.utils.utcnow()
    return e


live_msg = None


@tasks.loop(seconds=5)
async def live_updater():
    global live_msg
    if not live_msg:
        return
    try:
        await live_msg.edit(embed=render())
    except discord.NotFound:
        live_msg = None      # message was deleted — stop editing a ghost
    except Exception:
        pass                 # any render/subprocess/HTTP hiccup: skip this tick, keep the loop alive


bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())


@bot.event
async def on_ready():
    if GUILD_ID:
        g = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=g)
        await bot.tree.sync(guild=g)        # register (fast) on your server
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()               # wipe the old GLOBAL copies (kills duplicates)
    else:
        await bot.tree.sync()
    print(f"[{PHONE}] logged in as {bot.user}")


@bot.tree.command(description="Start one hopper (1-5)")
async def start(i: discord.Interaction, n: int):
    await i.response.send_message(f"[{PHONE}] {start_hopper(n)}")


@bot.tree.command(description="Stop one hopper (1-5)")
async def stop(i: discord.Interaction, n: int):
    await i.response.send_message(f"[{PHONE}] {stop_hopper(n)}")


@bot.tree.command(description="Restart one hopper (1-5)")
async def restart(i: discord.Interaction, n: int):
    stop_hopper(n)
    await i.response.send_message(f"[{PHONE}] {start_hopper(n)}")


@bot.tree.command(description="Start all hoppers on this phone")
async def startall(i: discord.Interaction):
    await i.response.send_message(f"[{PHONE}]\n" + "\n".join(start_hopper(n) for n in HOPPERS))


@bot.tree.command(description="Stop all hoppers + kill the tmux session")
async def stopall(i: discord.Interaction):
    tmux("kill-session", "-t", SESSION)
    await i.response.send_message(f"[{PHONE}] killed session '{SESSION}'")


@bot.tree.command(description="One-shot pretty status of all hoppers")
async def status(i: discord.Interaction):
    await i.response.send_message(embed=render())


@bot.tree.command(description="Device RAM / CPU load / free disk")
async def health(i: discord.Interaction):
    await i.response.send_message(f"[{PHONE}] {device_health()}")


@bot.tree.command(description="Show each account's Adopt Me inventory (bucks / pets / eggs)")
async def inv(i: discord.Interaction):
    files = sorted(INV_DIR.glob("*.json")) if INV_DIR.exists() else []
    if not files:
        return await i.response.send_message(f"[{PHONE}] no dumps in `{INV_DIR}` — is the monitor writing there?")
    rows, tot_b, tot_p = [], 0, 0
    for f in files:
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        s = d.get("stats", {})
        bucks = int(s.get("bucks", d.get("money", 0)) or 0)
        pets  = int(s.get("petCount", 0) or 0)
        eggs  = int(s.get("eggCount", 0) or 0)
        tot_b += bucks; tot_p += pets
        rows.append(f"{d.get('player', f.stem)[:14]:<14} {bucks:>9,}💰 {pets:>3}🐾 {eggs:>2}🥚")
    body = "\n".join(rows) or "(empty)"
    await i.response.send_message(
        f"[{PHONE}] inventory — {len(rows)} acct · {tot_b:,}💰 · {tot_p}🐾 total\n```\n{body[-1800:]}\n```")


@bot.tree.command(description="List scripts in the Delta autoexec folder")
async def scripts(i: discord.Interaction):
    fs = [f for f in sorted(AUTOEXEC.glob("*")) if f.is_file()] if AUTOEXEC.exists() else []
    body = "\n".join(f"{f.name}  {f.stat().st_size}b" for f in fs) or "(empty)"
    await i.response.send_message(f"[{PHONE}] autoexec `{AUTOEXEC}`:\n```\n{body[-1800:]}\n```")


@bot.tree.command(description="Show an autoexec script's contents")
async def script_get(i: discord.Interaction, name: str):
    f = _script(name)
    if not f.exists():
        return await i.response.send_message(f"[{PHONE}] {f.name} not found")
    await i.response.send_message(f"[{PHONE}] {f.name}:\n```lua\n{f.read_text(errors='replace')[-1800:]}\n```")


@bot.tree.command(description="Download a script from a URL into autoexec (create or overwrite)")
async def script_add(i: discord.Interaction, name: str, url: str):
    await i.response.defer()
    try:
        AUTOEXEC.mkdir(parents=True, exist_ok=True)
        data = await asyncio.to_thread(_fetch, url)
        _script(name).write_text(data)
        await i.followup.send(f"[{PHONE}] saved `{_script(name).name}` ({len(data)}b) to autoexec")
    except Exception as e:
        await i.followup.send(f"[{PHONE}] curl failed: {e}")


@bot.tree.command(description="Delete a script from the autoexec folder")
async def script_del(i: discord.Interaction, name: str):
    f = _script(name)
    if not f.exists():
        return await i.response.send_message(f"[{PHONE}] {f.name} not found")
    f.unlink()
    await i.response.send_message(f"[{PHONE}] deleted `{f.name}`")


@bot.tree.command(description="util.lua: enable auto-trade. Toggle categories OR pass item IDs. Usernames optional.")
async def autotrade(i: discord.Interaction,
                    pets: bool = False, toys: bool = False, food: bool = False,
                    transport: bool = False, gifts: bool = False, stickers: bool = False,
                    pet_accessories: bool = False, items: str = "", usernames: str = ""):
    chosen = [c for c, on in [("pets", pets), ("toys", toys), ("food", food),
                              ("transport", transport), ("gifts", gifts),
                              ("stickers", stickers), ("pet_accessories", pet_accessories)] if on]
    if not chosen and not items.strip():
        return await i.response.send_message(f"[{PHONE}] pick a category or pass items")
    f = AUTOEXEC / "util.lua"
    if not f.exists():
        return await i.response.send_message(f"[{PHONE}] util.lua not found in `{AUTOEXEC}`")
    t = f.read_text(errors="replace")
    # anchor on AutoTrade-only comments so AutoOpen/Shop's identical fields are untouched
    t = re.sub(r'(Enabled\s*=\s*)(?:true|false)(,\s*--\s*Start auto trading on load)',
               lambda m: m.group(1) + "true" + m.group(2), t)
    if chosen:
        t = re.sub(r'(Categories\s*=\s*)\{[^}]*\}', lambda m: m.group(1) + _lua_list(",".join(chosen)), t)
    if items.strip():
        t = re.sub(r'(TradeMode\s*=\s*)"[^"]*"', lambda m: m.group(1) + '"specific"', t)
        t = re.sub(r'(Items\s*=\s*)\{[^}]*\}(,\s*--\s*Item IDs/names to send)',
                   lambda m: m.group(1) + _lua_list(items) + m.group(2), t)
        what = f"items {_lua_list(items)}"
    else:
        t = re.sub(r'(TradeMode\s*=\s*)"[^"]*"', lambda m: m.group(1) + '"all"', t)
        what = f"categories {_lua_list(','.join(chosen))}"
    if usernames.strip():
        t = re.sub(r'(Usernames\s*=\s*)\{[^}]*\}', lambda m: m.group(1) + _lua_list(usernames), t)
        who = f"to {_lua_list(usernames)}"
    else:
        who = "usernames as-is"
    f.write_text(t)
    await i.response.send_message(f"[{PHONE}] auto-trade ON → {what}, {who}")


@bot.tree.command(description="Live status feed — edits one message every 5s")
async def live(i: discord.Interaction):
    global live_msg
    await i.response.send_message(embed=render())
    live_msg = await i.original_response()
    if not live_updater.is_running():
        live_updater.start()


@bot.tree.command(description="Force a hopper to jump to server RF<server> now")
async def goto(i: discord.Interaction, n: int, server: int):
    write_cmd(n, f"goto{server}")
    await i.response.send_message(f"[{PHONE}] hopper{n} → RF{server}")


@bot.tree.command(description="Add a PS link to the pool (link.txt)")
async def link_add(i: discord.Interaction, url: str):
    with open(POOL_FILE, "a", encoding="utf-8") as f:
        f.write(url.strip() + "\n")
    idx = len(pool())
    await i.response.send_message(f"[{PHONE}] added link #{idx} → `/all_goto {idx}` sends everyone there")


@bot.tree.command(description="Send ALL hoppers to a PS link now and hold (temporary, not saved)")
async def all_goto(i: discord.Interaction, url: str):
    for n in HOPPERS:
        write_cmd(n, f"pin {url.strip()}")
    await i.response.send_message(f"[{PHONE}] all hoppers → that link, holding. `/continue` to resume (link not saved)")


@bot.tree.command(name="continue", description="Resume ALL hoppers' normal rotation")
async def continue_(i: discord.Interaction):
    for n in HOPPERS:
        clear_cmd(n)
    await i.response.send_message(f"[{PHONE}] all hoppers resuming rotation")


@bot.tree.command(description="Assign hopper n to link.txt lines <first>-<last>")
async def assign(i: discord.Interaction, n: int, first: int, last: int):
    set_range(n, first, last)
    await i.response.send_message(f"[{PHONE}] hopper{n} = links {first}-{last} ({len(hopper_links(n))} servers)")


@bot.tree.command(description="Show every hopper's assignment")
async def assigns(i: discord.Interaction):
    d = ranges()
    body = "\n".join(f"hopper{k}: {d[k][0]}-{d[k][1]}" for k in sorted(d)) or "(none)"
    await i.response.send_message(f"[{PHONE}] assignments:\n```\n{body}\n```")


@bot.tree.command(description="Show a hopper's resolved servers")
async def servers(i: discord.Interaction, n: int):
    lst = hopper_links(n)
    body = "\n".join(f"RF{j}: {l}" for j, l in enumerate(lst, 1)) or "(empty)"
    await i.response.send_message(f"[{PHONE}] hopper{n}:\n```\n{body[-1850:]}\n```")


@bot.tree.command(description="Last N pane lines of a hopper")
async def logs(i: discord.Interaction, n: int, lines: int = 15):
    await i.response.send_message(f"[{PHONE}] hopper{n}:\n```\n{pane_tail(n, lines)[-1800:]}\n```")


@bot.tree.command(name="help", description="List every command")
async def help_cmd(i: discord.Interaction):
    rows = [f"/{c.name} — {c.description}" for c in sorted(bot.tree.get_commands(), key=lambda c: c.name)]
    await i.response.send_message(f"[{PHONE}] commands:\n```\n" + "\n".join(rows)[-1900:] + "\n```")


if __name__ == "__main__":
    if TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        raise SystemExit("Edit the CONFIG block: paste your bot TOKEN first.")
    bot.run(TOKEN)
