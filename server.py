"""
server.py — VPS side. ONE Discord bot (single token) + a tiny HTTP job-queue that
the phone agents (agent.py) poll. Lifecycle/control commands enqueue a job for a
target phone and await its result; status/inv commands read the phone's last report.

Run on the VPS:
    pip install -U discord.py aiohttp
    python server.py

Config: edit the CONFIG block, or use token.txt (Discord token) + config.txt
(GUILD_ID / KEY / PHONES / PORT). Phones must send header  X-Key: <KEY>.
"""

import time
import json
import uuid
import shlex
import asyncio
from pathlib import Path

import discord
from discord.ext import commands, tasks
from aiohttp import web

# ─────────────────────────── CONFIG — EDIT THIS ───────────────────────────
TOKEN    = "PASTE_YOUR_BOT_TOKEN_HERE"   # better: put it in token.txt (gitignored)
GUILD_ID = 1257246647830974515           # your server ID → slash cmds appear instantly
KEY      = "CHANGE_ME_SHARED_SECRET"     # shared secret; must match agent.py KEY
HOST     = "0.0.0.0"
PORT     = 8080
PHONES   = ["A", "B"]                    # phone ids that may connect
GRACE    = 20                            # a phone silent longer than this = offline
# ───────────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.resolve()
_tok = BASE_DIR / "token.txt"
if _tok.exists():
    TOKEN = _tok.read_text().strip()

_cfg = BASE_DIR / "config.txt"
if _cfg.exists():
    for _line in _cfg.read_text().splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = (s.strip() for s in _line.split("=", 1))
            if   _k == "GUILD_ID" and _v: GUILD_ID = int(_v)
            elif _k == "KEY"      and _v: KEY = _v
            elif _k == "PORT"     and _v: PORT = int(_v)
            elif _k == "PHONES"   and _v: PHONES = [x.strip() for x in _v.split(",") if x.strip()]

# ─────────────────────────── per-phone state ───────────────────────────
jobs    = {p: [] for p in PHONES}                                    # pending jobs per phone
futures = {}                                                         # job_id -> Future (awaiting result)
reports = {p: {"board": "", "footer": "", "inv": {}, "ts": 0.0} for p in PHONES}


def targets(phone: str) -> list:
    return PHONES if phone == "all" else [phone]


def online(phone: str) -> bool:
    return time.time() - reports.get(phone, {}).get("ts", 0) < GRACE


def enqueue(phone: str, cmd: str):
    jid = uuid.uuid4().hex[:8]
    jobs[phone].append({"id": jid, "cmd": cmd})
    fut = asyncio.get_event_loop().create_future()
    futures[jid] = fut
    return fut


async def run_on(phones: list, cmd: str, timeout: int = 15) -> str:
    """Enqueue cmd on each phone, wait for each result, join them."""
    pending = {}
    for p in phones:
        if p not in PHONES:
            continue
        if not online(p):
            pending[p] = None            # skip enqueue; report offline
        else:
            pending[p] = enqueue(p, cmd)
    out = []
    for p, fut in pending.items():
        if fut is None:
            out.append(f"[{p}] offline")
            continue
        try:
            out.append(f"[{p}] " + (await asyncio.wait_for(fut, timeout)))
        except asyncio.TimeoutError:
            futures.pop_id = getattr(futures, "pop", None)
            out.append(f"[{p}] no response")
    return "\n".join(out) or "(no phones)"


# ─────────────────────────── HTTP endpoint phones poll ───────────────────────────
async def handle_poll(req: web.Request):
    if req.headers.get("X-Key") != KEY:
        return web.json_response({"error": "bad key"}, status=403)
    phone = req.match_info["phone"]
    if phone not in PHONES:
        return web.json_response({"error": "unknown phone"}, status=404)
    body = await req.json()
    rep = reports[phone]
    if body.get("board"):
        rep["board"] = body["board"]
    if "footer" in body:
        rep["footer"] = body.get("footer", rep["footer"])
    if "inv" in body:
        rep["inv"] = body.get("inv", rep["inv"])
    rep["ts"] = time.time()
    for r in body.get("results", []):
        fut = futures.pop(r.get("id"), None)
        if fut and not fut.done():
            fut.set_result(r.get("text", ""))
    out = jobs[phone]
    jobs[phone] = []
    return web.json_response({"jobs": out})


async def start_http():
    app = web.Application()
    app.router.add_post("/api/{phone}/poll", handle_poll)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, HOST, PORT).start()
    print(f"[vps] HTTP job-queue listening on {HOST}:{PORT}")


# ─────────────────────────── Discord bot ───────────────────────────
bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())


def make_embed(phone: str) -> discord.Embed:
    r = reports.get(phone, {})
    up = online(phone)
    e = discord.Embed(title=f"🐎 {phone} · hopper feed",
                      description=r.get("board") or "```\n(no report yet)\n```",
                      color=0x2ecc71 if up else 0x95a5a6)
    e.set_footer(text=(r.get("footer") or "—") + ("" if up else " · ⚠️ offline"))
    e.timestamp = discord.utils.utcnow()
    return e


@bot.event
async def on_ready():
    if GUILD_ID:
        g = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=g)
        await bot.tree.sync(guild=g)
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
    else:
        await bot.tree.sync()
    if not getattr(bot, "_http_up", False):
        bot._http_up = True
        await start_http()
    print(f"logged in as {bot.user}")


async def job_reply(i: discord.Interaction, phone: str, cmd: str, code: bool = False):
    await i.response.defer()
    txt = await run_on(targets(phone), cmd)
    if code:
        txt = f"```\n{txt[-1850:]}\n```"
    await i.followup.send(txt)


# ── lifecycle ──
@bot.tree.command(description="Start one hopper (phone A/B/all)")
async def start(i: discord.Interaction, n: int, phone: str = "all"):
    await job_reply(i, phone, f"start {n}")

@bot.tree.command(description="Stop one hopper")
async def stop(i: discord.Interaction, n: int, phone: str = "all"):
    await job_reply(i, phone, f"stop {n}")

@bot.tree.command(description="Restart one hopper")
async def restart(i: discord.Interaction, n: int, phone: str = "all"):
    await job_reply(i, phone, f"restart {n}")

@bot.tree.command(description="Start all hoppers on a phone")
async def startall(i: discord.Interaction, phone: str = "all"):
    await job_reply(i, phone, "startall")

@bot.tree.command(description="Stop all hoppers + kill the tmux session")
async def stopall(i: discord.Interaction, phone: str = "all"):
    await job_reply(i, phone, "stopall")


# ── status (read the phone's last report — instant) ──
@bot.tree.command(description="One-shot status board (phone A/B/all)")
async def status(i: discord.Interaction, phone: str = "all"):
    await i.response.send_message(embeds=[make_embed(p) for p in targets(phone)][:10])

@bot.tree.command(description="Device RAM / load / disk (last reported)")
async def health(i: discord.Interaction, phone: str = "all"):
    lines = [f"[{p}] {reports.get(p, {}).get('footer') or 'no report yet'}" for p in targets(phone)]
    await i.response.send_message("\n".join(lines))

@bot.tree.command(description="Last N pane lines of a hopper")
async def logs(i: discord.Interaction, n: int, phone: str = "all", lines: int = 15):
    await job_reply(i, phone, f"logs {n} {lines}", code=True)


# ── live feed (edits one message every 5s from cached reports) ──
live_entries = []   # list of (Message, [phones]) — one embed per phone

@tasks.loop(seconds=5)
async def live_loop():
    for entry in list(live_entries):
        msg, phones = entry
        try:
            await msg.edit(embeds=[make_embed(p) for p in phones][:10])
        except discord.NotFound:
            live_entries.remove(entry)
        except Exception:
            pass

@bot.tree.command(description="Live status feed (phone A/B/all), updates every 5s")
async def live(i: discord.Interaction, phone: str = "all"):
    phones = targets(phone)
    await i.response.send_message(embeds=[make_embed(p) for p in phones][:10])
    live_entries.append((await i.original_response(), phones))
    if not live_loop.is_running():
        live_loop.start()


# ── servers / assignment ──
@bot.tree.command(description="Force a hopper to jump to RF<server> now")
async def goto(i: discord.Interaction, n: int, server: int, phone: str = "all"):
    await job_reply(i, phone, f"goto {n} {server}")

@bot.tree.command(description="Assign hopper n to link.txt lines <first>-<last>")
async def assign(i: discord.Interaction, n: int, first: int, last: int, phone: str = "all"):
    await job_reply(i, phone, f"assign {n} {first} {last}")

@bot.tree.command(description="Show every hopper's assignment")
async def assigns(i: discord.Interaction, phone: str = "all"):
    await job_reply(i, phone, "assigns", code=True)

@bot.tree.command(description="Show a hopper's resolved servers")
async def servers(i: discord.Interaction, n: int, phone: str = "all"):
    await job_reply(i, phone, f"servers {n}", code=True)


# ── pool + pin ──
@bot.tree.command(description="Add a PS link to the pool (link.txt)")
async def link_add(i: discord.Interaction, url: str, phone: str = "all"):
    await job_reply(i, phone, f"link_add {shlex.quote(url)}")

@bot.tree.command(description="Send ALL hoppers to a PS link now and hold")
async def all_goto(i: discord.Interaction, url: str, phone: str = "all"):
    await job_reply(i, phone, f"all_goto {shlex.quote(url)}")

@bot.tree.command(name="continue", description="Resume ALL hoppers' rotation")
async def continue_(i: discord.Interaction, phone: str = "all"):
    await job_reply(i, phone, "continue")


# ── inventory (computed from the phone's reported inv blob) ──
def _inv_of(phone: str) -> list:
    """All reported account blobs across the target phone(s) (skip empty/nameless)."""
    out = []
    for p in targets(phone):
        for b in (reports.get(p, {}).get("inv") or {}).values():
            if isinstance(b, dict) and b.get("player") and b.get("player") != "?":
                out.append(b)
    return out

@bot.tree.command(description="Each account's Adopt Me inventory (bucks/pets/eggs)")
async def inv(i: discord.Interaction, phone: str = "all"):
    data = _inv_of(phone)
    if not data:
        return await i.response.send_message(f"[{phone}] no inv reported (monitor writing? phone online?)")
    rows, tb, tp = [], 0, 0
    for d in data:
        s = d.get("stats", {})
        bucks = int(s.get("bucks", d.get("money", 0)) or 0)
        pets  = int(s.get("petCount", 0) or 0)
        eggs  = int(s.get("eggCount", 0) or 0)
        tb += bucks; tp += pets
        rows.append(f"{d.get('player', '?')[:14]:<14} {bucks:>9,}💰 {pets:>3}🐾 {eggs:>2}🥚")
    body = "\n".join(rows) or "(empty)"
    await i.response.send_message(
        f"[{phone}] inventory — {len(rows)} acct · {tb:,}💰 · {tp}🐾 total\n```\n{body[-1800:]}\n```")

@bot.tree.command(description="All pets across every account: count + full-grown, most-owned first")
async def pets(i: discord.Interaction, phone: str = "all"):
    data = _inv_of(phone)
    if not data:
        return await i.response.send_message(f"[{phone}] no inv reported")
    totals = {}   # kind(+neon/mega) -> {count, fg, rarity}
    for d in data:
        for pid, info in ((d.get("pets") or {}).get("by_type") or {}).items():
            if not isinstance(info, dict):
                continue
            t = totals.setdefault(pid, {"count": 0, "fg": 0, "rarity": ""})
            t["count"] += info.get("count", 0)
            t["fg"]    += info.get("fg", 0)
            if info.get("rarity"):
                t["rarity"] = str(info["rarity"])
    if not totals:
        return await i.response.send_message(f"[{phone}] no pets found")
    tot   = sum(v["count"] for v in totals.values())
    totfg = sum(v["fg"] for v in totals.values())
    rows  = [f"{v['count']:>4} {v['fg']:>4}FG  {(v['rarity'] or '?')[:9]:<9} {pid}"
             for pid, v in sorted(totals.items(), key=lambda kv: -kv[1]["count"])]
    body  = "cnt   fg  rarity    pet\n" + "\n".join(rows)
    await i.response.send_message(
        f"[{phone}] pets — {tot} pets ({totfg} full-grown) across {len(data)} acct:\n```\n{body[-1830:]}\n```")


# ── autoexec scripts ──
@bot.tree.command(description="List scripts in the Delta autoexec folder")
async def scripts(i: discord.Interaction, phone: str = "all"):
    await job_reply(i, phone, "scripts", code=True)

@bot.tree.command(description="Show an autoexec script's contents")
async def script_get(i: discord.Interaction, name: str, phone: str = "all"):
    await job_reply(i, phone, f"script_get {shlex.quote(name)}", code=True)

@bot.tree.command(description="Download a script from a URL into autoexec")
async def script_add(i: discord.Interaction, name: str, url: str, phone: str = "all"):
    await job_reply(i, phone, f"script_add {shlex.quote(name)} {shlex.quote(url)}")

@bot.tree.command(description="Delete a script from the autoexec folder")
async def script_del(i: discord.Interaction, name: str, phone: str = "all"):
    await job_reply(i, phone, f"script_del {shlex.quote(name)}")


# ── auto-trade ──
@bot.tree.command(description="util.lua auto-trade: toggle categories OR pass item IDs")
async def autotrade(i: discord.Interaction, phone: str = "all",
                    pets: bool = False, toys: bool = False, food: bool = False,
                    transport: bool = False, gifts: bool = False, stickers: bool = False,
                    pet_accessories: bool = False, items: str = "", usernames: str = ""):
    opts = {"pets": pets, "toys": toys, "food": food, "transport": transport, "gifts": gifts,
            "stickers": stickers, "pet_accessories": pet_accessories, "items": items, "usernames": usernames}
    await job_reply(i, phone, "autotrade " + json.dumps(opts))


# ── meta ──
@bot.tree.command(name="help", description="List every command")
async def help_cmd(i: discord.Interaction):
    guild = discord.Object(id=GUILD_ID) if GUILD_ID else None
    rows = [f"/{c.name} — {c.description}" for c in sorted(bot.tree.get_commands(guild=guild), key=lambda c: c.name)]
    ph = ", ".join(f"{p}{'🟢' if online(p) else '⚪'}" for p in PHONES)
    await i.response.send_message(f"phones: {ph}\ncommands:\n```\n" + "\n".join(rows)[-1800:] + "\n```")


if __name__ == "__main__":
    if TOKEN == "PASTE_YOUR_BOT_TOKEN_HERE":
        raise SystemExit("Edit CONFIG: put your Discord TOKEN in token.txt or the CONFIG block.")
    bot.run(TOKEN)
