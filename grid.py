"""Streetwise Grid: a live map of the shops and what the agents are doing (http://localhost:8090/map).

The agents run in one process (run_all.py), so they write small events here:
Directory -> the last search (centre + ring) and the shops it found, Guardian -> blocked shops,
Grahak -> confirmed orders and agent-charge payments. The map page polls /api/grid every 3 s."""
import time

from aiohttp import web

from shop_data import REGISTRY, is_open

EXPLORER_TX = "https://explore-dorado.fetch.ai/transactions/{}"

LAST: dict = {"search": None}  # {"lat", "lng", "radius_m", "area", "shop_ids", "at"}
BLOCKED: dict[str, str] = {}  # shop_id -> reason (last check)
CHOSEN: set[str] = set()  # shop ids in the last confirmed order
EVENTS: list[dict] = []  # newest first: {"t", "kind", "text", "link"}


def event(kind: str, text: str, link: str = "") -> None:
    EVENTS.insert(0, {"t": time.strftime("%H:%M:%S"), "kind": kind, "text": text, "link": link})
    del EVENTS[30:]


def searched(lat: float, lng: float, radius_m: int, area: str, shop_ids: list[str]) -> None:
    LAST["search"] = {"lat": lat, "lng": lng, "radius_m": radius_m, "area": area, "shop_ids": shop_ids, "at": time.time()}
    BLOCKED.clear()
    CHOSEN.clear()
    event("search", f"Store Locator: {len(shop_ids)} shops within {radius_m} m of {area.split(',')[0]}")


def blocked(shop_id: str, name: str, reason: str) -> None:
    BLOCKED[shop_id] = reason
    event("blocked", f"Trust Agent blocked {name}: {reason}")


def ordered(order_id: str, shop_ids: list[str], names: list[str], total: int) -> None:
    CHOSEN.clear()
    CHOSEN.update(shop_ids)
    event("order", f"Order {order_id}: {' + '.join(names)} · Rs {total}")


def paid(text: str, tx: str = "") -> None:
    event("payment", text, EXPLORER_TX.format(tx) if tx and not tx.startswith("DEMO") else "")


def snapshot() -> dict:
    s = LAST["search"]
    ids = set(s["shop_ids"]) if s else set()
    shops = []
    for sid, shop in REGISTRY.items():
        if sid not in ids and shop.get("demo"):  # map shops seen in old searches: keep the map clean
            continue
        state = ("chosen" if sid in CHOSEN else "blocked" if sid in BLOCKED else
                 "open" if is_open(shop) else "closed")
        shops.append({"id": sid, "name": shop["name"], "lat": shop["lat"], "lng": shop["lng"], "state": state,
                      "why": BLOCKED.get(sid, ""), "in_search": sid in ids})
    return {"search": s, "shops": shops, "events": EVENTS}


PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Streetwise Grid</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
html,body{margin:0;height:100%;background:#0A0C10;color:#E6EAF0;font:14px/1.45 Inter,system-ui,sans-serif}
.app{display:grid;grid-template-columns:1fr 360px;height:100%}#map{height:100%}
.leaflet-tile-pane{filter:invert(1) hue-rotate(180deg) brightness(.8) contrast(.9)}  /* dark map from the free OSM tiles */
.side{border-left:1px solid #263040;padding:16px;overflow:auto;background:#0E1116}
h1{font-size:18px;margin:0}.mu{color:#8A94A6;font-size:12.5px}
.lg{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0}.lg span{display:flex;gap:6px;align-items:center;font-size:12.5px}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}
.ev{border-bottom:1px dashed #263040;padding:8px 0;font-size:13px}.ev b{font-family:monospace;color:#8A94A6;font-weight:500;margin-right:6px}
.ev a{color:#38BDF8}.k-blocked{color:#F43F5E}.k-order{color:#22C55E}.k-payment{color:#FACC15}
@media(max-width:800px){.app{grid-template-columns:1fr;grid-template-rows:60% 40%}.side{border-left:0;border-top:1px solid #263040}}
</style></head><body><div class="app"><div id="map"></div><div class="side">
<h1>Streetwise Grid</h1><div class="mu">Every pin is a shop with its own Merchant Agent. Live from the agents.</div>
<div class="lg"><span><i class="dot" style="background:#22C55E"></i>open</span><span><i class="dot" style="background:#6B7280"></i>closed</span>
<span><i class="dot" style="background:#F43F5E"></i>blocked</span><span><i class="dot" style="background:#FF5A1F"></i>in the order</span></div>
<div class="mu" id="area"></div><div id="events"></div></div></div>
<script>
const C={open:"#22C55E",closed:"#6B7280",blocked:"#F43F5E",chosen:"#FF5A1F"};
const map=L.map("map").setView([28.4672,77.0710],16);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",{maxZoom:19,attribution:"© OpenStreetMap"}).addTo(map);
const layer=L.layerGroup().addTo(map);let lastAt=null;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
async function tick(){
  let d;try{d=await (await fetch("/api/grid")).json()}catch(e){return}
  layer.clearLayers();const s=d.search;
  if(s){[500,1000,2000].filter(r=>r<=Math.max(s.radius_m,500)).forEach(r=>L.circle([s.lat,s.lng],{radius:r,color:r==s.radius_m?"#FF5A1F":"#38BDF8",weight:1.5,dashArray:"5 6",fill:false}).addTo(layer));
    L.circleMarker([s.lat,s.lng],{radius:5,color:"#38BDF8",fillOpacity:1}).bindTooltip("search centre").addTo(layer);
    document.getElementById("area").textContent="Last search: "+s.area+" · ring "+s.radius_m+" m";
    if(s.at!==lastAt){lastAt=s.at;map.fitBounds(L.circle([s.lat,s.lng],{radius:s.radius_m}).addTo(layer).getBounds(),{padding:[30,30]})}}
  d.shops.forEach(p=>L.circleMarker([p.lat,p.lng],{radius:p.state=="chosen"?11:8,color:C[p.state],fillColor:C[p.state],fillOpacity:.85,weight:2})
    .bindTooltip(esc(p.name)+" · "+p.state+(p.why?" ("+esc(p.why)+")":""),{permanent:p.in_search,
      direction:s&&p.lng<s.lng?"left":"right",offset:s&&p.lng<s.lng?[-10,0]:[10,0]}).addTo(layer));  // labels to the outside: no overlap
  document.getElementById("events").innerHTML=d.events.map(e=>'<div class="ev k-'+e.kind+'"><b>'+e.t+'</b>'+esc(e.text)+
    (e.link?' · <a href="'+e.link+'" target="_blank">view tx</a>':"")+"</div>").join("")||'<p class="mu">Waiting for the first search…</p>';
}
tick();setInterval(tick,3000);
</script></body></html>"""


async def _map(request: web.Request) -> web.Response:
    return web.Response(text=PAGE, content_type="text/html")


async def _api(request: web.Request) -> web.Response:
    return web.json_response(snapshot())


ROUTES = [web.get("/map", _map), web.get("/api/grid", _api)]
