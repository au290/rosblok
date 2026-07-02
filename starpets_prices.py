"""Look up a StarPets item's price by name via their public API.

Usage:  python starpets_prices.py <name>
Example: python starpets_prices.py unicorn
"""
import sys
import json
import urllib.request

URL = "https://market.apineural.com/api/v2/store/items/all"
TYPES = ["transport", "pet", "egg", "potion", "stroller", "toy", "petwear", "gift"]


def search(name, currency="usd", amount=50):
    body = json.dumps({
        "filter": {"name": name, "types": [{"type": t} for t in TYPES]},
        "page": 1,
        "amount": amount,
        "currency": currency,
        "sort": {"popularity": "desc"},
    }).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "content-type": "application/json",
        "origin": "https://starpets.gg",
        "referer": "https://starpets.gg/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main():
    if len(sys.argv) < 2:
        print("usage: python starpets_prices.py <pet name>")
        return
    name = " ".join(sys.argv[1:])
    data = search(name)
    items = data.get("items", [])
    if not items:
        print(f"No items found for '{name}'.")
        return
    print(f"'{name}' -> {data.get('count')} matches (showing {len(items)}):\n")
    for it in items:
        tags = [t for t in (it.get("age"), it.get("pumping")) if t and t != "default"]
        variant = f" [{', '.join(tags)}]" if tags else ""
        print(f"  {it['price']:>7} $  {it['name']}{variant}  ({it['rare']})")


if __name__ == "__main__":
    main()
