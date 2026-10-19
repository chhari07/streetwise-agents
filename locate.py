"""The buyer's live location, so shops are searched around where they really are (not just a typed area).

Two ways in:
- Live link: Grahak sends a one-time link; the buyer taps "Share my live location", the browser's GPS is
  posted back here and Grahak picks it up (drain()). Served on its own port (STREETWISE_LOCATE_PORT, 8091),
  apart from the owner's YES / NO screen, so only this page needs to be public.
  Browsers give GPS only on https or localhost: for a phone, run a tunnel and set STREETWISE_PUBLIC_URL, e.g.
      cloudflared tunnel --url http://localhost:8091   ->   STREETWISE_PUBLIC_URL=https://<name>.trycloudflare.com
- In the chat: a Google Maps link (also maps.app.goo.gl short links), "geo:28.46,77.07" or plain "28.4672, 77.0710"."""
import asyncio
import json
import os
import re
import secrets
import time
import urllib.parse
import urllib.request

from aiohttp import web

PORT = int(os.getenv("STREETWISE_LOCATE_PORT", "8091"))
PUBLIC_URL = os.getenv("STREETWISE_PUBLIC_URL", f"http://localhost:{PORT}").rstrip("/")
LINK_TTL_S = 30 * 60

_links: dict[str, tuple[str, float]] = {}  # token -> (buyer address, created at)
_inbox: list[tuple[str, float, float, int]] = []  # (buyer, lat, lng, accuracy m) waiting for Grahak

PIN = re.compile(r"!3d(-?\d{1,2}\.\d+)!4d(-?\d{1,3}\.\d+)")  # a Maps place link: the pin itself
PAIR = re.compile(r"(-?\d{1,2}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})")  # "28.4672,77.071", "@28.46,77.07,17z", "q=..."
GEO = re.compile(r"\bgeo:(-?\d{1,2}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)", re.I)  # "geo:28.46,77.07"
URL = re.compile(r"https?://\S+", re.I)
SHORT = re.compile(r"https?://(?:maps\.app\.goo\.gl|goo\.gl/maps|g\.co/kgs)/\S+", re.I)


def link(buyer: str) -> str:
    """One-time live-location link for this buyer (valid 30 min)."""
    now = time.time()
    for t, (_, at) in list(_links.items()):
        if now - at > LINK_TTL_S:
            del _links[t]
    token = secrets.token_urlsafe(8)
    _links[token] = (buyer, now)
    return f"{PUBLIC_URL}/here/{token}"


def drain() -> list[tuple[str, float, float, int]]:
    """Live locations that came in since the last call."""
    got = _inbox[:]
    _inbox.clear()
    return got


def _ok(lat: float, lng: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lng <= 180 and (lat, lng) != (0, 0)


def _expand(url: str) -> str:
    """maps.app.goo.gl/... -> the full Google Maps URL it redirects to."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            return r.geturl()
    except Exception:
        return url


def coords_in(text: str) -> tuple[float, float] | None:
    for rx in (PIN, GEO, PAIR):
        for m in rx.finditer(text):
            lat, lng = float(m.group(1)), float(m.group(2))
            if _ok(lat, lng):
                return lat, lng
    return None


def find(text: str) -> tuple[dict | None, str]:
    """Location the buyer pasted in the chat -> ({"lat", "lng"} or {"place"}, the text without it).
    Blocking (a short link is opened), so call it with asyncio.to_thread."""
    full = text
    for short in SHORT.findall(text):
        full = full.replace(short, _expand(short))
    spot = coords_in(full)
    rest = URL.sub(" ", text)
    rest = GEO.sub(" ", rest)
    if spot:
        rest = PAIR.sub(" ", PIN.sub(" ", rest))
        return {"lat": spot[0], "lng": spot[1]}, re.sub(r"\s+", " ", rest).strip(" ,")
    m = re.search(r"/maps/(?:place|search)/([^/@?]+)", full)  # a Maps link with only a place name
    if m:
        place = urllib.parse.unquote_plus(m.group(1)).strip()
        if place:
            return {"place": place}, re.sub(r"\s+", " ", rest).strip(" ,")
    return None, text


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Streetwise · live location</title><style>
body{margin:0;background:#0A0C10;color:#E6EAF0;font:16px/1.5 Inter,system-ui,sans-serif}
.w{max-width:420px;margin:0 auto;padding:28px 16px}h1{font-size:22px;margin:0 0 6px}.mu{color:#8A94A6;font-size:14px}
button{width:100%;margin:22px 0 12px;border:0;border-radius:14px;padding:16px 0;background:#FF5A1F;color:#fff;font:800 18px system-ui;cursor:pointer}
button:disabled{opacity:.5}#s{min-height:48px}.ok{color:#22C55E;font-weight:700}.bad{color:#F43F5E}
</style></head><body><div class="w"><h1>📍 Share your live location</h1>
<p class="mu">Streetwise looks for open shops around you (500 m → 1 km → 2 km). Your location is used only for this search.</p>
<button id="b">Share my live location</button><div id="s" class="mu">__STATE__</div></div><script>
const b=document.getElementById('b'),s=document.getElementById('s');
if(__DONE__)b.disabled=true;
b.onclick=()=>{if(!navigator.geolocation){s.innerHTML='<span class=bad>This browser has no location. Type your area in ASI:One instead.</span>';return}
b.disabled=true;s.textContent='Finding you…';
navigator.geolocation.getCurrentPosition(async p=>{
 const r=await fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({lat:p.coords.latitude,lng:p.coords.longitude,acc:p.coords.accuracy})});
 s.innerHTML=r.ok?'<span class=ok>✓ Location sent.</span> Go back to ASI:One, your shop options are on the way. (No reply? Type <b>done</b>.)'
  :'<span class=bad>This link has expired. Ask the agent for a new one: type <b>live location</b>.</span>'},
 e=>{b.disabled=false;s.innerHTML='<span class=bad>Location blocked ('+e.message+'). Allow it and try again, or type your area in ASI:One.</span>'},
 {enableHighAccuracy:true,timeout:15000,maximumAge:0})};
</script></body></html>"""


async def _page(request: web.Request) -> web.Response:
    alive = request.match_info["token"] in _links
    state = "" if alive else '<span class="bad">This link has expired. In ASI:One type <b>live location</b> for a new one.</span>'
    return web.Response(text=PAGE.replace("__STATE__", state).replace("__DONE__", "false" if alive else "true"),
                        content_type="text/html")


async def _post(request: web.Request) -> web.Response:
    got = _links.pop(request.match_info["token"], None)  # one use only
    if not got or time.time() - got[1] > LINK_TTL_S:
        return web.json_response({"ok": False}, status=410)
    try:
        data = await request.json()
        lat, lng, acc = float(data["lat"]), float(data["lng"]), int(float(data.get("acc") or 0))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return web.json_response({"ok": False}, status=400)
    if not _ok(lat, lng):
        return web.json_response({"ok": False}, status=400)
    _inbox.append((got[0], lat, lng, acc))
    return web.json_response({"ok": True})


async def run_server(port: int = PORT) -> None:
    app = web.Application()
    app.add_routes([web.get("/here/{token}", _page), web.post("/here/{token}", _post)])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"Live location page: {PUBLIC_URL}/here/<token>  (port {port})", flush=True)
    while True:
        await asyncio.sleep(3600)
