"""Send a synthetic inventory report to the Trackstat monitor endpoint.

Examples:
    python web/test_trackstat.py --key YOUR_WEB_KEY
    python web/test_trackstat.py --key YOUR_WEB_KEY --interval 30

The payload matches monitor_adoptme.lua. It is intentionally a separate test
client and never reads or sends Roblox cookies.
"""

from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timezone

import requests


DEFAULT_URL = "http://agent.kqing.web.id"


def payload(account: str) -> dict:
    return {
        "source": "monitor_adoptme",
        "inv": {
            account: {
                "player": account,
                "money": 12345,
                "stats": {"bucks": 12345, "petCount": 4, "eggCount": 1},
                "pets": {
                    "count": 4,
                    "eggs": 1,
                    "by_type": {
                        "dog": {"count": 2, "fg": 1, "kind": "dog", "display_name": "Dog", "neon": False, "mega": False},
                        "cat (neon)": {"count": 1, "fg": 1, "kind": "cat", "display_name": "Cat", "neon": True, "mega": False},
                        "dragon (mega neon)": {"count": 1, "fg": 1, "kind": "dragon", "display_name": "Dragon", "neon": False, "mega": True},
                    },
                    "eggs_by_type": {"basic_egg": 1},
                },
            }
        },
    }


def send(url: str, key: str, account: str) -> None:
    endpoint = url.rstrip("/") + "/api/monitor/poll"
    response = requests.post(
        endpoint,
        headers={"Content-Type": "application/json", "X-Key": key, "User-Agent": "panen-trackstat-test"},
        json=payload(account),
        timeout=20,
    )
    try:
        body = response.json()
    except ValueError:
        body = response.text[:300]
    if not response.ok:
        raise RuntimeError(f"HTTP {response.status_code} from {endpoint}: {body}")
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    print(f"[{now}] sent Trackstat sample for {account}: HTTP {response.status_code} {body}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Send synthetic inventory data to Trackstat")
    parser.add_argument("--url", default=os.getenv("PANEN_URL", DEFAULT_URL), help="web base URL")
    parser.add_argument("--key", default=os.getenv("PANEN_KEY", ""), help="web KEY (or PANEN_KEY)")
    parser.add_argument("--account", default="trackstat-test", help="test account label")
    parser.add_argument("--interval", type=int, default=0, help="repeat every N seconds; 0 sends once")
    args = parser.parse_args()
    if not args.key:
        parser.error("provide --key or set PANEN_KEY")
    if args.interval < 0:
        parser.error("--interval cannot be negative")

    while True:
        try:
            send(args.url, args.key, args.account)
        except (requests.RequestException, RuntimeError) as exc:
            print(f"[trackstat-test] {exc}", flush=True)
            return 1
        if args.interval == 0:
            return 0
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
