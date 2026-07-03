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

import re
import math
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
reports = {p: {"board": "", "footer": "", "inv": {}, "servers": 0, "srv_now": [], "prices": {}, "rarities": {}, "ts": 0.0} for p in PHONES}


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
    if "servers" in body:
        rep["servers"] = body.get("servers", rep.get("servers", 0))
    if "srv_now" in body:
        rep["srv_now"] = body.get("srv_now", rep.get("srv_now", []))
    if body.get("prices"):
        rep["prices"] = body["prices"]
    if body.get("rarities"):
        rep["rarities"] = body["rarities"]
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


# ── dashboard (whole-fleet exec summary, auto-updates every 30s) ──
dash_msgs = []   # list of Message

def make_dashboard() -> discord.Embed:
    g, up, g_srv = {"accts": 0, "bucks": 0, "pets": 0, "fg": 0, "eggs": 0}, 0, 0
    fields = []
    for p in PHONES:
        on = online(p)
        if on:
            up += 1
        su = _inv_summary(p)
        for k in g:
            g[k] += su[k]
        foot = reports.get(p, {}).get("footer") or "no report yet"
        g_srv += reports.get(p, {}).get("servers", 0)
        now = reports.get(p, {}).get("srv_now") or []
        now_line = ("📍 " + " ".join(f"`{x}`" for x in now)) if now else ""
        fields.append((f"{'🟢' if on else '🔴'} Phone {p}",
                       f"{foot}\n{now_line}\n`{su['accts']}` acct · `{su['bucks']:,}`💰 · `{su['pets']}`🐾 ({su['fg']} FG) · {su['eggs']}🥚"))
    # colour reflects fleet health: all online = green, some offline = orange, all down = red
    if   up == len(PHONES): color = 0x2ECC71
    elif up == 0:           color = 0xE74C3C
    else:                   color = 0xE67E22
    dot = "🟢" if up == len(PHONES) else ("🔴" if up == 0 else "🟠")
    e = discord.Embed(title=f"{dot} Fleet Dashboard", color=color, timestamp=discord.utils.utcnow())
    for name, value in fields:
        e.add_field(name=name, value=value, inline=False)
    # top pets across the whole fleet, coloured by rarity
    top = sorted(_pets_totals("all").items(), key=lambda kv: -kv[1]["count"])[:10]
    if top:
        rar = _all_rarities()
        lines = []
        for pid, v in top:
            rn, pump = _key_variant(pid)
            rarity = rar.get(rn, "")
            tag = "" if pump == "default" else f" ({pump.replace('_', ' ')})"
            lines.append(f"{_rarity_ansi(rarity)}{v['count']:>4} {v['fg']:>3}FG  {_display_name(rn)}{tag}{_ANSI_RESET}")
        e.add_field(name="🔝 Top pets (fleet)", value="```ansi\n" + "\n".join(lines)[:990] + "\n```",
                    inline=False)
    val, priced, unpriced = _est_value("all")
    tot = priced + unpriced
    pct = int(100 * priced / tot) if tot else 0
    e.add_field(name="💵 Est. value (StarPets −25% tax)",
                value=f"**≈ ${val:,.2f}**   ·   {pct}% of pets priced", inline=False)
    e.description = (f"**{g['bucks']:,}** 💰   ·   **{g['pets']}** 🐾 ({g['fg']} FG)   ·   "
                     f"{g['eggs']} 🥚   ·   {g['accts']} acct   ·   🌐 **{g_srv}** srv   ·   "
                     f"**{up}/{len(PHONES)}** phones online")
    e.set_footer(text="fleet summary · auto-updates every 30s · prices via StarPets")
    return e

@tasks.loop(seconds=30)
async def dash_loop():
    for m in list(dash_msgs):
        try:
            await m.edit(embed=make_dashboard())
        except discord.NotFound:
            dash_msgs.remove(m)
        except Exception:
            pass

@bot.tree.command(description="Auto-updating fleet dashboard: bucks/pets/servers/value (every 30s)")
async def dashboard(i: discord.Interaction):
    await i.response.send_message(embed=make_dashboard())
    dash_msgs.append(await i.original_response())
    if not dash_loop.is_running():
        dash_loop.start()


# ── servers / assignment ──
@bot.tree.command(description="Force a hopper to jump to RF<server> now")
async def goto(i: discord.Interaction, n: int, server: int, phone: str = "all"):
    await job_reply(i, phone, f"goto {n} {server}")

@bot.tree.command(description="Send a hopper to RF<server> and HOLD there (goto + pin)")
async def goto_pin(i: discord.Interaction, n: int, server: int, phone: str = "all"):
    await job_reply(i, phone, f"goto_pin {n} {server}")

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


_ANSI_RESET = "[0m"
def _rarity_ansi(rarity: str) -> str:
    """Discord ANSI foreground code for an Adopt Me rarity (used inside ```ansi blocks)."""
    r = (rarity or "").lower()
    if "legendary" in r: return "[33m"   # gold
    if "ultra"     in r: return "[35m"   # pink  (ultra-rare)
    if "uncommon"  in r: return "[32m"   # green
    if "rare"      in r: return "[34m"   # blue
    if "common"    in r: return "[30m"   # gray
    return "[37m"                          # unknown -> white


def _inv_summary(phone: str) -> dict:
    """Totals across the target phone(s): accounts, bucks, pets, full-grown, eggs."""
    su = {"accts": 0, "bucks": 0, "pets": 0, "fg": 0, "eggs": 0}
    for d in _inv_of(phone):
        s = d.get("stats", {})
        su["accts"] += 1
        su["bucks"] += int(s.get("bucks", d.get("money", 0)) or 0)
        su["pets"]  += int(s.get("petCount", 0) or 0)
        su["eggs"]  += int(s.get("eggCount", 0) or 0)
        for info in ((d.get("pets") or {}).get("by_type") or {}).values():
            if isinstance(info, dict):
                su["fg"] += info.get("fg", 0)
    return su


def _pets_totals(phone: str) -> dict:
    """kind(+neon/mega) -> {count, fg, rarity} aggregated across the target phone(s)."""
    totals = {}
    for d in _inv_of(phone):
        for pid, info in ((d.get("pets") or {}).get("by_type") or {}).items():
            if not isinstance(info, dict):
                continue
            t = totals.setdefault(pid, {"count": 0, "fg": 0, "rarity": ""})
            t["count"] += info.get("count", 0)
            t["fg"]    += info.get("fg", 0)
            if info.get("rarity"):
                t["rarity"] = str(info["rarity"])
    return totals


# ─────────────────────── StarPets value (prices come FROM the phones) ───────────────────────
# The phones (agent.py) reach the StarPets API cleanly and fetch floor prices for their own
# pets, sending a "prices" map ("realName|pumping" -> USD) in each poll. The VPS just merges
# and applies them — no VPS -> StarPets calls (the VPS host does TLS interception).
def _key_variant(key: str):
    """/pets key -> (realName, pumping).  'shadow_dragon (neon)' -> ('shadow_dragon','neon')."""
    if key.endswith(" (mega neon)"): return key[:-12], "mega_neon"
    if key.endswith(" (neon)"):      return key[:-7],  "neon"
    return key, "default"


def _all_prices() -> dict:
    """Merge the price maps reported by every phone."""
    m = {}
    for p in PHONES:
        m.update(reports.get(p, {}).get("prices") or {})
    return m


def _all_rarities() -> dict:
    """Merge the StarPets rarity maps (realName -> rarity) reported by every phone."""
    m = {}
    for p in PHONES:
        m.update(reports.get(p, {}).get("rarities") or {})
    return m


def _display_name(kind: str) -> str:
    """Clean pet name from the kind: drop egg/event+year prefix, title-case.
    basic_egg_2022_alicorn -> Alicorn ; summer_2026_river_otter -> River Otter."""
    base = re.sub(r"^.*?\d{4}_", "", kind)
    return base.replace("_", " ").title()


TAX = 0.75      # StarPets takes ~25%, so realised value is 75% of the listing

def _group_value(rn: str, pump: str, count: int, prices: dict):
    """Value a pet group. Rule: 4 neons = 1 mega.  The -25% tax is applied to the UNIT
       price (each pet sells individually, floored to the cent), then × qty.
       normal → default ; neon → (count/4) × mega ; mega → mega.
       Returns (value, taxed_unit_price, qty, priced?)."""
    if pump == "default":
        p, qty = prices.get(f"{rn}|default"), count
    else:
        p = prices.get(f"{rn}|mega_neon")               # neon + mega both priced at mega
        qty = count / 4 if pump == "neon" else count
    if p is None:
        return (0.0, p, qty, False)
    unit = math.floor(p * TAX * 100 + 1e-6) / 100       # taxed unit price, floored to 2dp
    val  = math.floor(unit * qty * 100 + 1e-6) / 100    # line total, floored to 2dp
    return (val, unit, qty, True)


def _est_value(phone: str):
    """(total USD, priced pet count, unpriced pet count) from phone-reported StarPets floors."""
    prices = _all_prices()
    total, priced, unpriced = 0.0, 0, 0
    for key, v in _pets_totals(phone).items():
        rn, pump = _key_variant(key)
        val, _p, _q, ok = _group_value(rn, pump, v["count"], prices)
        if ok:
            total += val; priced += v["count"]
        else:
            unpriced += v["count"]
    return total, priced, unpriced

@bot.tree.command(description="Each account's Adopt Me inventory (bucks/pets/eggs)")
async def inv(i: discord.Interaction, phone: str = "all"):
    data = _inv_of(phone)
    if not data:
        return await i.response.send_message(f"⚠️ [{phone}] no inventory reported — is the monitor running and the phone online?")
    rows = []
    for d in sorted(data, key=lambda d: -int((d.get("stats", {})).get("bucks", d.get("money", 0)) or 0)):
        s = d.get("stats", {})
        bucks = int(s.get("bucks", d.get("money", 0)) or 0)
        pets  = int(s.get("petCount", 0) or 0)
        eggs  = int(s.get("eggCount", 0) or 0)
        rows.append(f"{d.get('player', '?')[:14]:<14} {bucks:>8,} {pets:>4} {eggs:>4}")
    su = _inv_summary(phone)
    e = discord.Embed(title=f"💰 Inventory · {phone}", color=0xF1C40F, timestamp=discord.utils.utcnow())
    e.description = "```\naccount           bucks pets eggs\n" + ("\n".join(rows))[-3800:] + "\n```"
    e.add_field(name="👤 Accounts", value=f"{su['accts']}", inline=True)
    e.add_field(name="💰 Bucks",    value=f"{su['bucks']:,}", inline=True)
    e.add_field(name="🐾 Pets",     value=f"{su['pets']}  ·  {su['fg']} FG  ·  {su['eggs']}🥚", inline=True)
    await i.response.send_message(embed=e)

@bot.tree.command(description="Show a phone's StarPets price-fetch log (why value is/isn't priced)")
async def pricelog(i: discord.Interaction, phone: str = "A"):
    await job_reply(i, phone, "pricelog", code=True)

@bot.tree.command(description="Force phones to re-fetch StarPets prices now (they refresh hourly anyway)")
async def refetch(i: discord.Interaction, phone: str = "all"):
    await job_reply(i, phone, "refetch")

@bot.tree.command(description="Inventory value breakdown: count × StarPets floor per pet")
async def value(i: discord.Interaction, phone: str = "all"):
    prices = _all_prices()
    rows, grand = [], 0.0
    for key, v in _pets_totals(phone).items():
        rn, pump = _key_variant(key)
        line, p, qty, ok = _group_value(rn, pump, v["count"], prices)
        if not ok:
            continue
        grand += line
        tag = "" if pump == "default" else f" ({pump.replace('_', ' ')})"   # neon / mega neon
        qtystr = f"{v['count']}/4" if pump == "neon" else f"{v['count']}"    # neon counts as /4 (=mega)
        rows.append((line, f"{qtystr:>7} x ${p:<5} = ${line:>8,.2f}  {_display_name(rn)}{tag}"))
    if not rows:
        return await i.response.send_message(f"[{phone}] no priced pets yet")
    rows.sort(reverse=True)
    body = "cnt  unit      line      pet\n" + "\n".join(r[1] for r in rows[:35])
    e = discord.Embed(title=f"💵 Value breakdown · {phone}", color=0xF1C40F, timestamp=discord.utils.utcnow())
    e.description = f"```\n{body}\n```"
    e.add_field(name="Grand total", value=f"**${grand:,.2f}**", inline=True)
    e.add_field(name="Pet types",   value=f"{len(rows)}", inline=True)
    e.set_footer(text="unit = StarPets floor − 25% tax (rounded down) · line = qty × unit · neon = count/4 (=mega)")
    await i.response.send_message(embed=e)

@bot.tree.command(description="All pets across every account: count + full-grown, most-owned first")
async def pets(i: discord.Interaction, phone: str = "all"):
    data = _inv_of(phone)
    if not data:
        return await i.response.send_message(f"[{phone}] no inv reported")
    totals = _pets_totals(phone)
    if not totals:
        return await i.response.send_message(f"[{phone}] no pets found")
    rar = _all_rarities()
    tot   = sum(v["count"] for v in totals.values())
    totfg = sum(v["fg"] for v in totals.values())
    rows = []
    for pid, v in sorted(totals.items(), key=lambda kv: -kv[1]["count"])[:40]:
        rn, pump = _key_variant(pid)
        rarity = rar.get(rn, "")
        tag = "" if pump == "default" else f" ({pump.replace('_', ' ')})"
        line = f"{v['count']:>4} {v['fg']:>4}FG  {_display_name(rn)}{tag}"
        rows.append(_rarity_ansi(rarity) + line + _ANSI_RESET)   # colour whole row by rarity
    legend = (f"{_rarity_ansi('legendary')}Legendary {_rarity_ansi('ultra')}Ultra "
              f"{_rarity_ansi('rare')}Rare {_rarity_ansi('uncommon')}Uncommon "
              f"{_rarity_ansi('common')}Common{_ANSI_RESET}")
    e = discord.Embed(title=f"🐾 Pets · {phone}", color=0x2ECC71, timestamp=discord.utils.utcnow())
    e.description = "```ansi\n" + legend + "\ncnt   fg  pet\n" + "\n".join(rows) + "\n```"
    e.add_field(name="🐾 Total",      value=f"{tot}", inline=True)
    e.add_field(name="🌟 Full grown", value=f"{totfg}", inline=True)
    e.add_field(name="🔖 Types",      value=f"{len(totals)}", inline=True)
    await i.response.send_message(embed=e)


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
