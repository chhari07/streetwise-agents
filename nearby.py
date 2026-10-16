"""Finds real places and shops near the buyer using OpenStreetMap's Nominatim (free, no API key).

- geocode("Sector 14 Gurgaon")  -> (lat, lng, "Sector 14, Gurugram, Haryana, India")
- osm_shops(lat, lng)           -> real supermarkets, kirana/convenience, dairy and sabzi shops nearby

Both return None when the service fails, so the caller can fall back to shops.json.
Set STREETWISE_OFFLINE=1 to skip the network completely (tests the fallback).
Nominatim's rules: clear User-Agent, at most 1 request per second, cache results."""
import json
import os
import time
import urllib.parse
import urllib.request

from shop_data import distance_m, real_hours

UA = "Streetwise-agents/0.1 (Fetch-A-Thon 3.0 demo)"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
SHOP_SEARCHES = ["supermarket", "convenience store", "dairy shop", "greengrocer"]
SHOP_KINDS = {"supermarket", "convenience", "general", "grocery", "dairy", "greengrocer"}
MAX_SHOPS = 6
MIN_SHOPS = 3        # fewer than this inside a ring -> widen to the next ring
RINGS_M = (500, 1000, 2000)  # ripple search: 500 m -> 1 km -> 2 km
WIDE_RADIUS_M = RINGS_M[-1]
NOT_GROCERY = ("stationer", "xerox", "medical", "pharma", "chemist", "mobile", "office", "salon", "cloth", "dry fruit")

_geo_cache: dict[str, tuple[float, float, str] | None] = {}
_shop_cache: dict[tuple, list[dict]] = {}  # same area again -> same shops, even if the service goes down
_last_call = 0.0


def offline() -> bool:
    return os.getenv("STREETWISE_OFFLINE") == "1"


def _search(params: dict) -> list[dict]:
    global _last_call
    time.sleep(max(0.0, 1.05 - (time.time() - _last_call)))  # 1 request per second
    _last_call = time.time()
    q = urllib.parse.urlencode({**params, "format": "json", "countrycodes": "in"})
    req = urllib.request.Request(f"{NOMINATIM}?{q}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())


def geocode(place: str) -> tuple[float, float, str] | None:
    key = place.strip().lower()
    if offline() or not key:
        return None
    if key not in _geo_cache:
        try:
            hits = _search({"q": place, "limit": 1})
            _geo_cache[key] = (float(hits[0]["lat"]), float(hits[0]["lon"]), hits[0]["display_name"]) if hits else None
        except Exception:
            return None  # don't cache network errors
    return _geo_cache[key]


def osm_shops(lat: float, lng: float, radius_m: int = 500) -> list[dict] | None:
    """Nearest shops inside radius_m, widening ring by ring (500 m -> 1 km -> 2 km) until there are MIN_SHOPS."""
    if offline():
        return None
    key = (round(lat, 3), round(lng, 3), radius_m)
    if key in _shop_cache:
        return _shop_cache[key]
    d = WIDE_RADIUS_M / 111000  # degrees, roughly
    box = f"{lng - d},{lat + d},{lng + d},{lat - d}"
    found, failures = {}, 0
    for words in SHOP_SEARCHES:
        params = {"q": words, "viewbox": box, "bounded": 1, "limit": 40, "extratags": 1}
        try:
            hits = _search(params)
        except Exception:
            try:  # one retry: a single failed search used to drop shops like 24Seven
                hits = _search(params)
            except Exception:
                failures += 1
                continue
        for h in hits:
            if h.get("class") != "shop" or h.get("type") not in SHOP_KINDS or not h.get("name"):
                continue
            if any(w in h["name"].lower() for w in NOT_GROCERY):
                continue
            sid = f"osm-{h['osm_type']}-{h['osm_id']}"
            hl, hg = float(h["lat"]), float(h["lon"])
            found[sid] = {"id": sid, "name": h["name"], "kind": h["type"], "lat": hl, "lng": hg,
                          "distance_m": distance_m(lat, lng, hl, hg),
                          "opening_hours": (h.get("extratags") or {}).get("opening_hours", "")}
    if failures == len(SHOP_SEARCHES):
        return None
    shops = sorted(found.values(), key=lambda s: s["distance_m"])
    near = ripple(shops, radius_m)
    radius_m = ring_of(near, radius_m)
    # 24x7 shops matter most at night: keep up to 2 within the radius even past the shop limit
    always = [s for s in shops if s["distance_m"] <= radius_m and s not in near[:MAX_SHOPS]
              and (real_hours(s) or ([], ""))[1] == "24x7"]
    _shop_cache[key] = near[:MAX_SHOPS] + always[:2]
    return _shop_cache[key]


def ripple(shops: list[dict], start_m: int = RINGS_M[0]) -> list[dict]:
    """Shops (each with distance_m) inside the first ring that has at least MIN_SHOPS, nearest first."""
    shops = sorted(shops, key=lambda s: s["distance_m"])
    near = []
    for r in [x for x in RINGS_M if x >= start_m] or [start_m]:
        near = [s for s in shops if s["distance_m"] <= r]
        if len(near) >= MIN_SHOPS:
            break
    return near


def ring_of(shops: list[dict], start_m: int = RINGS_M[0]) -> int:
    """The ring a ripple result reached: the smallest ring that holds every shop in it."""
    far = max((s["distance_m"] for s in shops), default=0)
    return next((r for r in RINGS_M if r >= max(far, start_m)), RINGS_M[-1])
