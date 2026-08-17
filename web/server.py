"""
Standalone web control plane for Hopper Fleet.

This server deliberately lives beside the existing Discord implementation. It
keeps the phone-agent protocol compatible with agent.py, but has no Discord
dependency and exposes a browser dashboard instead.

Run from this directory:
    python server.py

Phone agents and direct Adopt Me monitors use the same poll path and X-Key
authentication. The agent still supplies hopper/control data; a monitor can
send inventory independently.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import re
import shlex
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import web


BASE_DIR = Path(__file__).resolve().parent
ASSET_DIR = BASE_DIR / "assets"
HOPPER_META_FILE = BASE_DIR / "hoppers.json"

# These defaults are intentionally local to the web app. They do not read or
# modify the root server.py configuration.
HOST = "0.0.0.0"
PORT = 8090
KEY = "CHANGE_ME_SHARED_SECRET"
WEB_TOKEN = "CHANGE_ME_WEB_TOKEN"
PHONES = ["A", "B"]
GRACE = 60


def load_config() -> None:
    """Load web/config.txt and optional web/web_token.txt if present."""
    global HOST, PORT, KEY, WEB_TOKEN, PHONES, GRACE

    cfg = BASE_DIR / "config.txt"
    if cfg.exists():
        for raw in cfg.read_text(encoding="utf-8").splitlines():
            if "=" not in raw or raw.lstrip().startswith("#"):
                continue
            name, value = (part.strip() for part in raw.split("=", 1))
            if name == "HOST" and value:
                HOST = value
            elif name == "PORT" and value:
                PORT = int(value)
            elif name == "KEY" and value:
                KEY = value
            elif name == "WEB_TOKEN" and value:
                WEB_TOKEN = value
            elif name == "PHONES" and value:
                PHONES = [item.strip() for item in value.split(",") if item.strip()]
            elif name == "GRACE" and value:
                # Keep the dashboard from flickering offline during a short
                # mobile/VPS network stall.
                GRACE = max(60, int(value))

    # config.txt is the installer's source of truth. Keep the legacy token file
    # as a fallback for older installs that still use it with a placeholder config.
    token_file = BASE_DIR / "web_token.txt"
    token_value = token_file.read_text(encoding="utf-8").strip() if token_file.exists() else ""
    if token_value and WEB_TOKEN.startswith("CHANGE_ME"):
        WEB_TOKEN = token_value


load_config()

# Inventory reports can come from several Adopt Me monitors on one phone.
# Keep an account for a little longer than the phone heartbeat so a transient
# executor/game reload does not immediately remove it from the dashboard.
INVENTORY_GRACE = max(90, GRACE * 5)

# Per-phone state accepts the existing agent.py report payload plus direct
# account-level inventory reports from monitor_adoptme.lua.
jobs: dict[str, list[dict]] = {phone: [] for phone in PHONES}
futures: dict[str, asyncio.Future] = {}
reports: dict[str, dict] = {
    phone: {
        "board": "",
        "footer": "",
        "inv": {},
        "inv_seen": {},
        "servers": 0,
        "srv_now": [],
        "packages": {},
        "accounts": {},
        "prices": {},
        "rarities": {},
        "rotations": {},
        "trades": {},
        "ts": 0.0,
    }
    for phone in PHONES
}

# The long-lived WEB_TOKEN never goes into the page or URL; the browser receives
# only this signed HttpOnly cookie. Signing makes it survive a server restart.
SESSION_TTL = 24 * 60 * 60


def _session_cookie(expires: int | None = None) -> str:
    expiry = int(expires or (time.time() + SESSION_TTL))
    payload = f"{expiry}.{uuid.uuid4().hex}"
    signature = hmac.new(
        WEB_TOKEN.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{signature}"


def targets(phone: str) -> list[str]:
    return PHONES if phone == "all" else [phone]


def online(phone: str) -> bool:
    return time.time() - reports.get(phone, {}).get("ts", 0) < GRACE


def validate_phone(phone: str) -> str:
    if phone != "all" and phone not in PHONES:
        raise ValueError(f"unknown phone: {phone}")
    return phone


def enqueue(phone: str, command: str) -> tuple[str, asyncio.Future]:
    job_id = uuid.uuid4().hex[:10]
    future = asyncio.get_running_loop().create_future()
    jobs[phone].append({"id": job_id, "cmd": command})
    futures[job_id] = future
    return job_id, future


async def run_on(phone_list: list[str], command: str, timeout: int = 15) -> str:
    """Queue one command on each online phone and collect its response."""
    pending: dict[str, tuple[str, asyncio.Future] | None] = {}
    for phone in phone_list:
        if phone not in PHONES:
            continue
        if not online(phone):
            pending[phone] = None
        else:
            pending[phone] = enqueue(phone, command)

    output: list[str] = []
    for phone, item in pending.items():
        if item is None:
            output.append(f"[{phone}] offline")
            continue
        job_id, future = item
        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            output.append(f"[{phone}] {result}")
        except asyncio.TimeoutError:
            futures.pop(job_id, None)
            output.append(f"[{phone}] no response")
    return "\n".join(output) or "(no phones)"


def _normalise_trade_report(value: object) -> dict | None:
    """Keep only the documented live trade fields; this is never written to disk."""
    if not isinstance(value, dict):
        return None
    status = str(value.get("status", "")).strip().lower()[:40]
    if not status:
        return None
    result: dict = {"status": status, "fresh": bool(value.get("fresh", False))}
    for key in ("ts", "age", "count"):
        if key not in value or isinstance(value[key], bool):
            continue
        try:
            number = float(value[key])
        except (TypeError, ValueError):
            continue
        if math.isfinite(number) and number >= 0:
            result[key] = int(number) if number.is_integer() else number
    try:
        result["grace"] = max(0, min(int(value.get("grace", 0)), 3600))
    except (TypeError, ValueError):
        pass
    filename = Path(str(value.get("file", ""))).name
    if re.fullmatch(r"[^/\\]+_winteraddons\.json", filename, re.I):
        result["file"] = filename
    items = value.get("items")
    if isinstance(items, list):
        clean_items = []
        for item in items[:100]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()[:160]
            if not name:
                continue
            clean = {"name": name}
            try:
                qty = float(item.get("qty", 0))
                if math.isfinite(qty) and qty >= 0:
                    clean["qty"] = int(qty) if qty.is_integer() else qty
            except (TypeError, ValueError):
                clean["qty"] = 0
            clean_items.append(clean)
        result["items"] = clean_items
    meta = value.get("meta")
    if isinstance(meta, dict):
        result["meta"] = {}
        for key, item in list(meta.items())[:40]:
            if not isinstance(item, (str, int, float, bool)):
                continue
            if isinstance(item, float) and not math.isfinite(item):
                continue
            result["meta"][str(key)[:80]] = str(item)[:160] if isinstance(item, str) else item
        categories = meta.get("categories")
        if isinstance(categories, dict):
            clean_categories = {}
            for name, count in list(categories.items())[:40]:
                try:
                    count = float(count)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(count) and count >= 0:
                    clean_categories[str(name)[:80]] = int(count) if count.is_integer() else count
            result["meta"]["categories"] = clean_categories
    return result


def _merge_report(phone: str, body: dict) -> None:
    report = reports[phone]
    now = time.time()
    if body.get("board"):
        report["board"] = body["board"]
    for key in ("footer", "servers", "srv_now"):
        if key in body:
            report[key] = body[key]
    if isinstance(body.get("packages"), dict):
        report["packages"] = {
            str(number): str(package)
            for number, package in body["packages"].items()
            if re.fullmatch(r"\d+", str(number)) and re.fullmatch(r"[A-Za-z0-9_.]+", str(package))
        }
    if isinstance(body.get("accounts"), dict):
        report["accounts"] = {
            str(number): str(account).strip()[:80]
            for number, account in body["accounts"].items()
            if re.fullmatch(r"\d+", str(number)) and str(account).strip()
        }
    if isinstance(body.get("rotations"), dict):
        report["rotations"] = body["rotations"]
    if isinstance(body.get("trades"), dict):
        report["trades"] = {
            str(number): trade
            for number, value in body["trades"].items()
            if re.fullmatch(r"\d+", str(number))
            and (trade := _normalise_trade_report(value)) is not None
        }
    incoming = body.get("inv")
    if isinstance(incoming, dict):
        seen = report.setdefault("inv_seen", {})
        inventory = report.setdefault("inv", {})
        for account, data in incoming.items():
            if not isinstance(data, dict):
                continue
            account = str(account)
            inventory[account] = data
            seen[account] = now
        for account, last_seen in list(seen.items()):
            if now - float(last_seen) > INVENTORY_GRACE:
                seen.pop(account, None)
                inventory.pop(account, None)
    if body.get("prices"):
        report["prices"] = body["prices"]
    if body.get("rarities"):
        report["rarities"] = body["rarities"]
    report["ts"] = now

    for result in body.get("results", []):
        job_id = result.get("id")
        future = futures.pop(job_id, None)
        if future and not future.done():
            future.set_result(result.get("text", ""))


async def handle_poll(request: web.Request) -> web.Response:
    """Receive reports from agent.py or a direct Adopt Me monitor."""
    if request.headers.get("X-Key") != KEY:
        return web.json_response({"error": "bad key"}, status=403)
    phone = request.match_info["phone"]
    if phone not in PHONES:
        return web.json_response({"error": "unknown phone"}, status=404)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "invalid JSON"}, status=400)

    _merge_report(phone, body)
    pending = jobs[phone]
    jobs[phone] = []
    return web.json_response({"jobs": pending})


def _session_valid(request: web.Request) -> bool:
    now = time.time()
    cookie = request.cookies.get("web_session", "")
    parts = cookie.split(".")
    if len(parts) == 3:
        expiry, nonce, signature = parts
        try:
            expires = int(expiry)
        except ValueError:
            expires = 0
        payload = f"{expiry}.{nonce}"
        expected = hmac.new(
            WEB_TOKEN.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if expires > now and hmac.compare_digest(signature, expected):
            return True

    # Bearer auth is useful for scripts and local API clients. It is not put in
    # the dashboard page, which uses the HttpOnly session cookie instead.
    auth = request.headers.get("Authorization", "")
    return bool(auth.startswith("Bearer ") and auth[7:].strip() == WEB_TOKEN)


def require_web(request: web.Request) -> web.Response | None:
    if not _session_valid(request):
        return web.json_response({"error": "authentication required"}, status=401)
    return None


async def handle_login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "invalid JSON"}, status=400)
    if body.get("token") != WEB_TOKEN or WEB_TOKEN.startswith("CHANGE_ME"):
        return web.json_response({"error": "invalid token"}, status=401)

    response = web.json_response({"ok": True})
    response.set_cookie(
        "web_session",
        _session_cookie(),
        max_age=SESSION_TTL,
        httponly=True,
        samesite="Lax",
        secure=False,
        path="/",
    )
    return response


async def handle_logout(request: web.Request) -> web.Response:
    response = web.json_response({"ok": True})
    response.del_cookie("web_session", path="/")
    return response


def _report_view(phone: str) -> dict:
    report = reports[phone]
    age = None if not report["ts"] else round(max(0, time.time() - report["ts"]), 1)
    return {
        "phone": phone,
        "online": online(phone),
        "last_seen_seconds": age,
        "board": report.get("board", ""),
        "footer": report.get("footer", ""),
        "inv": report.get("inv", {}),
        "servers": report.get("servers", 0),
        "srv_now": report.get("srv_now", []),
        "packages": report.get("packages", {}),
        "prices": report.get("prices", {}),
        "rarities": report.get("rarities", {}),
        "rotations": report.get("rotations", {}),
        "trades": report.get("trades", {}),
    }


def _inventory_rows(phone: str) -> list[dict]:
    rows: list[dict] = []
    now = time.time()
    for target in targets(phone):
        report = reports[target]
        seen = report.get("inv_seen") or {}
        for account, data in (report.get("inv") or {}).items():
            if now - float(seen.get(account, 0)) > INVENTORY_GRACE:
                continue
            if isinstance(data, dict) and data.get("player") and data.get("player") != "?":
                rows.append(data)
    return rows


def _inventory_summary(phone: str) -> dict:
    rows = _inventory_rows(phone)
    bucks = pets = full_grown = eggs = 0
    for data in rows:
        stats = data.get("stats", {})
        bucks += int(stats.get("bucks", data.get("money", 0)) or 0)
        pets += int(stats.get("petCount", 0) or 0)
        eggs += int(stats.get("eggCount", 0) or 0)
        for pet in (data.get("pets", {}).get("by_type", {}) or {}).values():
            full_grown += int(pet.get("fg", 0) or 0)
    return {
        "accounts": len(rows),
        "bucks": bucks,
        "pets": pets,
        "full_grown": full_grown,
        "eggs": eggs,
        "servers": sum(reports[p].get("servers", 0) for p in targets(phone)),
        "online": sum(1 for p in targets(phone) if online(p)),
        "phones": len(targets(phone)),
    }


def _key_variant(key: str) -> tuple[str, str]:
    if key.endswith(" (mega neon)"):
        return key[:-12], "mega_neon"
    if key.endswith(" (neon)"):
        return key[:-7], "neon"
    return key, "default"


def _pets_totals(phone: str) -> dict[str, dict]:
    totals: dict[str, dict] = {}
    for data in _inventory_rows(phone):
        for key, value in ((data.get("pets", {}).get("by_type", {}) or {}).items()):
            item = totals.setdefault(key, {"count": 0, "fg": 0})
            item["count"] += int(value.get("count", 0) or 0)
            item["fg"] += int(value.get("fg", 0) or 0)
    return totals


def _all_prices(phone: str) -> dict:
    result = {}
    for target in targets(phone):
        result.update(reports[target].get("prices", {}) or {})
    return result


def _all_rarities(phone: str) -> dict:
    result = {}
    for target in targets(phone):
        result.update(reports[target].get("rarities", {}) or {})
    return result


def _display_name(kind: str) -> str:
    stripped = re.sub(r"^.*?\d{4}_", "", kind)
    return stripped.replace("_", " ").title()


def _group_value(real_name: str, variant: str, count: int, prices: dict) -> tuple[float, bool]:
    if variant == "default":
        price = prices.get(f"{real_name}|default")
        quantity = count
    else:
        price = prices.get(f"{real_name}|mega_neon")
        quantity = count / 4 if variant == "neon" else count
    if price is None:
        return 0.0, False
    unit = math.floor(float(price) * 0.75 * 100 + 1e-6) / 100
    return math.floor(unit * quantity * 100 + 1e-6) / 100, True


def _value_summary(phone: str) -> dict:
    prices = _all_prices(phone)
    total = 0.0
    priced = unpriced = 0
    for key, item in _pets_totals(phone).items():
        real_name, variant = _key_variant(key)
        value, ok = _group_value(real_name, variant, item["count"], prices)
        if ok:
            total += value
            priced += item["count"]
        else:
            unpriced += item["count"]
    return {"usd": round(total, 2), "priced_pets": priced, "unpriced_pets": unpriced}


def _hopper_metadata() -> dict:
    """Load optional display metadata without changing agent-side behavior."""
    if not HOPPER_META_FILE.exists():
        return {}
    try:
        data = json.loads(HOPPER_META_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _hopper_rows(phone: str) -> list[dict]:
    """Turn the existing human-readable board report into table-ready rows."""
    metadata = _hopper_metadata()
    rows: list[dict] = []
    for target_phone in targets(phone):
        report = reports[target_phone]
        board = report.get("board", "") or ""
        for raw_line in board.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("```") or line.startswith("#"):
                continue
            match = re.match(r"^(\d+)\s+(\S+)\s*(.*)$", line)
            if not match:
                continue
            number = int(match.group(1))
            marker = match.group(2)
            tail = match.group(3)
            lowered = tail.lower()
            server = marker if re.fullmatch(r"RF\d+", marker) else ""
            if "stopped" in lowered:
                state = "stopped"
            elif "held" in lowered or "pinned" in lowered:
                state = "held"
            elif server or "starting" in lowered:
                state = "running"
            else:
                state = "starting"
            progress = re.search(r"(\d+)\s*/\s*(\d+)s", tail)
            elapsed = int(progress.group(1)) if progress else 0
            total = int(progress.group(2)) if progress else 0
            key = f"{target_phone}:{number}"
            item = metadata.get(key, {})
            if not isinstance(item, dict):
                item = {}
            packages = report.get("packages") or {}
            package = packages.get(str(number)) or item.get("package") or "Not detected"
            accounts = report.get("accounts") or {}
            rotation = (report.get("rotations") or {}).get(str(number), {})
            if not isinstance(rotation, dict):
                rotation = {}
            links = rotation.get("links", [])
            if not isinstance(links, list):
                links = []
            try:
                cooldown = max(3, min(int(rotation.get("cooldown", 240) or 240), 86400))
            except (TypeError, ValueError):
                cooldown = 240
            rotation = {
                "links": [str(link) for link in links],
                "loop": bool(rotation.get("loop", True)),
                "cooldown": cooldown,
            }
            rotation_label = f"{len(rotation['links'])} saved server{'s' if len(rotation['links']) != 1 else ''}"
            trade = (report.get("trades") or {}).get(str(number), {})
            if not isinstance(trade, dict):
                trade = {}
            account = item.get("account") or "-"
            if account == "-":
                account = accounts.get(str(number)) or "-"
            if account == "-" and trade.get("file"):
                account = re.sub(r"_winteraddons\.json$", "", trade["file"], flags=re.I)
            rows.append({
                "id": key,
                "phone": target_phone,
                "hopper": number,
                "device": item.get("device") or f"Phone {target_phone}",
                "account": account,
                "package": package,
                "target": item.get("target") or server or (rotation_label if rotation["links"] else ("Rotation" if state != "stopped" else "-")),
                "server": server,
                "rotation": rotation,
                "trade": trade,
                "elapsed": elapsed,
                "total": total,
                "status": state if online(target_phone) else "offline",
                "online": online(target_phone),
            })
    return rows


def _status_payload(phone: str) -> dict:
    selected = targets(phone)
    summaries = {target: _inventory_summary(target) for target in selected}
    combined = _inventory_summary(phone)
    combined["value"] = _value_summary(phone)
    pets = []
    rarities = _all_rarities(phone)
    prices = _all_prices(phone)
    for key, item in sorted(_pets_totals(phone).items(), key=lambda pair: -pair[1]["count"]):
        real_name, variant = _key_variant(key)
        pet_value, priced = _group_value(real_name, variant, item["count"], prices)
        pets.append(
            {
                "name": _display_name(real_name),
                "variant": variant,
                "count": item["count"],
                "full_grown": item["fg"],
                "rarity": rarities.get(real_name, ""),
                "value_usd": pet_value,
                "priced": priced,
            }
        )
    return {
        "generated_at": time.time(),
        "selected_phone": phone,
        "available_phones": PHONES,
        "summary": combined,
        "phones": [_report_view(target) for target in selected],
        "hoppers": _hopper_rows(phone),
        "phone_summaries": summaries,
        "inventory": _inventory_rows(phone),
        "pets": pets[:100],
    }


async def handle_status(request: web.Request) -> web.Response:
    denied = require_web(request)
    if denied:
        return denied
    try:
        phone = validate_phone(request.query.get("phone", "all"))
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response(_status_payload(phone))


def _required_int(body: dict, name: str, minimum: int = 1) -> int:
    value = body.get(name)
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer") from None
    if result < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return result


def _required_text(body: dict, name: str, max_length: int = 4000) -> str:
    value = str(body.get(name, "")).strip()
    if not value:
        raise ValueError(f"{name} is required")
    if len(value) > max_length:
        raise ValueError(f"{name} is too long")
    return value


def _rotation_command(body: dict) -> str:
    hopper = _required_int(body, "hopper")
    if hopper > 100:
        raise ValueError("hopper must be 100 or less")
    links = body.get("links")
    if not isinstance(links, list):
        raise ValueError("links must be a list")
    if len(links) > 100:
        raise ValueError("links must contain 100 URLs or less")
    clean_links: list[str] = []
    seen: set[str] = set()
    for raw in links:
        link = str(raw).strip()
        if not link:
            continue
        if len(link) > 2000:
            raise ValueError("a private-server URL is too long")
        if any(char in link for char in "'\"\r\n") or any(char.isspace() for char in link):
            raise ValueError("private-server URLs cannot contain quotes or whitespace")
        parsed = urlparse(link)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"invalid private-server URL: {link[:80]}")
        if link not in seen:
            clean_links.append(link)
            seen.add(link)
    loop = body.get("loop", True)
    if not isinstance(loop, bool):
        raise ValueError("loop must be true or false")
    cooldown = _required_int(body, "cooldown", 3)
    if cooldown > 86400:
        raise ValueError("cooldown must be 86400 seconds or less")
    config = json.dumps(
        {"links": clean_links, "loop": loop, "cooldown": cooldown},
        separators=(",", ":"),
    )
    return f"rotation_set {hopper} {shlex.quote(config)}"


def build_command(body: dict) -> str:
    """Convert a typed web action into the agent's existing command format."""
    action = str(body.get("action", "")).strip().lower()
    if action == "rotation_set":
        return _rotation_command(body)
    if action in {"start", "stop", "restart"}:
        return f"{action} {_required_int(body, 'hopper')}"
    if action in {"startall", "stopall", "assigns", "continue", "refetch", "pricelog"}:
        return action
    if action == "logs":
        lines = _required_int(body, "lines", 1)
        if lines > 100:
            raise ValueError("lines must be 100 or less")
        return f"logs {_required_int(body, 'hopper')} {lines}"
    if action in {"goto", "goto_pin"}:
        return f"{action} {_required_int(body, 'hopper')} {_required_int(body, 'server')}"
    if action == "assign":
        first = _required_int(body, "first")
        last = _required_int(body, "last")
        if last < first:
            raise ValueError("last must be greater than or equal to first")
        return f"assign {_required_int(body, 'hopper')} {first} {last}"
    if action == "servers":
        return f"servers {_required_int(body, 'hopper')}"
    if action == "link_add":
        return "link_add " + shlex.quote(_required_text(body, "url"))
    if action == "all_goto":
        return "all_goto " + shlex.quote(_required_text(body, "url"))
    if action == "pin":
        return f"pin {_required_int(body, 'hopper')} " + shlex.quote(_required_text(body, "url"))
    if action == "unpin":
        return f"unpin {_required_int(body, 'hopper')}"
    if action == "scripts":
        return "scripts"
    if action in {"script_get", "script_del"}:
        return f"{action} " + shlex.quote(_required_text(body, "name", 120))
    if action == "script_add":
        name = shlex.quote(_required_text(body, "name", 120))
        url = shlex.quote(_required_text(body, "url"))
        return f"script_add {name} {url}"
    if action == "autotrade":
        options = {
            key: body.get(key, False)
            for key in (
                "pets",
                "toys",
                "food",
                "transport",
                "gifts",
                "stickers",
                "pet_accessories",
                "items",
                "usernames",
            )
        }
        if not any(options[key] for key in ("pets", "toys", "food", "transport", "gifts", "stickers", "pet_accessories")) and not str(options["items"]).strip():
            raise ValueError("choose a category or specify items")
        return "autotrade " + json.dumps(options, separators=(",", ":"))
    raise ValueError(f"unsupported action: {action or '(empty)'}")


async def handle_command(request: web.Request) -> web.Response:
    denied = require_web(request)
    if denied:
        return denied
    try:
        body = await request.json()
        phone = validate_phone(str(body.get("phone", "all")))
        command = build_command(body)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)

    result = await run_on(targets(phone), command)
    return web.json_response({"ok": True, "command": command, "result": result})


async def handle_healthz(_: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "hopper-web", "phones": PHONES})


async def handle_index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(ASSET_DIR / "index.html")


async def handle_asset(request: web.Request) -> web.StreamResponse:
    name = Path(request.match_info["name"]).name
    if name != request.match_info["name"]:
        raise web.HTTPNotFound()
    path = ASSET_DIR / name
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path)


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/assets/{name}", handle_asset)
    app.router.add_get("/healthz", handle_healthz)
    app.router.add_post("/api/login", handle_login)
    app.router.add_post("/api/logout", handle_logout)
    app.router.add_get("/api/status", handle_status)
    app.router.add_post("/api/command", handle_command)
    app.router.add_post("/api/{phone}/poll", handle_poll)
    return app


if __name__ == "__main__":
    if KEY.startswith("CHANGE_ME"):
        raise SystemExit("Set KEY in web/config.txt before starting the web server.")
    if WEB_TOKEN.startswith("CHANGE_ME"):
        raise SystemExit("Set WEB_TOKEN in web/config.txt or web/web_token.txt before starting the web server.")
    print(f"[web] dashboard: http://127.0.0.1:{PORT}/")
    print(f"[web] phone poll endpoint: /api/{{phone}}/poll")
    web.run_app(create_app(), host=HOST, port=PORT)
