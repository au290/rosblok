"""
agent.py — phone side. Headless worker that polls the web server for jobs,
launches one Roblox Android package per hopper, and reports status back. It does
not require tmux or dedicated hopperN.lua processes.

Run on the phone (Termux):
    python agent.py

Config: edit the CONFIG block, or drop a config.txt next to this file (gitignored)
with lines like  VPS_URL=https://your.vps:8080  /  KEY=...  /  PHONE=A  /  HOPPERS=1,2,3,4,5
"""

import re
import json
import math
import time
import shlex
import os
import threading
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
from collections import deque

# ─────────────────────────── CONFIG — EDIT THIS ───────────────────────────
VPS_URL  = "http://agent.kqing.web.id" # public web server endpoint
KEY      = "CHANGE_ME_SHARED_SECRET"   # must match server.py KEY
PHONE    = "A"                          # this phone's id ("A" / "B")
HOPPERS  = [1, 2, 3, 4, 5]              # this phone's hoppers
INTERVAL = 2                            # seconds between polls
PLACE_ID = "920587237"                 # fallback when a share URL omits the place
WINDOW_MODE = "auto"                    # detect freeform support, otherwise use Android's default
START_ON_BOOT = False                   # preserve the old explicit-start behavior
AUTO_DETECT_PACKAGES = True             # discover cloned com.roblox.* packages
PACKAGES = []                           # optional ordered package list
PACKAGE_OVERRIDES = {}                  # optional PACKAGE_1=... entries
# ───────────────────────────────────────────────────────────────────────────

# Single home on shared storage, managed by hand in the file manager. Rotation,
# logs, and the optional legacy link files remain device-persistent.
BASE_DIR = Path("/storage/emulated/0/Download")
RUN_DIR  = BASE_DIR
# Executor paths (their own app storage — not the Termux sandbox).
INV_DIR  = Path("/storage/emulated/0/Arceus X/Workspace/inv")
AUTOEXEC = Path("/storage/emulated/0/Arceus X/Autoexecute")

# Swap/Trade addons write one heartbeat file per Roblox account.  Delta normally
# exposes this directory, while cloned Roblox packages may keep an executor
# workspace in their private files directory.  The watcher checks both without
# requiring any addon changes.
TRADE_DIRS = [
    INV_DIR,
    Path("/storage/emulated/0/Delta/Workspace/inv"),
    Path("/storage/emulated/0/Delta/Workspace"),
    BASE_DIR,
    AUTOEXEC,
    Path("/storage/emulated/0/Delta/Autoexecute"),
]
TRADE_STALE_SECONDS = 40
TRADE_LAUNCH_GRACE = 45
TRADE_RETRY_COOLDOWN = 10

DATA_DIR  = RUN_DIR
MAP_FILE  = DATA_DIR / "servers.txt"
POOL_FILE = DATA_DIR / "link.txt"
ROTATION_FILE = DATA_DIR / "rotations.json"
ROTATION_DIR = DATA_DIR / "rotations"
DEFAULT_COOLDOWN = 240

# config.txt (gitignored) overrides the CONFIG block above
_cfg = BASE_DIR / "config.txt"
if _cfg.exists():
    for _line in _cfg.read_text().splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = (s.strip() for s in _line.split("=", 1))
            if   _k == "VPS_URL" and _v: VPS_URL = _v
            elif _k == "KEY"     and _v: KEY = _v
            elif _k == "PHONE"   and _v: PHONE = _v
            elif _k == "HOPPERS" and _v: HOPPERS = [int(x) for x in _v.split(",") if x.strip()]
            elif _k == "PLACE_ID" and _v: PLACE_ID = _v
            elif _k == "WINDOW_MODE" and _v:
                if _v.lower() in {"auto", "default"}:
                    WINDOW_MODE = "auto"
                elif _v.lower() in {"none", "off"}:
                    WINDOW_MODE = None
                else:
                    try: WINDOW_MODE = int(_v)
                    except ValueError: pass
            elif _k == "START_ON_BOOT": START_ON_BOOT = _v.lower() in {"1", "true", "yes", "on"}
            elif _k == "AUTO_DETECT_PACKAGES": AUTO_DETECT_PACKAGES = _v.lower() in {"1", "true", "yes", "on"}
            elif _k == "PACKAGES": PACKAGES = [x.strip() for x in _v.split(",") if x.strip()]
            elif _k == "TRADE_DIRS":
                TRADE_DIRS = [Path(x.strip()) for x in _v.split(",") if x.strip()]
            elif _k == "TRADE_STALE_SECONDS":
                try: TRADE_STALE_SECONDS = max(10, int(_v))
                except ValueError: pass
            elif _k == "TRADE_LAUNCH_GRACE":
                try: TRADE_LAUNCH_GRACE = max(5, int(_v))
                except ValueError: pass
            elif _k.startswith("PACKAGE_"):
                try:
                    PACKAGE_OVERRIDES[int(_k[8:])] = _v
                except ValueError:
                    pass

VPS_URL = VPS_URL.rstrip("/")


# ─────────────────────────── direct Android hopper control ───────────────────
_RUNTIME_LOCK = threading.RLock()
_RUNTIMES = {}
_DETECTED_PACKAGES = None
_WINDOW_MODE_READY = False
_DETECTED_WINDOW_MODE = None
_TRADE_FILE_CACHE: dict[str, tuple[float, list[Path]]] = {}
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9_.]+$")
_ACCOUNT_LOCK = threading.Lock()
_ACCOUNT_CACHE: dict[str, tuple[float, str]] = {}
_USER_ID_CACHE: dict[str, str] = {}
ACCOUNT_CACHE_TTL = 60


def detect_packages(refresh: bool = False) -> list[str]:
    """Return installed Roblox clone package IDs, if root/pm is available."""
    global _DETECTED_PACKAGES
    if _DETECTED_PACKAGES is not None and not refresh:
        return list(_DETECTED_PACKAGES)
    found = []
    try:
        result = subprocess.run(
            ["su", "-c", "pm list packages"], capture_output=True, text=True,
            errors="replace", timeout=8,
        )
        for line in result.stdout.splitlines():
            package = line.strip().removeprefix("package:")
            if "roblox" in package.lower() and _PACKAGE_RE.fullmatch(package):
                found.append(package)
    except (OSError, subprocess.SubprocessError):
        pass
    _DETECTED_PACKAGES = sorted(set(found))
    return list(_DETECTED_PACKAGES)


def package_for(n: int, refresh: bool = False) -> str:
    package = PACKAGE_OVERRIDES.get(n)
    if package:
        return package
    try:
        slot = HOPPERS.index(n)
    except ValueError:
        slot = n - 1
    configured = [p for p in PACKAGES if _PACKAGE_RE.fullmatch(p)]
    if 0 <= slot < len(configured):
        return configured[slot]
    detected = detect_packages(refresh=refresh) if AUTO_DETECT_PACKAGES else []
    if 0 <= slot < len(detected):
        return detected[slot]
    return ""


def resolved_window_mode() -> int | None:
    """Use freeform when Android advertises it; otherwise let Android decide."""
    global _WINDOW_MODE_READY, _DETECTED_WINDOW_MODE
    if isinstance(WINDOW_MODE, int):
        return WINDOW_MODE if WINDOW_MODE >= 0 else None
    if WINDOW_MODE is None or str(WINDOW_MODE).lower() != "auto":
        return None
    if _WINDOW_MODE_READY:
        return _DETECTED_WINDOW_MODE

    feature = _su("pm has-feature android.software.freeform_window_management", timeout=8)
    setting = _su("settings get global enable_freeform_support", timeout=8)
    has_feature = bool(
        feature and feature.returncode == 0
        and feature.stdout.strip().lower() in {"1", "true", "yes"}
    )
    enabled = bool(setting and setting.returncode == 0 and setting.stdout.strip() == "1")
    _DETECTED_WINDOW_MODE = 5 if has_feature or enabled else None
    _WINDOW_MODE_READY = True
    return _DETECTED_WINDOW_MODE


def _runtime(n: int) -> dict:
    with _RUNTIME_LOCK:
        state = _RUNTIMES.get(n)
        if state is None:
            state = {
                "hopper": n, "package": package_for(n), "desired": False,
                "actual": False, "held": False, "index": None,
                "link": "", "deep_link": "", "next_at": 0.0,
                "last_launch": 0.0, "last_health": 0.0,
                "trade": None, "trade_launch_at": 0.0,
                "trade_retry_at": 0.0, "trade_file": "",
                "logs": deque(maxlen=80),
            }
            log_file = RUN_DIR / f"hopper{n}.log"
            try:
                state["logs"].extend(log_file.read_text(errors="replace").splitlines()[-80:])
            except OSError:
                pass
            _RUNTIMES[n] = state
        return state


def _log(state: dict, message: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] [{state['package'] or 'unassigned'}] {message}"
    state["logs"].append(line)
    try:
        with (RUN_DIR / f"hopper{state['hopper']}.log").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass
    print(f"[hopper{state['hopper']}] {message}", flush=True)


def _su(command: str, timeout: int = 15):
    try:
        return subprocess.run(["su", "-c", command], capture_output=True, text=True,
                              errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def app_running(package: str) -> bool:
    if not _PACKAGE_RE.fullmatch(package):
        return False
    result = _su(f"pidof {shlex.quote(package)}", timeout=8)
    return bool(result and result.returncode == 0 and result.stdout.strip())


_TRADE_FILE_RE = re.compile(r"^[^/\\]+_winteraddons\.json$", re.I)


def _trade_dirs_for(state: dict) -> list[Path]:
    """Return package-private locations first, then shared executor folders."""
    dirs: list[Path] = []
    package = state.get("package", "")
    if _PACKAGE_RE.fullmatch(package or ""):
        for root in (Path("/data/data"), Path("/data/user/0"),
                     Path("/storage/emulated/0/Android/data")):
            dirs.extend((root / package / "files" / name for name in (
                "", "Workspace", "workspace", "Delta/Workspace", "Delta/workspace",
            )))
    dirs.extend(Path(item) for item in TRADE_DIRS)
    result: list[Path] = []
    seen: set[str] = set()
    for directory in dirs:
        key = str(directory)
        if key not in seen:
            seen.add(key)
            result.append(directory)
    return result


def _trade_files(directory: Path) -> list[Path]:
    """List heartbeat files even when Termux needs root to read the folder."""
    cache_key = str(directory)
    cached = _TRADE_FILE_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < 10:
        return list(cached[1])
    found: list[Path] = []
    try:
        found.extend(path for path in directory.glob("*_winteraddons.json")
                     if path.is_file() and _TRADE_FILE_RE.fullmatch(path.name))
    except OSError:
        pass
    if found:
        _TRADE_FILE_CACHE[cache_key] = (time.monotonic(), found)
        return found
    result = _su(
        f"find {shlex.quote(str(directory))} -maxdepth 1 -type f "
        "-name '*_winteraddons.json' -print",
        timeout=8,
    )
    if result and result.returncode == 0:
        for raw in result.stdout.splitlines():
            path = Path(raw.strip())
            if _TRADE_FILE_RE.fullmatch(path.name):
                found.append(path)
    _TRADE_FILE_CACHE[cache_key] = (time.monotonic(), found)
    return found


def _read_trade_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        result = _su(f"cat {shlex.quote(str(path))}", timeout=8)
        if result and result.returncode == 0:
            return result.stdout
    return None


def _normalise_trade(raw: object, path: Path, now: float) -> dict | None:
    if not isinstance(raw, dict):
        return None
    status = raw.get("status")
    if not isinstance(status, str) or not status.strip():
        return None
    try:
        timestamp = float(raw.get("ts"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(timestamp):
        return None
    age = max(0.0, now - timestamp)
    result = {
        "status": status.strip().lower()[:40],
        "ts": int(timestamp) if timestamp.is_integer() else timestamp,
        "age": round(age, 1),
        "fresh": age <= TRADE_STALE_SECONDS,
        "file": path.name,
    }
    if "count" in raw:
        try:
            count = float(raw["count"])
            if math.isfinite(count) and count >= 0:
                result["count"] = int(count) if count.is_integer() else count
        except (TypeError, ValueError):
            pass
    items = raw.get("items")
    if isinstance(items, list):
        clean_items = []
        for item in items[:100]:
            if not isinstance(item, dict) or not str(item.get("name", "")).strip():
                continue
            clean = {"name": str(item["name"]).strip()[:160]}
            try:
                qty = float(item.get("qty", 0))
                if math.isfinite(qty) and qty >= 0:
                    clean["qty"] = int(qty) if qty.is_integer() else qty
            except (TypeError, ValueError):
                clean["qty"] = 0
            clean_items.append(clean)
        result["items"] = clean_items
    meta = raw.get("meta")
    if isinstance(meta, dict):
        clean_meta = {}
        for key, value in list(meta.items())[:40]:
            key = str(key).strip()[:80]
            if not key:
                continue
            if key == "categories" and isinstance(value, dict):
                categories = {}
                for name, count in list(value.items())[:40]:
                    try:
                        count = float(count)
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(count) and count >= 0:
                        categories[str(name).strip()[:80]] = int(count) if count.is_integer() else count
                clean_meta[key] = categories
                continue
            if isinstance(value, (dict, list)):
                continue
            if isinstance(value, (str, int, float, bool)):
                if isinstance(value, float) and not math.isfinite(value):
                    continue
                clean_meta[key] = str(value)[:160] if isinstance(value, str) else value
        result["meta"] = clean_meta
    return result


def read_trade_status(state: dict, now: float | None = None) -> dict | None:
    """Read the newest valid heartbeat for this hopper without persisting it."""
    now = time.time() if now is None else now
    directories = _trade_dirs_for(state)
    package_files: list[Path] = []
    shared_files: list[Path] = []
    shared_roots = {str(Path(item)) for item in TRADE_DIRS}
    for directory in directories:
        files = _trade_files(directory)
        if str(directory) in shared_roots:
            shared_files.extend(files)
        else:
            package_files.extend(files)
    files = package_files or shared_files
    candidates: list[tuple[dict, Path]] = []
    seen: set[str] = set()
    for path in files:
        if str(path) in seen:
            continue
        seen.add(str(path))
        text = _read_trade_text(path)
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        status = _normalise_trade(parsed, path, now)
        if status:
            candidates.append((status, path))
    if not candidates:
        return None
    current_file = state.get("trade_file", "")
    for status, path in candidates:
        if current_file and str(path) == current_file:
            return status
    assigned = {
        other.get("trade_file", "")
        for other in _RUNTIMES.values()
        if other is not state and other.get("desired")
    }
    available = [item for item in candidates if str(item[1]) not in assigned] or candidates
    status, path = max(available, key=lambda item: float(item[0].get("ts", 0)))
    state["trade_file"] = str(path)
    return status


def _refresh_trade_locked(state: dict, now: float | None = None) -> dict | None:
    trade = read_trade_status(state, now)
    launched = state.get("trade_launch_at") or state.get("last_launch") or 0
    # A heartbeat from before this launch belongs to the server we just left.
    # os.time() is second-resolution, so a new session may need one heartbeat
    # interval before its timestamp becomes newer than the Python launch time.
    if trade is not None and launched and float(trade.get("ts", 0)) < launched:
        trade = dict(trade)
        trade["fresh"] = False
        trade["previous_session"] = True
    state["trade"] = trade
    return trade


def _trade_needs_retry(state: dict, trade: dict | None, now: float) -> bool:
    if now < state.get("trade_retry_at", 0):
        return False
    if trade and trade.get("fresh"):
        return str(trade.get("status", "")).lower() in {"disconnected", "error"}
    launched = state.get("trade_launch_at") or state.get("last_launch") or now
    return now - launched >= TRADE_LAUNCH_GRACE


def parse_private_server(link: str) -> tuple[str, str]:
    """Extract a place and private-server code from Roblox/share URLs."""
    raw = str(link or "").strip()
    parsed = urlparse(raw)
    query = parse_qs(parsed.query, keep_blank_values=True)
    place_match = re.search(r"/(?:games|place)/(\d+)", parsed.path, re.I)
    place = (place_match.group(1) if place_match else "")
    for key in ("placeId", "placeid", "place_id"):
        if query.get(key):
            place = query[key][0]
            break
    code = ""
    for key in ("privateServerLinkCode", "linkCode", "code"):
        if query.get(key):
            code = query[key][0]
            break
    if not place:
        place = str(PLACE_ID).strip()
    if not place or not re.fullmatch(r"\d+", place):
        raise ValueError("Roblox link is missing a numeric place ID")
    if not code:
        raise ValueError("Roblox link is missing a private-server link code")
    return place, code


def deep_link(link: str) -> str:
    raw = str(link or "").strip()
    if raw.lower().startswith("roblox://"):
        # Roblox deep links use '&' directly after the scheme, without '?'.
        place_match = re.search(r"(?:^|[?&/])placeId=(\d+)", raw, re.I)
        code_match = re.search(r"(?:^|[?&])(?:linkCode|privateServerLinkCode|code)=([A-Za-z0-9_-]+)", raw, re.I)
        place = place_match.group(1) if place_match else str(PLACE_ID)
        code = unquote(code_match.group(1)) if code_match else ""
        if place and code:
            return f"roblox://placeId={quote(place, safe='')}&linkCode={quote(code, safe='')}"
    place, code = parse_private_server(raw)
    return f"roblox://placeId={quote(place, safe='')}&linkCode={quote(code, safe='')}"


def _launch_locked(state: dict, link: str, index: int | None = None, label: str = "") -> None:
    target = deep_link(link)
    package = state["package"]
    if not _PACKAGE_RE.fullmatch(package or ""):
        package = package_for(state["hopper"], refresh=True)
        state["package"] = package
    if not _PACKAGE_RE.fullmatch(package or ""):
        detected = detect_packages() if AUTO_DETECT_PACKAGES else []
        raise ValueError(
            f"no installed Roblox package detected for hopper{state['hopper']} "
            f"({len(detected)} package(s) found for {len(HOPPERS)} hopper(s))"
        )
    for other in _RUNTIMES.values():
        if other is not state and other.get("desired") and other.get("package") == package:
            raise ValueError(f"{package} is already assigned to hopper{other['hopper']}")
    stopped = _su(f"am force-stop {shlex.quote(package)}", timeout=15)
    if stopped is None:
        raise RuntimeError("su is unavailable")
    if stopped.returncode == 0:
        state["actual"] = False
    time.sleep(0.5)
    command = (f"am start -a android.intent.action.VIEW -d {shlex.quote(target)} "
               f"-p {shlex.quote(package)}")
    window_mode = resolved_window_mode()
    if window_mode is not None:
        command += f" --windowingMode {window_mode}"
    result = _su(command, timeout=20)
    if result is None:
        raise RuntimeError("su is unavailable")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "am start failed").strip())
    launched_at = time.time()
    state.update({"actual": True, "link": str(link).strip(), "deep_link": target,
                  "last_launch": launched_at, "last_health": launched_at,
                  "trade": None, "trade_launch_at": launched_at,
                  "trade_retry_at": 0.0})
    if index is not None:
        state["index"] = index
    rotation = ensure_rotations().get(str(state["hopper"]), {})
    state["next_at"] = time.time() + int(rotation.get("cooldown", DEFAULT_COOLDOWN))
    _log(state, f"Launching {label or ('RF' + str((state['index'] or 0) + 1))}: {target}")


def _launch_index_locked(state: dict, index: int) -> None:
    links = hopper_links(state["hopper"])
    if not links:
        raise ValueError(f"hopper{state['hopper']} has no saved servers")
    if not 0 <= index < len(links):
        raise ValueError(f"hopper{state['hopper']} has no RF{index + 1} (has RF1..RF{len(links)})")
    _launch_locked(state, links[index], index=index)


def is_running(n: int) -> bool:
    return bool(_runtime(n).get("actual"))


def start_hopper(n: int) -> str:
    state = _runtime(n)
    with _RUNTIME_LOCK:
        if state["desired"] and (state["actual"] or time.time() - state["last_launch"] < 5):
            return f"hopper{n} already running ({state['package']})"
        links = hopper_links(n)
        if not links:
            return f"hopper{n} has no saved servers"
        previous_desired = state["desired"]
        previous_held = state["held"]
        state["desired"] = True
        state["held"] = False
        index = state["index"] if state["index"] is not None and state["index"] < len(links) else 0
        try:
            _launch_index_locked(state, index)
        except Exception as exc:
            state["desired"] = previous_desired
            state["held"] = previous_held
            _log(state, f"launch failed: {exc}")
            return f"hopper{n} launch failed: {exc}"
    return f"started hopper{n} ({state['package']})"


def stop_hopper(n: int) -> str:
    state = _runtime(n)
    with _RUNTIME_LOCK:
        was_running = state["desired"] or state["actual"]
        state["desired"] = False
        state["held"] = False
        package = state["package"]
        _su(f"am force-stop {shlex.quote(package)}", timeout=15)
        state["actual"] = False
        state["next_at"] = 0
        state["trade"] = None
        state["trade_launch_at"] = 0
        state["trade_retry_at"] = 0
        _log(state, "stopped")
    return f"stopped hopper{n}" if was_running else f"hopper{n} not started"


def pane_tail(n: int, lines: int = 1) -> str:
    state = _runtime(n)
    with _RUNTIME_LOCK:
        rows = list(state["logs"])[-max(1, lines):]
    return "\n".join(rows) if rows else "—"


def tick_hoppers() -> None:
    """Drive each rotation from the addon's trade heartbeat."""
    now = time.time()
    with _RUNTIME_LOCK:
        for n in HOPPERS:
            state = _runtime(n)
            if not state["desired"]:
                continue
            links = hopper_links(n)
            if not links:
                if state["actual"]:
                    _su(f"am force-stop {shlex.quote(state['package'])}", timeout=15)
                state["desired"] = False
                state["actual"] = False
                state["index"] = None
                _log(state, "stopped: rotation has no servers")
                continue
            try:
                trade = _refresh_trade_locked(state, now)
                if state["held"]:
                    if _trade_needs_retry(state, trade, now):
                        reason = "no script" if trade is None else (
                            "stale script" if not trade.get("fresh") else trade.get("status", "error")
                        )
                        state["trade_retry_at"] = now + TRADE_RETRY_COOLDOWN
                        _log(state, f"{reason}; relaunching pinned server")
                        _launch_locked(state, state["link"], index=state.get("index"), label="PIN")
                    elif now - state["last_health"] >= 5 and now - state["last_launch"] >= 5:
                        state["actual"] = app_running(state["package"])
                        state["last_health"] = now
                        if not state["actual"]:
                            _launch_locked(state, state["link"], label="PIN")
                    continue
                if state["index"] is None:
                    _launch_index_locked(state, 0)
                    continue

                status = str((trade or {}).get("status", "")).lower()
                if trade and trade.get("fresh") and status == "completed":
                    rotation = _normalise_rotation(ensure_rotations().get(str(n), {}))
                    next_index = state["index"] + 1
                    if next_index >= len(links):
                        if not rotation["loop"]:
                            state["desired"] = False
                            state["actual"] = False
                            _su(f"am force-stop {shlex.quote(state['package'])}", timeout=15)
                            _log(state, "rotation complete")
                            continue
                        next_index = 0
                    _log(state, f"trade completed; advancing to RF{next_index + 1}")
                    _launch_index_locked(state, next_index)
                elif _trade_needs_retry(state, trade, now):
                    reason = "no script" if trade is None else (
                        "stale script" if not trade.get("fresh") else status
                    )
                    state["trade_retry_at"] = now + TRADE_RETRY_COOLDOWN
                    _log(state, f"{reason}; relaunching RF{state['index'] + 1}")
                    _launch_index_locked(state, state["index"])
                elif now - state["last_health"] >= 5 and now - state["last_launch"] >= 5:
                    state["actual"] = app_running(state["package"])
                    state["last_health"] = now
                    if not state["actual"]:
                        _launch_index_locked(state, state["index"] or 0)
            except Exception as exc:
                state["actual"] = False
                _log(state, f"runtime error: {exc}")


def runtime_worker() -> None:
    while True:
        try:
            tick_hoppers()
        except Exception as exc:
            print(f"[agent {PHONE}] hopper worker error: {exc}", flush=True)
        time.sleep(2)


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
    if ROTATION_FILE.exists():
        current = load_rotations().get(str(n), {})
        current["links"] = pool()[first - 1:last]
        set_rotation(n, current)


def legacy_hopper_links(n: int) -> list:
    first, last = ranges().get(n, (1, 0))
    return pool()[first - 1:last]


def _normalise_rotation(value) -> dict:
    if not isinstance(value, dict):
        value = {}
    links = value.get("links", [])
    if not isinstance(links, list):
        links = []
    links = [str(link).strip() for link in links if str(link).strip()]
    try:
        cooldown = int(value.get("cooldown", DEFAULT_COOLDOWN))
    except (TypeError, ValueError):
        cooldown = DEFAULT_COOLDOWN
    cooldown = max(3, min(cooldown, 86400))
    return {"links": links[:100], "loop": bool(value.get("loop", True)), "cooldown": cooldown}


def load_rotations() -> dict:
    if not ROTATION_FILE.exists():
        return {}
    try:
        raw = json.loads(ROTATION_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): _normalise_rotation(value) for key, value in raw.items()}


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _write_rotation_files(rotations: dict) -> None:
    ROTATION_DIR.mkdir(parents=True, exist_ok=True)
    for key, value in rotations.items():
        try:
            hopper = int(key)
        except (TypeError, ValueError):
            continue
        data = _normalise_rotation(value)
        header = f"# cooldown={data['cooldown']} loop={'true' if data['loop'] else 'false'}\n"
        body = header + "".join(f"{link}\n" for link in data["links"])
        _atomic_write(ROTATION_DIR / f"h{hopper}.txt", body)


def _rebuild_legacy_files(rotations: dict) -> None:
    """Keep old hopper scripts working while the device upgrades to rotation files."""
    links: list[str] = []
    ranges_body = ["# hopper : firstLink-lastLink  (line numbers in link.txt, 1-based)"]
    keys = sorted((int(key), value) for key, value in rotations.items() if str(key).isdigit())
    for hopper, value in keys:
        hopper_links = _normalise_rotation(value)["links"]
        first = len(links) + 1
        links.extend(hopper_links)
        ranges_body.append(f"{hopper}: {first}-{len(links)}")
    _atomic_write(POOL_FILE, "".join(f"{link}\n" for link in links))
    _atomic_write(MAP_FILE, "\n".join(ranges_body) + "\n")


def ensure_rotations() -> dict:
    """Migrate the legacy pool once, then keep all rotation state on-device."""
    rotations = load_rotations()
    if rotations:
        runtime_missing = any(not (ROTATION_DIR / f"h{key}.txt").exists() for key in rotations)
        if runtime_missing or not POOL_FILE.exists() or not MAP_FILE.exists():
            try:
                _write_rotation_files(rotations)
                _rebuild_legacy_files(rotations)
            except OSError:
                pass
        return rotations
    rotations = {
        str(n): {"links": legacy_hopper_links(n), "loop": True, "cooldown": DEFAULT_COOLDOWN}
        for n in HOPPERS
    }
    try:
        _atomic_write(ROTATION_FILE, json.dumps(rotations, separators=(",", ":"), sort_keys=True))
        _write_rotation_files(rotations)
        _rebuild_legacy_files(rotations)
    except OSError:
        # The agent can still operate with the legacy files while storage is unavailable.
        pass
    return rotations


def rotation_snapshot() -> dict:
    return ensure_rotations()


def hopper_links(n: int) -> list:
    rotation = ensure_rotations().get(str(n))
    if rotation is not None:
        return list(rotation.get("links", []))
    return legacy_hopper_links(n)


def set_rotation(n: int, value: dict) -> str:
    if n < 1:
        raise ValueError("hopper must be at least 1")
    rotations = ensure_rotations()
    rotations[str(n)] = _normalise_rotation(value)
    _atomic_write(ROTATION_FILE, json.dumps(rotations, separators=(",", ":"), sort_keys=True))
    _write_rotation_files({str(n): rotations[str(n)]})
    _rebuild_legacy_files(rotations)
    links = rotations[str(n)]["links"]
    state = _RUNTIMES.get(n)
    if state is not None:
        with _RUNTIME_LOCK:
            current = state.get("link", "")
            if current in links:
                state["index"] = links.index(current)
                state["next_at"] = time.time() + rotations[str(n)]["cooldown"]
            else:
                state["index"] = None
                if state.get("desired") and not state.get("held"):
                    state["next_at"] = 0
    mode = "looping" if rotations[str(n)]["loop"] else "one-shot"
    return f"saved hopper{n} rotation: {len(links)} server(s), {mode}, {rotations[str(n)]['cooldown']}s cooldown"


def goto_hopper(n: int, server: int, hold: bool = False) -> str:
    state = _runtime(n)
    links = hopper_links(n)
    if not (1 <= server <= len(links)):
        return f"hopper{n} has no RF{server} (has RF1..RF{len(links)})"
    with _RUNTIME_LOCK:
        previous_desired = state["desired"]
        previous_held = state["held"]
        state["desired"] = True
        state["held"] = hold
        try:
            _launch_index_locked(state, server - 1)
        except Exception as exc:
            state["desired"] = previous_desired
            state["held"] = previous_held
            return f"hopper{n} launch failed: {exc}"
    if hold:
        return f"hopper{n} pinned to RF{server}, holding (/continue to release)"
    return f"hopper{n} → RF{server}"


def pin_hopper(n: int, link: str) -> str:
    state = _runtime(n)
    link = str(link).strip()
    if not link:
        return f"hopper{n} pin link is empty"
    with _RUNTIME_LOCK:
        previous_desired = state["desired"]
        previous_held = state["held"]
        state["desired"] = True
        state["held"] = True
        try:
            _launch_locked(state, link, index=state.get("index"), label="PIN")
        except Exception as exc:
            state["desired"] = previous_desired
            state["held"] = previous_held
            return f"hopper{n} launch failed: {exc}"
    return f"hopper{n} pinned to that link, holding (/unpin {n} or /continue; link not saved)"


def release_hopper(n: int) -> str:
    state = _runtime(n)
    with _RUNTIME_LOCK:
        state["held"] = False
        links = hopper_links(n)
        if state["desired"] and links:
            index = state["index"] if state["index"] is not None and state["index"] < len(links) else 0
            try:
                _launch_index_locked(state, index)
            except Exception as exc:
                state["actual"] = False
                return f"hopper{n} resume failed: {exc}"
    return f"hopper{n} released, resuming rotation"


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
        state = _runtime(n)
        if not state["desired"]:
            rows.append(f"{n:>2}  {'—':<5} stopped")
            continue
        if state["actual"]:
            up += 1
        if state["held"]:
            now.append(f"{n}:PIN")
            rows.append(f"{n:>2}  📌    held")
            continue
        index = state.get("index")
        links = hopper_links(n)
        srv = f"RF{index + 1}" if index is not None and index < len(links) else None
        now.append(f"{n}:{srv or '?'}")
        rotation = _normalise_rotation(ensure_rotations().get(str(n), {}))
        total = rotation["cooldown"] if srv else 0
        elapsed = max(0, min(total, int(time.time() - state.get("last_launch", time.time())))) if total else 0
        prog = f"{_bar(elapsed, total)} {elapsed:>3}/{total}s" if total else "starting…"
        rows.append(f"{n:>2}  {srv or '??':<5} {prog}")
    board  = "```\n #  srv   progress\n" + "\n".join(rows) + "\n```"
    footer = f"{device_health()} · {up}/{len(HOPPERS)} running"
    return board, footer, now


def trade_snapshot() -> dict:
    """Return current validated trade data for the live poll only."""
    now = time.time()
    snapshot = {}
    with _RUNTIME_LOCK:
        for n in HOPPERS:
            state = _runtime(n)
            if not state.get("desired"):
                snapshot[str(n)] = {"status": "stopped", "fresh": False}
                continue
            trade = _refresh_trade_locked(state, now)
            if trade is None:
                launched = state.get("trade_launch_at") or state.get("last_launch") or now
                grace_left = max(0, math.ceil(TRADE_LAUNCH_GRACE - (now - launched)))
                snapshot[str(n)] = {
                    "status": "no script",
                    "fresh": False,
                    "grace": grace_left,
                }
            else:
                snapshot[str(n)] = dict(trade)
    return snapshot


def trade_account_name(trade: dict | None) -> str:
    """Extract the Roblox account name from its heartbeat filename."""
    filename = str((trade or {}).get("file", ""))
    match = re.fullmatch(r"([^/\\]+)_winteraddons\.json", filename, re.I)
    return match.group(1) if match else ""


def parse_package_identity(xml_text: str) -> tuple[str, str]:
    """Return ``(username, user_id)`` from Roblox's package preferences."""
    try:
        root = ET.fromstring(xml_text)
    except (ET.ParseError, TypeError, ValueError):
        return "", ""
    username = ""
    user_id = ""
    for item in root:
        key = str(item.attrib.get("name", "")).strip().lower()
        if key == "username":
            value = str(item.text or item.attrib.get("value", "")).strip()
            if re.fullmatch(r"[A-Za-z0-9_]{3,20}", value):
                username = value
        elif key in {"userid_long", "userid", "user_id"}:
            value = str(item.attrib.get("value", item.text or "")).strip()
            if re.fullmatch(r"[1-9]\d{0,19}", value):
                user_id = value
    return username, user_id


def _package_preferences(package: str) -> str | None:
    """Read prefs.xml through root without touching the WebView cookie DB."""
    if not _PACKAGE_RE.fullmatch(package or ""):
        return None
    for root in ("/data/data", "/data/user/0"):
        path = f"{root}/{package}/shared_prefs/prefs.xml"
        result = _su(f"cat {shlex.quote(path)}", timeout=8)
        if result and result.returncode == 0 and result.stdout.strip():
            return result.stdout
    return None


def _username_for_user_id(user_id: str) -> str:
    with _ACCOUNT_LOCK:
        cached = _USER_ID_CACHE.get(user_id, "")
    if cached:
        return cached
    username = ""
    try:
        request = urllib.request.Request(
            f"https://users.roblox.com/v1/users/{quote(user_id, safe='')}",
            headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 (hopperbot)"},
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            payload = json.load(response)
        value = payload.get("name") if isinstance(payload, dict) else None
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_]{3,20}", value.strip()):
            username = value.strip()
    except Exception:
        pass
    if username:
        with _ACCOUNT_LOCK:
            _USER_ID_CACHE[user_id] = username
    return username


def package_account_name(package: str, refresh: bool = False) -> str:
    """Return the account currently stored by one installed Roblox package."""
    if not _PACKAGE_RE.fullmatch(package or ""):
        return ""
    now = time.monotonic()
    with _ACCOUNT_LOCK:
        cached = _ACCOUNT_CACHE.get(package)
        if cached and not refresh and now - cached[0] < ACCOUNT_CACHE_TTL:
            return cached[1]
    xml_text = _package_preferences(package)
    if xml_text is None:
        # Keep a previously detected name through a temporary root/read error.
        username = cached[1] if cached else ""
    else:
        username, user_id = parse_package_identity(xml_text)
        if not username and user_id:
            username = _username_for_user_id(user_id)
    with _ACCOUNT_LOCK:
        _ACCOUNT_CACHE[package] = (time.monotonic(), username)
    return username


def package_account_snapshot() -> dict[str, str]:
    accounts = {}
    for n in HOPPERS:
        package = _runtime(n).get("package", "")
        with _ACCOUNT_LOCK:
            cached = _ACCOUNT_CACHE.get(package)
        username = cached[1] if cached else ""
        if username:
            accounts[str(n)] = username
    return accounts


def account_worker() -> None:
    """Refresh package identities without delaying the VPS poll heartbeat."""
    while True:
        for n in HOPPERS:
            try:
                package = _runtime(n).get("package", "")
                package_account_name(package, refresh=True)
            except Exception:
                pass
        time.sleep(ACCOUNT_CACHE_TTL)


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
SP_WORD_ALIASES = {    # word-level remap: Adopt Me names a pet-word differently than StarPets.
                       # Applied per-word on the year-stripped name (so acorn_wizard -> oakee_wizard).
    "acorn": "oakee",  # summer_2026_acorn_wizard on AM = oakee_wizard on StarPets
}
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
    stripped = "_".join(SP_WORD_ALIASES.get(w, w) for w in stripped.split("_"))  # remap per word
    # match on the name with underscores stripped: Adopt Me's kind and StarPets' realName
    # split words differently (dragonfruit_fox vs dragon_fruit_fox; frostbite_bear vs frostbitebear)
    norm = lambda s: (s or "").replace("_", "").lower()
    names = {norm(real_name), norm(stripped)}
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
        if last_err is None:                            # 200 OK but StarPets returned nothing
            _plog(f"{real_name}: no listings (search='{stripped}')")
        else:
            _plog(f"{real_name}: fetch failed ({last_err})")
        return None, None
    matches = [it for it in items if norm(it.get("realName")) in names
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
            # value rule (4 neons = 1 mega): normals priced at default, neon+mega at mega_neon
            need = set()
            for k in keys:
                rn, pump = _key_variant(k)
                need.add((rn, "default" if pump == "default" else "mega_neon"))
            now = time.time()
            todo = [(rn, vv) for rn, vv in need if now - PRICE_TS.get(f"{rn}|{vv}", 0) > PRICE_TTL]
            _plog(f"scan: {len(keys)} kinds, {len(need)} price-keys, {len(todo)} to (re)fetch, {len(PRICES)} cached")
            ok, changed = 0, False
            for rn, vv in todo:
                pk = f"{rn}|{vv}"
                price, rarity = _sp_floor(rn, vv)
                PRICE_TS[pk] = time.time()              # mark attempted (success or fail) so it respects TTL
                if price is not None:
                    PRICES[pk] = price; ok += 1; changed = True
                    if rarity:
                        RARITIES[rn] = rarity
                    _plog(f"{pk} = ${price} ({rarity})")
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
    if v == "rotation_set":
        if len(a) != 2:
            raise ValueError("rotation_set requires hopper and config")
        return set_rotation(int(a[0]), json.loads(a[1]))
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
    if v == "stopall":   return "\n".join(stop_hopper(n) for n in HOPPERS)
    if v == "goto":      return goto_hopper(int(a[0]), int(a[1]))
    if v == "goto_pin":
        return goto_hopper(int(a[0]), int(a[1]), hold=True)
    if v == "pin":                                       # paste a link -> pin ONE hopper (link not saved)
        return pin_hopper(int(a[0]), a[1])
    if v == "unpin":
        return release_hopper(int(a[0]))
    if v == "all_goto":
        return "\n".join(pin_hopper(n, a[0]) for n in HOPPERS)
    if v == "continue":
        return "\n".join(release_hopper(n) for n in HOPPERS)
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
    packages = {
        str(n): _runtime(n)["package"] for n in HOPPERS
        if _runtime(n)["package"]
    }
    trades = trade_snapshot()
    accounts = {
        number: account
        for number, trade in trades.items()
        if (account := trade_account_name(trade))
    }
    # prefs.xml is tied directly to the package assigned to this hopper, so it
    # is more reliable than a shared-workspace heartbeat filename.
    accounts.update(package_account_snapshot())
    body = json.dumps({"board": board, "footer": footer, "inv": read_inv(),
                       "servers": servers, "srv_now": now, "packages": packages, "prices": PRICES,
                       "rarities": RARITIES, "rotations": rotation_snapshot(),
                       "trades": trades, "accounts": accounts,
                       "results": results}).encode()
    req = urllib.request.Request(f"{VPS_URL}/api/{PHONE}/poll", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-Key": KEY,
                                          "User-Agent": "Mozilla/5.0 (hopperbot)"})  # dodge Cloudflare's Python-urllib ban (err 1010)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode()).get("jobs", [])


def main():
    print(f"[agent {PHONE}] polling {VPS_URL} every {INTERVAL}s")
    detected = detect_packages() if AUTO_DETECT_PACKAGES else []
    if AUTO_DETECT_PACKAGES:
        print(f"[agent {PHONE}] detected {len(detected)} Roblox package(s): {', '.join(detected) or 'none'}", flush=True)
    mode = resolved_window_mode()
    mode_label = f"windowingMode {mode}" if mode is not None else "Android default"
    print(f"[agent {PHONE}] window mode: {mode_label}", flush=True)
    for n in HOPPERS:
        state = _runtime(n)
        print(f"[agent {PHONE}] hopper{n} -> {state['package'] or 'NOT DETECTED'}", flush=True)
    threading.Thread(target=runtime_worker, daemon=True).start()
    threading.Thread(target=account_worker, daemon=True).start()
    if START_ON_BOOT:
        for n in HOPPERS:
            print(f"[agent {PHONE}] {start_hopper(n)}", flush=True)
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
