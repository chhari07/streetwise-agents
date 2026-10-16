"""Shop data shared by the agents: shops.json (offline fallback), the live shop registry, and helpers."""
import json
import math
import re
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import state

DATA = json.loads((Path(__file__).with_name("shops.json")).read_text())
ITEMS: list[dict] = json.loads((Path(__file__).with_name("catalogue.json")).read_text())["items"]
CATALOGUE: dict[str, str] = {i["key"]: i["label"] for i in ITEMS}
SHOPS: list[dict] = DATA["shops"]
BUYER_DEFAULT = DATA["buyer_default"]

# Until shop owners join, every order is a demo: no shop is told and no money moves.
# STREETWISE_DEMO=0 only changes the wording; real orders still need owner sign-up, owner alerts and payment.
DEMO_MODE = os.getenv("STREETWISE_DEMO", "1") != "0"


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> int:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return int(2 * r * math.asin(math.sqrt(a)))


def current_hour() -> int:
    """Hour in India. STREETWISE_CLOCK_HOUR overrides it (useful for demos at night)."""
    forced = os.getenv("STREETWISE_CLOCK_HOUR")
    if forced:
        return int(forced)
    return datetime.now(ZoneInfo("Asia/Kolkata")).hour


def is_open(shop: dict) -> bool:
    if shop.get("closed_now"):  # demo shop with its shutter down (shows the QR closed-shop path)
        return False
    # Made-up demo hours shouldn't block a demo at night; real hours from OpenStreetMap always count.
    if DEMO_MODE and shop.get("hours_source", "demo") == "demo":
        return True
    start, end = shop["open"]
    return start <= current_hour() < end


def label(item: str) -> str:
    return CATALOGUE.get(item, item)


# ---- Live shop registry -------------------------------------------------------------
# Until Streetwise is public, real nearby shops (from OpenStreetMap) are "demo shops":
# real name and location, but made-up prices that stay the same for that shop every run.
# Directory registers them here; Dukaan and Guardian read them back by shop_id.
REGISTRY: dict[str, dict] = {s["id"]: s for s in SHOPS}

BASE_PRICES = {i["key"]: i["price"] for i in ITEMS}
GROUPS_BY_KIND = {"dairy": {"dairy", "bakery"}, "greengrocer": {"veg", "fruit"}}  # other kinds stock everything
STOCK_BY_KIND = {kind: [i["key"] for i in ITEMS if i["group"] in groups] for kind, groups in GROUPS_BY_KIND.items()}


def _roll(*parts) -> float:
    """Stable pseudo-random number in [0, 1) for this shop + key (same every run)."""
    import hashlib
    h = hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()
    return int(h[:8], 16) / 0x100000000


def trick_shop(shop_ids: list[str], avoid: set[str] = frozenset()) -> str | None:
    """The one shop in a search that hides a packing fee, so Guardian has something to catch.
    Same pick every run; none when there's only one shop (it would leave the buyer with nothing).
    Shops in `avoid` (24x7 ones) are skipped when possible, so night orders still have a clean shop."""
    if len(shop_ids) < 2:
        return None
    pool = [i for i in shop_ids if i not in avoid] or shop_ids
    return min(pool, key=lambda i: _roll(i, "trick"))


ALWAYS_OPEN_NAME = re.compile(r"\b24\s*[-x×*/]?\s*(seven|7|hours?|hrs)\b", re.I)
SIMPLE_HOURS = re.compile(r"^(?:Mo-Su\s+)?(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")


def real_hours(osm: dict) -> tuple[list[int], str] | None:
    """Opening hours from OpenStreetMap when they are simple enough to trust:
    "24/7" or a 24x7 name -> always open; "Mo-Su 10:00-21:00" or "10:00-21:00" -> those hours."""
    oh = (osm.get("opening_hours") or "").strip()
    if oh == "24/7" or ALWAYS_OPEN_NAME.search(osm["name"]):
        return [0, 24], "24x7"
    m = SIMPLE_HOURS.match(oh)
    if m:
        h1, m1, h2, m2 = map(int, m.groups())
        end = h2 + (1 if m2 else 0)
        if 0 <= h1 < end <= 24:
            return [h1, end], "osm"
    return None


def demo_shop(osm: dict, trick: bool = False) -> dict:
    """Turns a real OpenStreetMap shop into a working demo shop with steady demo prices."""
    sid = osm["id"]
    stock = STOCK_BY_KIND.get(osm["kind"], list(BASE_PRICES))
    prices = {}
    for item in stock:
        if osm["kind"] != "supermarket" and _roll(sid, item, "stock") < 0.15:
            continue  # small shops miss a few items
        prices[item] = round(BASE_PRICES[item] * (0.9 + 0.22 * _roll(sid, item, "price")))
    real = real_hours(osm)
    return {
        "id": sid, "name": osm["name"], "lat": osm["lat"], "lng": osm["lng"], "kind": osm["kind"], "demo": True,
        "open": real[0] if real else [7 + int(_roll(sid, "open") * 3), 21 + int(_roll(sid, "close") * 3)],
        "hours_source": real[1] if real else "demo",
        "auto_accept_under": [500, 1000, 1500][int(_roll(sid, "auto") * 3)],
        "published_fees": [],
        "extra_fees": [{"name": "packing", "amount": 30}] if trick else [],
        "prices": prices,
        "out_of_stock": [],
    }


def _fill_demo_stock(shop: dict) -> None:
    """shops.json shops list prices only for the first 13 items (their demo story depends on those).
    They get steady demo prices for the rest of the catalogue, by shop kind, so any item works offline too."""
    for item in STOCK_BY_KIND.get(shop.get("kind", ""), list(BASE_PRICES)):
        if item in shop["prices"] or item in FIRST_ITEMS or _roll(shop["id"], item, "stock") < 0.15:
            continue
        shop["prices"][item] = round(BASE_PRICES[item] * (0.9 + 0.22 * _roll(shop["id"], item, "price")))


FIRST_ITEMS = {"paneer_200g", "butter_100g", "cream_200ml", "tomato_1kg", "onion_1kg", "ginger_garlic_paste",
               "garam_masala_50g", "kasuri_methi", "milk_1l", "rice_1kg", "atta_5kg", "eggs_6", "bread"}
for _s in SHOPS:
    _fill_demo_stock(_s)


def register(shop: dict) -> dict:
    """Keeps the first profile a shop got, so its prices don't change between searches.
    Saved to state/shops_seen.json, so an order for a quote from before a restart still finds the shop."""
    if shop["id"] not in REGISTRY:
        REGISTRY[shop["id"]] = shop
        state.save("shops_seen.json", {k: v for k, v in REGISTRY.items() if v.get("demo")})
    return REGISTRY[shop["id"]]


for _id, _shop in state.load("shops_seen.json").items():
    REGISTRY.setdefault(_id, _shop)


def get_shop(shop_id: str) -> dict:
    return REGISTRY[shop_id]
