"""Small development phone simulator for the web dashboard.

The real ``agent.py`` runs on an Android device.  This script speaks the same
poll protocol from a desktop so the dashboard can be exercised without a
phone, Roblox, tmux, or an executor.  It keeps all simulated state in memory;
restart the script to reset it.

Run from the repository root with ``python web/seeder.py`` or from ``web``
with ``python seeder.py``.  The script deliberately has no third-party
dependencies.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INTERVAL = 2.0
DEFAULT_HOPPERS = 5

# Names use the same keys as monitor_adoptme.lua.  The web server strips the
# year prefix for display and uses these exact keys for its price lookup.
PET_DATA = (
    ("2026_dog", 5, 2, "common", 1.25),
    ("2026_cat (neon)", 2, 1, "rare", 12.50),
    ("2026_dragon", 1, 0, "legendary", 28.00),
    ("2026_bunny", 7, 5, "uncommon", 2.75),
    ("2026_owl (mega neon)", 1, 1, "ultra", 85.00),
)


def read_config() -> dict[str, str]:
    """Read the web server config without importing server.py."""
    values: dict[str, str] = {}
    config = BASE_DIR / "config.txt"
    if not config.exists():
        return values
    try:
        lines = config.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        print(f"[seeder] could not read {config}: {exc}", file=sys.stderr)
        return values
    for raw in lines:
        if "=" not in raw or raw.lstrip().startswith("#"):
            continue
        name, value = (part.strip() for part in raw.split("=", 1))
        if name and value:
            values[name.upper()] = value
    return values


def parse_hoppers(value: str | None) -> list[int]:
    """Accept either a hopper count (``5``) or explicit numbers (``1,3``)."""
    if not value:
        return list(range(1, DEFAULT_HOPPERS + 1))
    try:
        if "," not in value:
            count = int(value)
            if count < 1:
                raise ValueError
            return list(range(1, count + 1))
        result: list[int] = []
        for part in value.split(","):
            number = int(part.strip())
            if number < 1:
                raise ValueError
            if number not in result:
                result.append(number)
        if not result:
            raise ValueError
        return result
    except ValueError:
        raise argparse.ArgumentTypeError(
            "hoppers must be a positive count or comma-separated numbers"
        ) from None


def parse_phones(value: str | None) -> list[str]:
    if not value:
        return []
    phones = [item.strip() for item in value.split(",") if item.strip()]
    if not phones:
        raise argparse.ArgumentTypeError("phones must contain at least one id")
    return list(dict.fromkeys(phones))


def seeded_links(phone: str, hopper: int) -> list[str]:
    """Give each hopper its own visible rotation while staying deterministic."""
    return [
        f"https://www.roblox.com/share?code=sim-{phone.lower()}-{hopper:02d}-{index:02d}"
        for index in range(1, 4)
    ]


def normalise_rotation(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    raw_links = value.get("links", [])
    links = []
    if isinstance(raw_links, list):
        for item in raw_links[:100]:
            link = str(item).strip()
            if link and link not in links:
                links.append(link)
    return {"links": links, "loop": bool(value.get("loop", True))}


@dataclass
class Hopper:
    number: int
    rotation: dict[str, Any]
    running: bool = True
    held: bool = False
    pin_url: str = ""
    server_index: int = 0
    runtime: float = 0.0
    changed_at: float = field(default_factory=time.monotonic)

    def tick(self, now: float) -> None:
        if not self.running:
            self.changed_at = now
            return
        self.runtime += max(0.0, now - self.changed_at)
        self.changed_at = now

    def set_running(self, running: bool) -> None:
        self.running = running
        self.held = False
        self.pin_url = ""
        self.runtime = 0.0
        self.changed_at = time.monotonic()

    def current_link(self) -> str:
        links = self.rotation["links"]
        if not links:
            return ""
        return links[min(self.server_index, len(links) - 1)]


class SimulatedPhone:
    def __init__(self, phone: str, hopper_numbers: list[int]) -> None:
        self.phone = phone
        self.hoppers = {
            number: Hopper(number, normalise_rotation({"links": seeded_links(phone, number)}))
            for number in hopper_numbers
        }
        # Start each row at a different point so the first dashboard render is
        # useful for testing, instead of showing every hopper on RF1 at 0s.
        for number, hopper in self.hoppers.items():
            hopper.server_index = (number - 1) % len(hopper.rotation["links"])
            hopper.runtime = float(number * 17)
        self.link_pool = [link for hopper in self.hoppers.values() for link in hopper.rotation["links"]]
        self.last_error = ""
        self.poll_count = 0

    def tick(self) -> None:
        now = time.monotonic()
        for hopper in self.hoppers.values():
            hopper.tick(now)

    def rotations(self) -> dict[str, dict[str, Any]]:
        return {str(number): dict(hopper.rotation) for number, hopper in self.hoppers.items()}

    def inventory(self) -> dict[str, dict[str, Any]]:
        """Return two account reports with enough variation for all dashboard views."""
        accounts: dict[str, dict[str, Any]] = {}
        for account_number, multiplier in ((1, 1), (2, 2)):
            name = f"Sim_{self.phone}_Account_{account_number}"
            pets: dict[str, dict[str, int]] = {}
            for index, (kind, count, grown, _rarity, _price) in enumerate(PET_DATA):
                adjusted = count + (1 if account_number == 2 and index == 1 else 0)
                pets[kind] = {"count": adjusted, "fg": min(adjusted, grown + account_number - 1)}
            pet_count = sum(item["count"] for item in pets.values())
            bucks = 12500 * multiplier + ((self.poll_count // 10) * 25 if account_number == 1 else 0)
            accounts[name] = {
                "player": name,
                "money": bucks,
                "stats": {"bucks": bucks, "petCount": pet_count, "eggCount": 2 + account_number},
                "pets": {"by_type": pets},
            }
        return accounts

    def prices(self) -> dict[str, float]:
        return {
            f"{kind.split(' (')[0]}|{'mega_neon' if '(' in kind else 'default'}": price
            for kind, _count, _fg, _rarity, price in PET_DATA
        }

    def rarities(self) -> dict[str, str]:
        return {kind.split(" (")[0]: rarity for kind, _count, _fg, rarity, _price in PET_DATA}

    def trades(self) -> dict[str, dict[str, Any]]:
        """Exercise live, terminal, retry, and missing-addon dashboard states."""
        statuses = ("active", "starting", "completed", "disconnected", "no script")
        now = int(time.time())
        result: dict[str, dict[str, Any]] = {}
        for number, hopper in self.hoppers.items():
            if not hopper.running:
                result[str(number)] = {"status": "stopped", "fresh": False}
                continue
            status = statuses[(number - 1) % len(statuses)]
            if status == "no script":
                result[str(number)] = {"status": status, "fresh": False}
                continue
            result[str(number)] = {
                "status": status,
                "ts": now,
                "age": 0,
                "fresh": True,
                "count": number * 3 + self.poll_count // 8,
                "items": [
                    {"name": "Ghostfinn Rod", "qty": 1},
                    {"name": "Kraken", "qty": number},
                ],
                "meta": {
                    "players": f"{2 + number % 4}/6",
                    "runtime": int(hopper.runtime),
                    "categories": {"rods": 1, "pets": number},
                },
                "file": f"Sim_{self.phone}_Trader_{number}_winteraddons.json",
            }
        return result

    def board(self) -> tuple[str, str, list[str]]:
        self.tick()
        rows: list[str] = []
        current: list[str] = []
        running = 0
        for number, hopper in self.hoppers.items():
            if not hopper.running:
                rows.append(f"{number:>2}  --    stopped")
                continue
            running += 1
            if hopper.held:
                rows.append(f"{number:>2}  PIN   held")
                current.append(f"{number}:PIN")
                continue
            link = hopper.current_link()
            server = f"RF{hopper.server_index + 1}" if link else "??"
            rows.append(f"{number:>2}  {server:<5} {int(hopper.runtime):>5}s runtime")
            current.append(f"{number}:{server}")
        board = "```\n #  srv   runtime\n" + "\n".join(rows) + "\n```"
        footer = f"simulator | 512MB free | load 0.18 | 7.4G free | {running}/{len(self.hoppers)} running"
        return board, footer, current

    def report(self) -> dict[str, Any]:
        self.poll_count += 1
        board, footer, current = self.board()
        return {
            "board": board,
            "footer": footer,
            "inv": self.inventory(),
            "servers": sum(len(hopper.rotation["links"]) for hopper in self.hoppers.values()),
            "srv_now": current,
            "prices": self.prices(),
            "rarities": self.rarities(),
            "rotations": self.rotations(),
            "packages": {
                str(number): f"com.roblox.client.sim{self.phone.lower()}{number}"
                for number in self.hoppers
            },
            "trades": self.trades(),
        }

    def _hopper(self, value: str) -> Hopper:
        number = int(value)
        if number not in self.hoppers:
            raise ValueError(f"hopper{number} is not configured on phone {self.phone}")
        return self.hoppers[number]

    def command(self, command: str) -> str:
        """Execute the subset of agent commands exposed by the web UI."""
        parts = shlex.split(command)
        if not parts:
            return "empty simulated command"
        action, args = parts[0].lower(), parts[1:]
        if action == "rotation_set":
            if len(args) != 2:
                raise ValueError("rotation_set requires hopper and config")
            hopper = self._hopper(args[0])
            hopper.rotation = normalise_rotation(json.loads(args[1]))
            hopper.server_index = min(hopper.server_index, max(0, len(hopper.rotation["links"]) - 1))
            hopper.runtime = 0.0
            hopper.changed_at = time.monotonic()
            mode = "looping" if hopper.rotation["loop"] else "one-shot"
            return f"saved simulated hopper{hopper.number} rotation: {len(hopper.rotation['links'])} server(s), {mode}"
        if action in {"start", "stop", "restart"}:
            hopper = self._hopper(args[0])
            if action == "start":
                was_running = hopper.running
                if not was_running:
                    hopper.set_running(True)
                return f"hopper{hopper.number} {'already running' if was_running else 'started'} (simulated)"
            if action == "stop":
                was_running = hopper.running
                hopper.set_running(False)
                return f"hopper{hopper.number} {'stopped' if was_running else 'not running'} (simulated)"
            hopper.set_running(True)
            return f"restarted hopper{hopper.number} (simulated)"
        if action in {"startall", "stopall"}:
            running = action == "startall"
            for hopper in self.hoppers.values():
                hopper.set_running(running)
            return f"{'started' if running else 'stopped'} {len(self.hoppers)} simulated hoppers"
        if action in {"goto", "goto_pin"}:
            hopper = self._hopper(args[0])
            server = int(args[1])
            if not 1 <= server <= len(hopper.rotation["links"]):
                return f"hopper{hopper.number} has no RF{server} (has RF1..RF{len(hopper.rotation['links'])})"
            hopper.server_index = server - 1
            hopper.runtime = 0.0
            hopper.running = True
            hopper.held = action == "goto_pin"
            hopper.pin_url = hopper.current_link() if hopper.held else ""
            return f"hopper{hopper.number} {'pinned to' if hopper.held else 'moved to'} RF{server} (simulated)"
        if action == "pin":
            hopper = self._hopper(args[0])
            hopper.pin_url = args[1]
            hopper.held = True
            hopper.running = True
            return f"hopper{hopper.number} pinned to that link (simulated)"
        if action == "unpin":
            hopper = self._hopper(args[0])
            hopper.held = False
            hopper.pin_url = ""
            hopper.changed_at = time.monotonic()
            return f"hopper{hopper.number} released, resuming rotation (simulated)"
        if action == "continue":
            for hopper in self.hoppers.values():
                hopper.held = False
                hopper.pin_url = ""
                hopper.changed_at = time.monotonic()
            return "resuming simulated rotation"
        if action == "servers":
            hopper = self._hopper(args[0])
            return "\n".join(f"RF{index}: {link}" for index, link in enumerate(hopper.rotation["links"], 1)) or "(empty)"
        if action == "assign":
            hopper = self._hopper(args[0])
            first, last = int(args[1]), int(args[2])
            hopper.rotation["links"] = self.link_pool[max(0, first - 1):last]
            hopper.server_index = min(hopper.server_index, max(0, len(hopper.rotation["links"]) - 1))
            return f"hopper{hopper.number} = links {first}-{last} ({len(hopper.rotation['links'])} servers, simulated)"
        if action == "assigns":
            return "\n".join(f"hopper{number}: 1-{len(hopper.rotation['links'])}" for number, hopper in self.hoppers.items()) or "(none)"
        if action == "link_add":
            self.link_pool.append(args[0])
            return f"added simulated link #{len(self.link_pool)}"
        if action == "all_goto":
            for hopper in self.hoppers.values():
                hopper.pin_url = args[0]
                hopper.held = True
            return "all simulated hoppers pinned, holding"
        if action == "logs":
            hopper = self._hopper(args[0])
            return f"[simulator] hopper{hopper.number} heartbeat ok\n[simulator] current={hopper.current_link() or 'none'}"
        if action == "pricelog":
            return f"prices cached: {len(self.prices())} | rarities: {len(self.rarities())}\n(simulated price worker)"
        if action == "refetch":
            return "marked simulated prices stale"
        if action == "scripts":
            return "hopper1.lua  simulated\nmonitor_adoptme.lua  simulated"
        if action in {"script_get", "script_add", "script_del", "autotrade"}:
            return f"{action} accepted (simulated)"
        return f"unknown cmd: {command} (simulated)"


def poll(phone: SimulatedPhone, url: str, key: str, results: list[dict[str, str]]) -> list[dict[str, Any]]:
    body = phone.report()
    body["results"] = results
    endpoint = f"{url.rstrip('/')}/api/{phone.phone}/poll"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-Key": key, "User-Agent": "panen-seeder/1.0"},
    )
    with urllib.request.urlopen(request, timeout=8) as response:
        payload = json.loads(response.read().decode("utf-8"))
    jobs = payload.get("jobs", [])
    return jobs if isinstance(jobs, list) else []


def service_phone(phone: SimulatedPhone, url: str, key: str) -> None:
    """Poll, execute queued jobs, and immediately return their results."""
    results: list[dict[str, str]] = []
    for _ in range(4):
        jobs = poll(phone, url, key, results)
        results = []
        if not jobs:
            return
        for job in jobs:
            job_id = str(job.get("id", ""))
            command = str(job.get("cmd", ""))
            try:
                result = phone.command(command)
            except Exception as exc:  # the real agent reports command failures instead of dying
                result = f"error running '{command}': {exc}"
            print(f"[seeder {phone.phone}] {command} -> {result.splitlines()[0]}")
            results.append({"id": job_id, "text": result})
    if results:
        poll(phone, url, key, results)


def build_parser(config: dict[str, str]) -> argparse.ArgumentParser:
    default_host = config.get("HOST", "127.0.0.1")
    if default_host in {"0.0.0.0", "::"}:
        default_host = "127.0.0.1"
    default_url = f"http://{default_host}:{config.get('PORT', '8090')}"
    parser = argparse.ArgumentParser(description="Seed the Hopper Fleet dashboard with simulated phones.")
    parser.add_argument("--url", default=default_url, help=f"web server base URL (default: {default_url})")
    parser.add_argument("--key", default=config.get("KEY", "CHANGE_ME_SHARED_SECRET"), help="phone poll key")
    parser.add_argument("--phones", type=parse_phones, default=parse_phones(config.get("PHONES", "A,B")), help="phone IDs, comma-separated")
    parser.add_argument("--hoppers", type=parse_hoppers, default=parse_hoppers(None), help="hopper count or comma-separated numbers (default: 5)")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL, help="seconds between poll rounds")
    parser.add_argument("--once", action="store_true", help="send one report per phone, then exit")
    return parser


def main() -> int:
    config = read_config()
    parser = build_parser(config)
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be greater than zero")
    phones = [SimulatedPhone(phone, args.hoppers) for phone in args.phones]
    if not phones:
        parser.error("at least one phone is required")
    print(f"[seeder] posting {len(phones)} simulated phone(s) to {args.url.rstrip('/')}")
    print(f"[seeder] phones={','.join(args.phones)} hoppers={','.join(map(str, args.hoppers))} interval={args.interval:g}s")
    print("[seeder] press Ctrl+C to stop; state resets when the seeder exits")
    while True:
        started = time.monotonic()
        for phone in phones:
            try:
                service_phone(phone, args.url, args.key)
                phone.last_error = ""
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
                message = str(exc)
                if message != phone.last_error:
                    print(f"[seeder {phone.phone}] poll error: {message}", file=sys.stderr)
                    phone.last_error = message
        if args.once:
            return 0
        delay = max(0.0, args.interval - (time.monotonic() - started))
        time.sleep(delay)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[seeder] stopped")
