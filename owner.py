"""Shop owner's one-tap Yes / No for each order (the "Streetwise Shop" screen).

STREETWISE_OWNER=panel  -> each Merchant agent waits for the owner to tap YES or NO on
                           http://localhost:8090 (open it on a phone on the same Wi-Fi, or a laptop).
                           No tap within OWNER_TIMEOUT_S -> the order is not confirmed.
STREETWISE_OWNER=auto   -> no screen: the owner's auto-accept rule decides (used by test_flow.py).

There is no stock list to keep up to date: the owner just answers each order."""
import asyncio
import html
import itertools
import os
import time

from aiohttp import web

import grid

MODE = os.getenv("STREETWISE_OWNER", "auto")
OWNER_TIMEOUT_S = int(os.getenv("STREETWISE_OWNER_TIMEOUT", "60"))
PANEL_PORT = int(os.getenv("STREETWISE_OWNER_PORT", "8090"))

_ids = itertools.count(1)
_open: dict[int, dict] = {}  # order id -> {shop, lines, total, future, at}
_done: list[dict] = []  # last answers, newest first


async def ask(shop_name: str, lines: list[tuple[str, int, int]], total: int) -> bool | None:
    """Shows the order on the owner's screen. True = YES, False = NO, None = no tap in time.
    lines: (item label, qty, price)."""
    oid = next(_ids)
    fut = asyncio.get_running_loop().create_future()
    _open[oid] = {"shop": shop_name, "lines": lines, "total": total, "future": fut, "at": time.time()}
    try:
        return await asyncio.wait_for(fut, OWNER_TIMEOUT_S)
    except asyncio.TimeoutError:
        return None
    finally:
        _open.pop(oid, None)
        ans = fut.result() if fut.done() and not fut.cancelled() else None  # wait_for cancels it on timeout
        _done.insert(0, {"shop": shop_name, "total": total, "answer": ans})
        del _done[8:]


CSS = """body{margin:0;background:#0A0C10;color:#E6EAF0;font:15px/1.45 Inter,system-ui,sans-serif}
.w{max-width:440px;margin:0 auto;padding:18px 16px}h1{font-size:20px;margin:0 0 4px}.mu{color:#8A94A6;font-size:13px}
.o{background:#11151B;border:1px solid #263040;border-radius:16px;padding:14px;margin:14px 0}
.o h2{font-size:16px;margin:0 0 8px}.li{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px dashed #263040;font-family:monospace}
.tot{font-weight:700;border:0}.tot b{color:#22C55E}.bt{display:flex;gap:10px;margin-top:12px}
button{flex:1;border-radius:12px;padding:14px 0;font:800 18px system-ui;cursor:pointer}
.y{background:#22C55E;color:#0A0C10;border:0}.n{background:none;color:#F43F5E;border:1.5px solid #F43F5E}
.d{display:flex;justify-content:space-between;font-size:13px;padding:4px 0;color:#8A94A6}"""


def _page() -> str:
    cards = []
    for oid, o in sorted(_open.items()):
        left = max(0, OWNER_TIMEOUT_S - int(time.time() - o["at"]))
        rows = "".join(f'<div class="li"><span>{html.escape(n)} × {q}</span><span>₹{p}</span></div>' for n, q, p in o["lines"])
        cards.append(f'<div class="o"><h2>{html.escape(o["shop"])} · new order</h2>{rows}'
                     f'<div class="li tot"><span>TOTAL</span><b>₹{o["total"]}</b></div>'
                     f'<form method="post" action="/answer" class="bt"><input type="hidden" name="id" value="{oid}">'
                     f'<button class="y" name="ok" value="1">YES</button><button class="n" name="ok" value="0">NO</button></form>'
                     f'<p class="mu">{left} s left · no tap = not confirmed</p></div>')
    done = "".join(f'<div class="d"><span>{html.escape(d["shop"])} · ₹{d["total"]}</span>'
                   f'<span>{"YES" if d["answer"] else "NO" if d["answer"] is False else "no reply"}</span></div>' for d in _done)
    body = "".join(cards) or '<p class="mu" style="margin-top:24px">No new orders. This page refreshes by itself.</p>'
    return (f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta http-equiv="refresh" content="2"><title>Streetwise Shop</title><style>{CSS}</style></head><body><div class="w">'
            f'<h1>Streetwise Shop</h1><div class="mu">Tap YES if you have the items at this price. · <a href="/map" style="color:#38BDF8">Streetwise Grid map</a></div>{body}'
            f'{"<p class=mu style=margin-top:20px>Recent</p>" + done if done else ""}</div></body></html>')


async def _index(request: web.Request) -> web.Response:
    return web.Response(text=_page(), content_type="text/html")


async def _answer(request: web.Request) -> web.Response:
    form = await request.post()
    o = _open.get(int(form.get("id", 0)))
    if o and not o["future"].done():
        o["future"].set_result(form.get("ok") == "1")
    raise web.HTTPSeeOther("/")


async def run_panel(port: int = PANEL_PORT) -> None:
    app = web.Application()
    app.add_routes([web.get("/", _index), web.post("/answer", _answer), *grid.ROUTES])
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"Shop owner screen: http://localhost:{port}  (YES / NO for each order) · live map: http://localhost:{port}/map", flush=True)
    while True:
        await asyncio.sleep(3600)
