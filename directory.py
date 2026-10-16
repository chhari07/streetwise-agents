"""Streetwise Directory (Store Locator Agent): tells Grahak which shops are near the buyer.

Every Streetwise shop runs its own Merchant (Dukaan) agent, so each ShopInfo carries that shop's
own agent address. Real nearby shops found on OpenStreetMap haven't joined Streetwise yet, so one
shared demo Dukaan answers for them (demo prices). If OpenStreetMap is down or finds nothing,
the shops.json demo shops are used so the demo never breaks.

Search ripple: 500 m -> 1 km -> 2 km, until at least 3 shops are found.
A ShopSearch with shop_id (the buyer scanned that shop's QR) returns just that shop."""
import asyncio

from uagents import Agent, Context

from models import ShopInfo, ShopList, ShopSearch
from nearby import geocode, osm_shops, ring_of, ripple
from shop_data import BUYER_DEFAULT, REGISTRY, SHOPS, demo_shop, distance_m, real_hours, register, trick_shop


def make_directory(seed: str, dukaan_address: str, shop_agents: dict[str, str] | None = None, **agent_kwargs) -> Agent:
    """dukaan_address: the shared demo Dukaan (for map shops not on Streetwise yet).
    shop_agents: shop_id -> that shop's own Merchant agent address."""
    agent = Agent(name="streetwise-directory", seed=seed, **agent_kwargs)
    shop_agents = shop_agents or {}

    def info(s: dict, dist: int, always: bool = False) -> ShopInfo:
        return ShopInfo(shop_id=s["id"], name=s["name"], address=shop_agents.get(s["id"], dukaan_address),
                        distance_m=dist, open_24x7=always, lat=s["lat"], lng=s["lng"])

    def fallback(lat: float, lng: float, radius_m: int) -> tuple[list[ShopInfo], int]:
        def near(la, lg):
            found = ripple([{**s, "distance_m": distance_m(la, lg, s["lat"], s["lng"])} for s in SHOPS], radius_m)
            return [info(s, s["distance_m"]) for s in found], ring_of(found, radius_m)
        # buyer is far from the demo shops -> show them around the demo area instead
        got = near(lat, lng)
        return got if got[0] else near(BUYER_DEFAULT["lat"], BUYER_DEFAULT["lng"])

    @agent.on_message(ShopSearch, replies=ShopList)
    async def on_search(ctx: Context, sender: str, req: ShopSearch):
        if req.shop_id:  # QR scan: just the scanned shop
            s = REGISTRY.get(req.shop_id)
            shops = [info(s, 0)] if s else []
            ctx.logger.info(f"QR scan {req.shop_id}: " + (s["name"] if s else "unknown shop"))
            await ctx.send(sender, ShopList(shops=shops, area=s["name"] if s else "", note="" if s else "QR shop not found"))
            return
        lat, lng, area, note = req.lat, req.lng, BUYER_DEFAULT["area"], ""
        if req.place:
            spot = await asyncio.to_thread(geocode, req.place)
            if spot:
                lat, lng, area = spot
            else:
                note = f"'{req.place}' map par nahi mila, demo area use kiya."
        osm = await asyncio.to_thread(osm_shops, lat, lng, req.radius_m)
        if osm:
            always = {s["id"] for s in osm if (real_hours(s) or ([], ""))[1] == "24x7"}
            trick_id = trick_shop([s["id"] for s in osm], avoid=always)
            for s in osm:
                register(demo_shop(s, trick=s["id"] == trick_id))
            found = [info(s, s["distance_m"], s["id"] in always) for s in osm]
            radius, demo = ring_of(osm, req.radius_m), True
        else:
            found, radius = fallback(lat, lng, req.radius_m)
            found.sort(key=lambda s: s.distance_m)
            demo = False
            note = ("Live map search abhi band hai, demo dukaanein dikha rahe hain." if osm is None else
                    f"{area.split(',')[0]} ke paas OpenStreetMap par koi dukaan nahi mili, "
                    "isliye demo dukaanein (Sector 29 Gurgaon) dikha rahe hain.")
        ctx.logger.info(f"{len(found)} shops within {radius} m of {area[:40]} ({'OpenStreetMap' if demo else 'shops.json'}): "
                        + ", ".join(s.name for s in found))
        await ctx.send(sender, ShopList(shops=found, area=area, demo_prices=demo, note=note.strip(), radius_m=radius))

    return agent
