"""Report aggregate account availability from a WinterHub/Rejoin dashboard.

This is intentionally separate from agent.py and monitor_adoptme.lua. It logs
in to the source dashboard, reads its agent/package list, and posts only an
online/total count to this project's web server. Usernames, cookies, and source
credentials are never sent to the dashboard.

Copy rejoin_listener.example.txt to rejoin_listener.txt, fill the blank values,
then run:

    python rejoin_listener.py

Use --once to collect and submit one sample before running it continuously.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import requests


BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "rejoin_listener.txt"
DEFAULTS = {
    "SOURCE_URL": "https://agent.wintercode.dev",
    "SOURCE_SCRIPT_KEY": "",
    "SOURCE_PASSWORD": "",
    "SOURCE_CREDENTIALS_FILE": "",
    "ACCOUNT_DB": "",
    "TOTAL_ACCOUNTS": "observed",
    "TARGET_URL": "http://127.0.0.1:8090",
    "TARGET_KEY": "",
    "TARGET_KEY_FILE": "",
    "POLL_INTERVAL": "30",
    "ONLINE_MODE": "ingame",
}
VALID_MODES = {"reported", "connected", "running", "ingame"}


class ListenerError(RuntimeError):
    pass


def read_config() -> dict[str, str]:
    config = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        for raw in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            if "=" not in raw or raw.lstrip().startswith("#"):
                continue
            key, value = (part.strip() for part in raw.split("=", 1))
            if key in config:
                config[key] = value
    return config


def config_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (BASE_DIR / path)


def resolve_credentials(config: dict[str, str]) -> None:
    """Resolve optional local credential pointers without printing secrets."""
    credential_path = config["SOURCE_CREDENTIALS_FILE"].strip()
    if credential_path and (not config["SOURCE_SCRIPT_KEY"].strip() or not config["SOURCE_PASSWORD"]):
        text = config_path(credential_path).read_text(encoding="utf-8", errors="replace")
        key_match = re.search(r"[\"']script_key[\"']\s*:\s*[\"']([^\"']+)[\"']", text)
        password_match = re.search(r"[\"']password[\"']\s*:\s*[\"']([^\"']+)[\"']", text)
        if not config["SOURCE_SCRIPT_KEY"].strip() and key_match:
            config["SOURCE_SCRIPT_KEY"] = key_match.group(1)
        if not config["SOURCE_PASSWORD"] and password_match:
            config["SOURCE_PASSWORD"] = password_match.group(1)
    target_key_path = config["TARGET_KEY_FILE"].strip()
    if target_key_path and not config["TARGET_KEY"].strip():
        text = config_path(target_key_path).read_text(encoding="utf-8", errors="replace")
        match = re.search(r"(?m)^\s*KEY\s*=\s*([^\r\n#]+)", text)
        if match:
            config["TARGET_KEY"] = match.group(1).strip()


def load_account_names(config: dict[str, str]) -> set[str]:
    """Load local user:password:cookie rows without retaining credentials."""
    raw_path = config["ACCOUNT_DB"].strip()
    if not raw_path:
        return set()
    path = config_path(raw_path)
    if not path.is_file():
        raise ListenerError(f"ACCOUNT_DB not found: {path}")
    names: set[str] = set()
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        username = line.split(":", 1)[0].strip()
        if username:
            names.add(username.casefold())
    if not names:
        raise ListenerError("ACCOUNT_DB does not contain any usernames")
    return names


def configured_total(config: dict[str, str], account_names: set[str]) -> int:
    if account_names:
        return len(account_names)
    try:
        total = int(config["TOTAL_ACCOUNTS"])
    except ValueError:
        if config["TOTAL_ACCOUNTS"].strip().lower() == "observed":
            return 0
        raise ListenerError("TOTAL_ACCOUNTS must be an integer or observed") from None
    if total < 1:
        raise ListenerError("Set ACCOUNT_DB or a positive TOTAL_ACCOUNTS")
    return total


def account_is_online(agent: object, package: object, mode: str) -> bool:
    if not isinstance(agent, dict) or not isinstance(package, dict):
        return False
    if mode == "reported":
        return True
    if not bool(agent.get("connected")):
        return False
    if mode == "connected":
        return True
    if mode == "running":
        return bool(package.get("is_running"))
    state = str(package.get("game_state", "")).strip().lower()
    return bool(package.get("is_running")) and state in {"ingame", "in_game", "in-game"}


def online_account_names(agents: object, mode: str) -> set[str]:
    if not isinstance(agents, list):
        raise ListenerError("source response does not contain an agents list")
    names: set[str] = set()
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        packages = agent.get("packages", [])
        if not isinstance(packages, list):
            continue
        for package in packages:
            if not account_is_online(agent, package, mode):
                continue
            username = str(package.get("username", "")).strip() if isinstance(package, dict) else ""
            if username:
                names.add(username.casefold())
    return names


class RejoinClient:
    def __init__(self, config: dict[str, str]) -> None:
        self.config = config
        self.session = requests.Session()
        self.authenticated = False
        self.base_url = config["SOURCE_URL"].rstrip("/")
        self.headers = {
            "Accept": "application/json",
            "Referer": f"{self.base_url}/",
            "User-Agent": "Panen rejoin listener/1.0",
        }

    def login(self) -> None:
        script_key = self.config["SOURCE_SCRIPT_KEY"].strip()
        password = self.config["SOURCE_PASSWORD"]
        if not script_key or not password:
            raise ListenerError("SOURCE_SCRIPT_KEY and SOURCE_PASSWORD are required")
        response = self.session.post(
            f"{self.base_url}/api/auth/login",
            json={"script_key": script_key, "password": password},
            headers={**self.headers, "Origin": self.base_url},
            timeout=20,
        )
        if not response.ok:
            raise ListenerError(f"source login failed (HTTP {response.status_code})")
        self.authenticated = True

    def agents(self) -> list[dict]:
        if not self.authenticated:
            self.login()
        response = self.session.get(f"{self.base_url}/api/agents", headers=self.headers, timeout=20)
        if response.status_code in {401, 403}:
            self.authenticated = False
            self.login()
            response = self.session.get(f"{self.base_url}/api/agents", headers=self.headers, timeout=20)
        if not response.ok:
            raise ListenerError(f"source agents request failed (HTTP {response.status_code})")
        try:
            payload = response.json()
        except ValueError:
            raise ListenerError("source agents response is not JSON") from None
        agents = payload.get("agents") if isinstance(payload, dict) else None
        if not isinstance(agents, list):
            raise ListenerError("source agents response has no agents list")
        return agents


def submit(config: dict[str, str], online_accounts: int, total_accounts: int, mode: str) -> None:
    target_url = config["TARGET_URL"].rstrip("/")
    target_key = config["TARGET_KEY"].strip()
    if not target_key:
        raise ListenerError("TARGET_KEY is required")
    response = requests.post(
        f"{target_url}/api/rejoin/stats",
        json={
            "source": "rejoin_listener",
            "online_accounts": online_accounts,
            "total_accounts": total_accounts,
            "mode": mode,
        },
        headers={"X-Key": target_key},
        timeout=20,
    )
    if not response.ok:
        raise ListenerError(f"dashboard submit failed (HTTP {response.status_code})")


def sample(client: RejoinClient, config: dict[str, str]) -> tuple[int, int, str]:
    mode = config["ONLINE_MODE"].strip().lower()
    if mode not in VALID_MODES:
        raise ListenerError(f"ONLINE_MODE must be one of: {', '.join(sorted(VALID_MODES))}")
    account_names = load_account_names(config)
    total_accounts = configured_total(config, account_names)
    agents = client.agents()
    observed_names = online_account_names(agents, "reported")
    if total_accounts == 0:
        total_accounts = len(observed_names)
        if total_accounts < 1:
            raise ListenerError("source returned no usernames; set TOTAL_ACCOUNTS manually")
    online_names = online_account_names(agents, mode)
    if account_names:
        online_names.intersection_update(account_names)
    online_accounts = len(online_names)
    if online_accounts > total_accounts:
        raise ListenerError("online count exceeds TOTAL_ACCOUNTS; update the local total")
    submit(config, online_accounts, total_accounts, mode)
    return online_accounts, total_accounts, mode


def main() -> int:
    parser = argparse.ArgumentParser(description="Report rejoin account counts to Hopper Fleet Web")
    parser.add_argument("--once", action="store_true", help="collect and send one sample, then exit")
    args = parser.parse_args()
    config = read_config()
    try:
        resolve_credentials(config)
    except OSError as exc:
        print(f"[rejoin-listener] credential file error: {exc}", file=sys.stderr)
        return 2
    try:
        interval = max(10, int(config["POLL_INTERVAL"]))
    except ValueError:
        print("[rejoin-listener] POLL_INTERVAL must be an integer", file=sys.stderr)
        return 2
    client = RejoinClient(config)
    while True:
        try:
            online, total, mode = sample(client, config)
            print(f"[rejoin-listener] {online}/{total} online ({mode})", flush=True)
        except (ListenerError, requests.RequestException) as exc:
            print(f"[rejoin-listener] {exc}", file=sys.stderr, flush=True)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
