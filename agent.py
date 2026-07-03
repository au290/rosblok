"""
agent.py — phone side. Headless worker that polls the VPS (server.py) for jobs,
runs them locally via tmux (exactly like master_bot.py used to), and reports status
back. NO Discord token lives on the phone anymore — the token is only on the VPS.

Run on the phone (Termux):
    pip install -U discord.py   # not needed here; only stdlib is used
    python agent.py

Config: edit the CONFIG block, or drop a config.txt next to this file (gitignored)
with lines like  VPS_URL=https://your.vps:8080  /  KEY=...  /  PHONE=A  /  HOPPERS=1,2,3,4,5
"""

import re
import json
import time
import shlex
import threading
import subprocess
import urllib.request
from pathlib import Path

# ─────────────────────────── CONFIG — EDIT THIS ───────────────────────────
VPS_URL  = "http://YOUR_VPS_IP:8080"   # where server.py listens
KEY      = "CHANGE_ME_SHARED_SECRET"   # must match server.py KEY
PHONE    = "A"                          # this phone's id ("A" / "B")
LUA      = "lua"                        # "lua5.4" if `which lua` shows that
SESSION  = "farm"                       # tmux session name
HOPPERS  = [1, 2, 3, 4, 5]              # this phone's hoppers
INTERVAL = 2                            # seconds between polls
# ───────────────────────────────────────────────────────────────────────────

# Single home on shared storage, managed by hand in the file manager: this holds
# EVERYTHING — config.txt, hopper*.lua, cmd/, logs, link.txt, servers.txt.
# (Hardcoded, not derived from __file__, so it works no matter where you launch from.)
BASE_DIR = Path("/storage/emulated/0/Download")
RUN_DIR  = BASE_DIR
LUA_CMDS = {"lua", "lua5.4", "lua5.3", "luajit"}
# Delta executor paths (its own app storage — not the Termux sandbox).
INV_DIR  = Path("/storage/emulated/0/Delta/Workspace/inv")
AUTOEXEC = Path("/storage/emulated/0/Delta/Autoexecute")

DATA_DIR  = RUN_DIR
MAP_FILE  = DATA_DIR / "servers.txt"
POOL_FILE = DATA_DIR / "link.txt"

# config.txt (gitignored) overrides the CONFIG block above
_cfg = BASE_DIR / "config.txt"
if _cfg.exists():
    for _line in _cfg.read_text().splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = (s.strip() for s in _line.split("=", 1))
            if   _k == "VPS_URL" and _v: VPS_URL = _v
            elif _k == "KEY"     and _v: KEY = _v
            elif _k == "PHONE"   and _v: PHONE = _v
            elif _k == "LUA"     and _v: LUA = _v
            elif _k == "SESSION" and _v: SESSION = _v
            elif _k == "HOPPERS" and _v: HOPPERS = [int(x) for x in _v.split(",") if x.strip()]


# ─────────────────────────── tmux / hopper control ───────────────────────────
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
    if not (RUN_DIR / f"hopper{n}.lua").exists():
        return f"hopper{n}.lua not found in {RUN_DIR}"
    ensure_window(n)
    tmux("send-keys", "-t", tgt(n), f"cd '{RUN_DIR}' && {LUA} hopper{n}.lua", "Enter")
    return f"started hopper{n}"


def stop_hopper(n: int) -> str:
    if f"h{n}" not in windows():
        return f"hopper{n} not started"
    tmux("send-keys", "-t", tgt(n), "C-c")
    return f"stopped hopper{n}"


def pane_tail(n: int, lines: int = 1) -> str:
    if f"h{n}" not in windows():
        return "—"
    rows = [l for l in tmux("capture-pane", "-p", "-t", tgt(n)).stdout.splitlines() if l.strip()]
    return "\n".join(rows[-lines:]) if rows else "—"


# ─────────────────────────── data / cmd files ───────────────────────────
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
    d = RUN_DIR / "cmd"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"h{n}.txt").write_text(c)


def clear_cmd(n: int):
    f = RUN_DIR / "cmd" / f"h{n}.txt"
    if f.exists():
        f.unlink()


# ─────────────────────────── autoexec scripts ───────────────────────────
def _script(name: str) -> Path:
    return AUTOEXEC / Path(name).name


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "hopperbot"})
    return urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace")


def _lua_list(csv: str) -> str:
    xs = [x.strip() for x in csv.split(",") if x.strip()]
    return "{" + ", ".join(f'"{x}"' for x in xs) + "}"


def do_autotrade(o: dict) -> str:
    cats = ["pets", "toys", "food", "transport", "gifts", "stickers", "pet_accessories"]
    chosen = [c for c in cats if o.get(c)]
    items, usernames = o.get("items", ""), o.get("usernames", "")
    if not chosen and not items.strip():
        return "pick a category or pass items"
    f = AUTOEXEC / "util.lua"
    if not f.exists():
        return f"util.lua not found in {AUTOEXEC}"
    t = f.read_text(errors="replace")
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
    return f"auto-trade ON → {what}, {who}"


# ─────────────────────────── status board ───────────────────────────
SRV_RE  = re.compile(r"RF\d+")
PROG_RE = re.compile(r"(\d+)s\s*/\s*(\d+)s")


def _parse(n: int):
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
    m    = re.search(r"Mem:\s+(\d+)\s+\d+\s+(\d+)", sh("free -m"))
    load = re.search(r"[\d.]+", sh("cat /proc/loadavg"))
    disk = re.search(r"\s(\d+)\s+\d+%\s", sh("df /data 2>/dev/null | tail -1"))
    ram  = f"{m.group(2)}/{m.group(1)}MB" if m else "?"
    cpu  = load.group() if load else "?"
    gb   = f"{int(disk.group(1)) / 1048576:.1f}G" if disk else "?"
    return f"🧠 {ram} free · ⚙️ load {cpu} · 💾 {gb} free"


def build_board():
    rows, up, now = [], 0, []
    for n in HOPPERS:
        if not is_running(n):
            rows.append(f"{n:>2}  {'—':<5} stopped")
            continue
        if "PINNED" in pane_tail(n):
            up += 1
            now.append(f"{n}:PIN")
            rows.append(f"{n:>2}  📌    held")
            continue
        srv, el, tot = _parse(n)
        up += 1
        now.append(f"{n}:{srv or '?'}")               # this hopper's current server
        prog = f"{_bar(el, tot)} {el:>3}/{tot}s" if tot else "starting…"
        rows.append(f"{n:>2}  {srv or '??':<5} {prog}")
    board  = "```\n #  srv   progress\n" + "\n".join(rows) + "\n```"
    footer = f"{device_health()} · {up}/{len(HOPPERS)} running"
    return board, footer, now


def read_inv() -> dict:
    out = {}
    if INV_DIR.exists():
        for f in sorted(INV_DIR.glob("*.json")):
            try:
                out[f.stem] = json.loads(f.read_text())
            except Exception:
                pass
    return out


# ─────────────── StarPets pricing (fetched here — phones reach the API cleanly) ───────────────
# The VPS host does TLS interception, so pricing lives on the phone. We fetch floor prices for
# this phone's pets in a background thread and include them in each poll for the VPS to merge.
_SP_URL = "https://market.apineural.com/api/v2/store/items/all"
_SP_HEADERS = {
    "content-type": "application/json",
    "origin": "https://starpets.gg", "referer": "https://starpets.gg/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
}
PRICES_FILE = RUN_DIR / "prices.json"
PRICES = {}
RARITIES = {}          # realName -> rarity (from StarPets), sent to the VPS for /pets colors
PRICE_LOG = []         # recent price-worker log lines, surfaced by /pricelog
PRICE_TS = {}          # pk -> last fetch time (in-memory; empty on restart => refetch all)
PRICE_TTL = 3600       # re-fetch each price at most once per hour
INTERVAL_PRICE = 120   # price-worker scan cadence (new pets + /refetch land within this)
if PRICES_FILE.exists():
    try:
        PRICES = json.loads(PRICES_FILE.read_text())
    except Exception:
        PRICES = {}


def _key_variant(key: str):
    if key.endswith(" (mega neon)"): return key[:-12], "mega_neon"
    if key.endswith(" (neon)"):      return key[:-7],  "neon"
    return key, "default"


def _plog(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    PRICE_LOG.append(line)
    if len(PRICE_LOG) > 60:
        del PRICE_LOG[0]
    print(f"[price {PHONE}] {line}", flush=True)


def _sp_floor(real_name: str, pumping: str):
    """(floor USD, rarity) for this pet + variant, or (None, None). Adopt Me prefixes
    event/egg pets; StarPets realName is sometimes the full kind, sometimes the bare name —
    search by the year-stripped name and match realName against both. Logs failures."""
    stripped = re.sub(r"^.*?\d{4}_", "", real_name)
    names = {real_name, stripped}
    body = json.dumps({"filter": {"name": stripped.replace("_", " "),
                                  "types": [{"type": t} for t in ("pet", "egg")]},
                       "page": 1, "amount": 50, "currency": "usd",
                       "sort": {"popularity": "desc"}}).encode()
    items, last_err = [], None
    for attempt in range(3):                            # the API 400s intermittently
        try:
            req = urllib.request.Request(_SP_URL, data=body, headers=_SP_HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                items = json.load(r).get("items", [])
            break
        except Exception as e:
            last_err = e
            time.sleep(2)
    if not items:
        _plog(f"{real_name}: fetch failed ({last_err})")
        return None, None
    matches = [it for it in items if it.get("realName") in names
               and (it.get("pumping") or "default") == pumping and it.get("price")]
    if not matches:
        _plog(f"{real_name}|{pumping}: 0 matches in {len(items)} results (search='{stripped}')")
        return None, None
    floor = min(it["price"] for it in matches)
    rarity = matches[0].get("rare")
    return floor, rarity


def price_worker():
    """Background loop: fetch + cache StarPets floors + rarity for this phone's pets."""
    _plog(f"started (VPS <- prices from this phone)")
    while True:
        try:
            keys = set()
            for d in read_inv().values():
                if isinstance(d, dict):
                    keys.update((d.get("pets") or {}).get("by_type") or {})
            now = time.time()
            # (re)fetch anything uncached OR older than the TTL
            todo = [k for k in keys
                    if now - PRICE_TS.get(f"{_key_variant(k)[0]}|{_key_variant(k)[1]}", 0) > PRICE_TTL]
            _plog(f"scan: {len(keys)} pet kinds, {len(todo)} to (re)fetch, {len(PRICES)} cached")
            ok, changed = 0, False
            for key in todo:
                rn, pump = _key_variant(key)
                pk = f"{rn}|{pump}"
                price, rarity = _sp_floor(rn, pump)
                PRICE_TS[pk] = time.time()              # mark attempted (success or fail) so it respects TTL
                if price is not None:
                    PRICES[pk] = price; ok += 1; changed = True
                    if rarity:
                        RARITIES[rn] = rarity
                    _plog(f"{rn}|{pump} = ${price} ({rarity})")
                time.sleep(1)                           # be gentle on the API
            if changed:
                try:
                    PRICES_FILE.write_text(json.dumps(PRICES))
                except Exception as e:
                    _plog(f"cache write failed: {e}")
            _plog(f"done: {ok}/{len(todo)} priced, {len(PRICES)} total")
        except Exception as e:
            _plog(f"worker error: {e}")
        time.sleep(INTERVAL_PRICE)                      # rescan (new pets + TTL refresh + /refetch)


# ─────────────────────────── job dispatch ───────────────────────────
def dispatch(cmd: str) -> str:
    if cmd.startswith("autotrade "):
        return do_autotrade(json.loads(cmd[len("autotrade "):]))
    p = shlex.split(cmd)
    v, a = p[0], p[1:]
    if v == "pricelog":
        return (f"prices cached: {len(PRICES)} · rarities: {len(RARITIES)}\n"
                + ("\n".join(PRICE_LOG[-24:]) or "(no price activity yet)"))
    if v == "refetch":
        PRICE_TS.clear()                                 # mark all stale -> next scan re-fetches
        return f"marked {len(PRICES)} prices stale — re-fetching within {INTERVAL_PRICE}s"
    if v == "start":     return start_hopper(int(a[0]))
    if v == "stop":      return stop_hopper(int(a[0]))
    if v == "restart":   stop_hopper(int(a[0])); return start_hopper(int(a[0]))
    if v == "startall":  return "\n".join(start_hopper(n) for n in HOPPERS)
    if v == "stopall":   tmux("kill-session", "-t", SESSION); return f"killed session '{SESSION}'"
    if v == "goto":      write_cmd(int(a[0]), f"goto{a[1]}"); return f"hopper{a[0]} → RF{a[1]}"
    if v == "all_goto":
        for n in HOPPERS: write_cmd(n, f"pin {a[0].strip()}")
        return "all hoppers pinned, holding (/continue to resume)"
    if v == "continue":
        for n in HOPPERS: clear_cmd(n)
        return "resuming rotation"
    if v == "assign":    set_range(int(a[0]), int(a[1]), int(a[2])); return f"hopper{a[0]} = links {a[1]}-{a[2]} ({len(hopper_links(int(a[0])))} servers)"
    if v == "assigns":
        d = ranges()
        return "\n".join(f"hopper{k}: {d[k][0]}-{d[k][1]}" for k in sorted(d)) or "(none)"
    if v == "servers":
        lst = hopper_links(int(a[0]))
        return "\n".join(f"RF{j}: {l}" for j, l in enumerate(lst, 1)) or "(empty)"
    if v == "link_add":
        with open(POOL_FILE, "a", encoding="utf-8") as f:
            f.write(a[0].strip() + "\n")
        return f"added link #{len(pool())}"
    if v == "logs":
        n = int(a[0]); lines = int(a[1]) if len(a) > 1 else 15
        return pane_tail(n, lines)
    if v == "scripts":
        fs = [f for f in sorted(AUTOEXEC.glob("*")) if f.is_file()] if AUTOEXEC.exists() else []
        return "\n".join(f"{f.name}  {f.stat().st_size}b" for f in fs) or "(empty)"
    if v == "script_get":
        f = _script(a[0])
        return f.read_text(errors="replace")[-1800:] if f.exists() else f"{f.name} not found"
    if v == "script_add":
        AUTOEXEC.mkdir(parents=True, exist_ok=True)
        data = _fetch(a[1]); _script(a[0]).write_text(data)
        return f"saved {_script(a[0]).name} ({len(data)}b)"
    if v == "script_del":
        f = _script(a[0])
        if not f.exists(): return f"{f.name} not found"
        f.unlink(); return f"deleted {f.name}"
    return f"unknown cmd: {cmd}"


def safe(cmd: str) -> str:
    try:
        return dispatch(cmd)
    except Exception as e:
        return f"error running '{cmd}': {e}"


# ─────────────────────────── poll loop ───────────────────────────
def poll(results: list) -> list:
    board, footer, now = build_board()
    servers = sum(len(hopper_links(n)) for n in HOPPERS)   # total private servers in rotation
    body = json.dumps({"board": board, "footer": footer, "inv": read_inv(),
                       "servers": servers, "srv_now": now, "prices": PRICES,
                       "rarities": RARITIES, "results": results}).encode()
    req = urllib.request.Request(f"{VPS_URL}/api/{PHONE}/poll", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Key": KEY,
                                          "User-Agent": "Mozilla/5.0 (hopperbot)"})  # dodge Cloudflare's Python-urllib ban (err 1010)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode()).get("jobs", [])


def main():
    print(f"[agent {PHONE}] polling {VPS_URL} every {INTERVAL}s")
    threading.Thread(target=price_worker, daemon=True).start()   # fetch StarPets prices in the background
    results = []
    while True:
        try:
            jobs = poll(results); results = []
            if jobs:
                # run them, then poll again immediately so the Discord command returns fast
                results = [{"id": j["id"], "text": safe(j["cmd"])} for j in jobs]
                jobs = poll(results); results = []
                if jobs:
                    results = [{"id": j["id"], "text": safe(j["cmd"])} for j in jobs]
        except Exception as e:
            print(f"[agent {PHONE}] poll error: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
