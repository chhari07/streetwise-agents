"""Streetwise Grahak (Shopper Agent): the buyer's agent. Chats in ASI:One and runs the whole flow:
item list -> Directory -> quotes from every nearby shop's Dukaan -> Guardian -> best deal -> order.

QR path: "QR bansal: milk, bread" (what a shop's QR sticker sends) asks the scanned shop first;
if it is closed, misses items or is blocked, the nearby open shops are searched (500 m -> 1 km -> 2 km).
After a confirmed order Streetwise asks each shop's agent for its agent charge with Fetch.ai's
AgentPaymentProtocol (this agent is the seller role). The buyer never pays anything extra."""
import asyncio
import json
import os
import re
import time
import uuid

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    TextContent,
    chat_protocol_spec,
)

from uagents_core.contrib.protocols.payment import (CancelPayment, CommitPayment, CompletePayment, Funds, RejectPayment,
                                                    RequestPayment, payment_protocol_spec)

import grid
import locate
import owner
import payments
from models import (CheckQuotes, Item, OrderRequest, OrderResult, QuoteList, QuoteRequest, ShopList, ShopOrder,
                    ShopSearch, Verdict)
import state
from shop_data import BUYER_DEFAULT, CATALOGUE, DEMO_MODE, ITEMS, REGISTRY, label

QUOTE_TIMEOUT_S = 8
ORDER_TIMEOUT_S = owner.OWNER_TIMEOUT_S + 15 if owner.MODE == "panel" else QUOTE_TIMEOUT_S  # owner gets time to tap
QR = re.compile(r"^\s*(?:qr|scan(?:ned)?)\s*[:#-]?\s*([a-z0-9][\w-]*)\s*[:,\-]?\s*(.*)$", re.I | re.S)
DEAL_TTL_S = 3 * 3600  # a saved deal older than this is dropped (prices may have changed)
STATE_FILE = "grahak_state.json"
LIVE_ASK = re.compile(r"^(?:send |share |bhejo |my |meri |current |live |gps |\W)*(?:location|loc|lokeshan)"
                      r"(?: link| bhejo| bhejna| share| please| do| de do)*$", re.I)  # "live location", "meri location bhejo"
DIRECTORY_TIMEOUT_S = 25  # the live map search takes ~5 s, more when the service is slow
YES = {"haan", "han", "haa", "ha", "yes", "y", "ok", "okay", "order", "confirm", "kar do", "haan ji"}
YES_WORDS = {"haan", "han", "haa", "ha", "yes", "y", "ok", "okay", "order", "confirm", "ji", "theek", "thik", "hai", "done"}
# words that can sit around a pick ("haan=option 1", "option 2 wala please", "1 order kar do") without making it a new request
PICK_WORDS = YES_WORDS | {"option", "opt", "no", "number", "wala", "vala", "wali", "please", "plz", "kar", "karo", "do",
                          "de", "dena", "le", "lo", "lena", "chahiye", "select", "choose", "go", "with", "the", "ka", "ki"}
# fallback parser when the LLM is off or fails: what buyers type -> item key, longest words first
KEYWORDS = sorted(((w, i["key"]) for i in ITEMS for w in i["words"]), key=lambda kv: -len(kv[0]))
RECIPES = {"paneer butter masala": {"paneer_200g": 2, "butter_100g": 1, "cream_200ml": 1, "tomato_1kg": 1,
                                    "onion_1kg": 1, "ginger_garlic_paste": 1, "garam_masala_50g": 1, "kasuri_methi": 1}}

LLM_PROMPT = """You turn a shopping or cooking request from India into a JSON shopping list.
Use ONLY these item keys (key: what it is): {catalogue}
Reply with JSON only, no other text: {{"items": [{{"name": "<key>", "qty": <whole number of packs>}}], "unknown": ["<things the buyer asked for that match no key>"], "budget": <rupees or null>, "location": "<area, locality, city or pincode the buyer mentioned, or null>"}}
- For a dish, list the ingredients a home cook would buy, with sensible pack counts for the number of people. Leave out dish ingredients that have no key (don't put those in "unknown").
- A brand or a near match is fine when it's clearly the same thing (e.g. "Amul butter" -> butter_100g, "Coca-Cola" -> cold_drink_750ml).
- "unknown" lists only products the buyer named directly that have no matching key, in the buyer's words (e.g. "Dettol soap" has a key, "printer paper" doesn't).
- If the message is just a greeting or not about shopping, return {{"items": [], "unknown": [], "budget": null, "location": null}}.
- "location" is only where the buyer is (e.g. "Sector 14 Gurgaon", "Koramangala Bangalore", "110024"), never a shop name."""


def parse_budget(text: str) -> int | None:
    m = re.search(r"(?:\brs\.?|₹|\binr)\s*(\d[\d,]*)", text, re.I) or re.search(r"(\d[\d,]*)\s*(?:rs\b|rupees|₹)", text, re.I)
    return int(m.group(1).replace(",", "")) if m else None


def parse_location(text: str) -> str | None:
    """'... near Sector 14 Gurgaon, Rs 500 tak' -> 'Sector 14 Gurgaon'. Also a 6-digit pincode."""
    m = re.search(r"\b(?:near|around|in|at|location|area)\s*:?\s+(.+?)(?=\s*(?:[,.;!?]|$|\bfor\b|\brs\b|₹|\bunder\b|\btak\b|\bbudget\b|\bme(?:in)?\b))",
                  text, re.I)
    if m and len(re.sub(r"[^A-Za-z]", "", m.group(1))) >= 3:
        return m.group(1).strip()
    m = re.search(r"\b\d{6}\b", text)
    return m.group(0) if m else None


def keyword_parse(text: str) -> list[Item]:
    t = text.lower()
    for dish, items in RECIPES.items():
        if dish in t:
            return [Item(name=k, qty=q) for k, q in items.items()]
    place = parse_location(text)
    if place:  # "near Raipur" shouldn't match "rai"
        t = t.replace(place.lower(), " ")
    found = {}
    for word, key in KEYWORDS:  # longest first, and each match is cut out: "peanut butter" isn't also "butter"
        pattern = r"\b" + re.escape(word) + r"s?\b"
        if re.search(pattern, t):
            found[key] = 1
            t = re.sub(pattern, " ", t)
    return [Item(name=k, qty=q) for k, q in found.items()]


GREETINGS = {"hi", "hello", "hey", "namaste", "namaskar", "hii", "helo"}


def parse_qr(text: str) -> tuple[str | None, str]:
    """'QR bansal: milk, bread' -> ('bansal', 'milk, bread'). Only known shop ids count, so 'qr milk' isn't a scan."""
    m = QR.match(text)
    if m and m.group(1).lower() in REGISTRY:
        return m.group(1).lower(), m.group(2).strip()
    return None, text


def area_reply(text: str) -> str | None:
    """Answer to "Aap kahan hain?": 'near Guna', 'for guna', 'Lajpat Nagar Delhi', '110024' -> the area."""
    place = parse_location(text)
    if place:
        return place
    t = re.sub(r"^(?:for|near|in|at|mera area|my area|area|location|i am in|main)\s+", "", text.strip(" .!?"), flags=re.I)
    t = re.sub(r"(?:\s+(?:mein|me|hoon|hu|hun|hai))+$", "", t, flags=re.I).strip(" .,")
    if t.lower() in GREETINGS or keyword_parse(t) or len(t.split()) > 5 or len(re.sub(r"[^A-Za-z0-9]", "", t)) < 3:
        return None
    return t


def make_grahak(seed: str, directory: str, guardian: str, llm=None, **agent_kwargs) -> Agent:
    agent = Agent(name="streetwise-grahak", seed=seed, handle_messages_concurrently=True, **agent_kwargs)
    chat = Protocol(spec=chat_protocol_spec)
    pay = Protocol(spec=payment_protocol_spec, role="seller")  # Streetwise charges each shop's agent per order
    qr_shop: dict[str, str] = {}  # buyer address -> shop id they scanned, until they say what they need
    # Saved to state/grahak_state.json after every change, so a restart doesn't lose a buyer's "1" / "haan".
    saved = state.load(STATE_FILE)
    pending: dict[str, dict] = {  # buyer address -> the deal waiting for "haan"
        b: {**d, "verdict": Verdict(**d["verdict"])} for b, d in saved.get("pending", {}).items()
        if time.time() - d.get("at", 0) < DEAL_TTL_S}
    # buyer address -> last area they typed, or their live location {"lat", "lng", "acc"} (GPS link / Maps link / lat,lng)
    places: dict[str, str | dict] = saved.get("places", {})
    sessions: dict[str, object] = {}  # buyer address -> session of their last chat message (to reply from the GPS link)
    waiting: dict[str, tuple] = {  # buyer address -> (items, unknown, budget) until they tell us their area
        b: ([Item(**i) for i in w[0]], w[1], w[2]) for b, w in saved.get("waiting", {}).items()}

    def save_state():
        state.save(STATE_FILE, {
            "pending": {b: {**d, "verdict": d["verdict"].model_dump()} for b, d in pending.items()},
            "places": places,
            "waiting": {b: ([i.model_dump() for i in w[0]], w[1], w[2]) for b, w in waiting.items()},
        })

    async def reply(ctx: Context, to: str, text: str, end: bool = False):
        # Keep the session open until the order is done; an end-session after every reply makes
        # ASI:One send the buyer's next message ("haan") to its own assistant instead of Grahak.
        content = [TextContent(type="text", text=text)] + ([EndSessionContent(type="end-session")] if end else [])
        await ctx.send(to, ChatMessage(content=content))

    async def make_list(ctx: Context, text: str) -> tuple[list[Item], list[str], int | None, str | None]:
        budget, place = parse_budget(text), parse_location(text)
        if llm is not None:
            try:
                cat = ", ".join(f"{k}: {v}" for k, v in CATALOGUE.items())
                out = await asyncio.to_thread(lambda: llm.chat.completions.create(
                    model="asi1", max_tokens=600,
                    messages=[{"role": "system", "content": LLM_PROMPT.format(catalogue=cat)}, {"role": "user", "content": text}]))
                raw = str(out.choices[0].message.content)
                data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
                items = [Item(name=i["name"], qty=max(1, int(i.get("qty", 1)))) for i in data.get("items", []) if i.get("name") in CATALOGUE]
                unknown = [str(u) for u in data.get("unknown") or [] if str(u).strip()]
                return items, unknown, data.get("budget") or budget, data.get("location") or place
            except Exception as err:
                ctx.logger.warning(f"LLM list failed, using keyword parser: {err}")
        return keyword_parse(text), [], budget, place

    async def ask(ctx: Context, to: str, msg, resp_type, timeout: int = QUOTE_TIMEOUT_S):
        try:
            resp, status = await asyncio.wait_for(ctx.send_and_receive(to, msg, response_type=resp_type, timeout=timeout), timeout)
            return resp
        except asyncio.TimeoutError:
            ctx.logger.warning(f"no reply from {to[:16]}… in {timeout}s, skipping")
            return None

    def by_agent(shops) -> dict[str, list]:
        """Groups shops by Dukaan agent address: one request per agent (uAgents can't match
        two replies from the same agent at once, and today one agent serves all demo shops)."""
        groups: dict[str, list] = {}
        for s in shops:
            groups.setdefault(s.address, []).append(s)
        return groups

    async def find_deal(ctx: Context, buyer: str, text: str):
        scanned, text = parse_qr(text)
        scanned = scanned or qr_shop.pop(buyer, None)
        if scanned and not text:
            qr_shop[buyer] = scanned
            await reply(ctx, buyer, f"Namaste! Aapne **{REGISTRY[scanned]['name']}** ka QR scan kiya. Kya chahiye?")
            return
        items, unknown, budget, place = await make_list(ctx, text)
        if place:
            places[buyer] = place
            save_state()
        if not items and unknown:
            await reply(ctx, buyer, "Maaf kijiye, yeh abhi list mein nahi hai: " + ", ".join(unknown) + ".\n\n"
                                    "Streetwise par roz ka kirana saaman milta hai, jaise doodh, atta, dal, chawal, tel, "
                                    "masale, sabzi, chai, coffee, Maggi, biscuit, sabun, detergent. Kuch aur bataiye?")
            return
        if not items and buyer in pending:
            n = len(pending[buyer]["verdict"].options)
            await reply(ctx, buyer, f"Samjha nahi. Order ke liye sirf **{' / '.join(str(i) for i in range(1, n + 1))}** "
                                    "ya **haan** likhein, ya naya saaman bataiye.")
            return
        if not items:
            await reply(ctx, buyer, "Namaste! Main **Streetwise** hoon, aapke mohalle ki dukaanon se daam poochta hoon.\n\n"
                                    "Bataiye kya chahiye aur aap kahan hain, jaise: "
                                    "*Paneer butter masala for 4 near Sector 29 Gurgaon, Rs 500 tak*")
            return
        if scanned:  # the shop's QR already says where the buyer is
            await get_quotes(ctx, buyer, items, unknown, budget, scanned=scanned)
            return
        if not places.get(buyer):
            waiting[buyer] = (items, unknown, budget)
            save_state()
            await reply(ctx, buyer, where_are_you(buyer))
            return
        await get_quotes(ctx, buyer, items, unknown, budget)

    def where_are_you(buyer: str) -> str:
        return ("Aap kahan hain?\n\n"
                f"📍 **Live location bhejein** (sabse sahi, ek tap): {locate.link(buyer)}\n\n"
                "Ya apna area / pincode likhein, jaise *Lajpat Nagar Delhi* ya *110024*, ya Google Maps ka link paste karein.")

    async def got_location(ctx: Context, buyer: str, spot: dict, rest: str = "") -> bool:
        """Saves the buyer's live location / pasted place. Returns True when this message is fully handled."""
        places[buyer] = spot
        ctx.logger.info(f"location from {buyer[:16]}…: {spot}")
        if buyer in waiting:
            got = waiting.pop(buyer)
            save_state()
            await get_quotes(ctx, buyer, *got)
            return True
        save_state()
        if rest.strip(" .!"):
            return False  # "milk and bread <maps link>": carry on with the items
        await reply(ctx, buyer, "📍 Location mil gayi! Ab bataiye kya chahiye, jaise *doodh, bread aur anda*.")
        return True

    async def try_scanned(ctx: Context, buyer: str, items: list[Item], unknown: list[str], budget: int | None, sid: str):
        """Asks the scanned shop first. Returns (handled, why it wasn't, the shop) so the caller can search around it."""
        rid = uuid.uuid4().hex[:8]
        sl = await ask(ctx, directory, ShopSearch(lat=0, lng=0, shop_id=sid), ShopList)
        if not sl or not sl.shops:
            return False, "QR wali dukaan nahi mili, paas ki dukaanein dekh raha hoon.", None
        s = sl.shops[0]
        ql = await ask(ctx, s.address, QuoteRequest(request_id=rid, items=items, shop_ids=[sid]), QuoteList)
        q = ql.quotes[0] if ql and ql.quotes else None
        if q is None:
            return False, f"**{s.name}** se jawab nahi aaya. Paas ki dukaanein dekh raha hoon:", s
        v = await ask(ctx, guardian, CheckQuotes(request_id=rid, items=items, quotes=[q], budget=budget, distances={sid: 0}), Verdict)
        if v and v.options and not v.blocked and not v.options[0].missing:
            await present(ctx, buyer, rid, items, unknown, budget, ShopList(shops=[s], area=s.name), [q], v,
                          [f"Aapne **{s.name}** ka QR scan kiya: dukaan khuli hai aur sab saaman hai."])
            return True, "", s
        if not q.open_now:
            why = f"Aapne **{s.name}** ka QR scan kiya, par dukaan abhi band hai. Paas ki khuli dukaanein dekh raha hoon (500 m → 1 km → 2 km)."
        elif v and v.blocked:
            why = f"Guardian ne **{s.name}** ko roka ({next(iter(v.blocked.values()))}). Paas ki dukaanein dekh raha hoon."
        else:
            gone = ", ".join(label(l.name) for l in q.lines if not l.in_stock)
            why = f"**{s.name}** ke paas yeh nahi hai: {gone}. Paas ki dukaanein bhi dekh raha hoon."
        ctx.logger.info(f"QR {s.name}: not usable alone -> searching nearby")
        return False, why, s

    async def get_quotes(ctx: Context, buyer: str, items: list[Item], unknown: list[str], budget: int | None,
                         scanned: str | None = None):
        ctx.logger.info("item list: " + ", ".join(f"{i.name} x{i.qty}" for i in items) + (f" | budget Rs {budget}" if budget else ""))
        spot = places.get(buyer, "")
        intro: list[str] = []
        if isinstance(spot, dict) and "lat" in spot:  # live location: search around the buyer's own pin
            search = ShopSearch(lat=spot["lat"], lng=spot["lng"], live=True)
            intro.append(f"📍 Aapki live location ke paas ki dukaanein (±{spot['acc']} m)." if spot.get("acc")
                         else "📍 Aapke bheje Maps pin ke paas ki dukaanein.")
        else:
            place = spot.get("place", "") if isinstance(spot, dict) else spot
            search = ShopSearch(lat=BUYER_DEFAULT["lat"], lng=BUYER_DEFAULT["lng"], place=place)
        if scanned:
            done, why, here = await try_scanned(ctx, buyer, items, unknown, budget, scanned)
            if done:
                return
            intro.append(why)
            if here:
                search = ShopSearch(lat=here.lat, lng=here.lng)
        shops = await ask(ctx, directory, search, ShopList, timeout=DIRECTORY_TIMEOUT_S)
        if not shops or not shops.shops:
            await reply(ctx, buyer, "Maaf kijiye, aapke paas koi dukaan nahi mili. Apna area dobara likhein, jaise *near Lajpat Nagar Delhi*, "
                                    f"ya live location bhejein: {locate.link(buyer)}")
            return
        rid = uuid.uuid4().hex[:8]
        groups = by_agent(shops.shops)
        results = await asyncio.gather(*(ask(ctx, a, QuoteRequest(request_id=rid, items=items, shop_ids=[s.shop_id for s in ss]),
                                             QuoteList) for a, ss in groups.items()))
        quotes = [q for r in results if r for q in r.quotes]
        ctx.logger.info(f"{len(quotes)}/{len(shops.shops)} shops replied")
        dist = {s.shop_id: s.distance_m for s in shops.shops}
        tag = {s.shop_id: " · 24x7" for s in shops.shops if s.open_24x7}
        verdict = await ask(ctx, guardian, CheckQuotes(request_id=rid, items=items, quotes=quotes, budget=budget,
                                                       distances=dist), Verdict)
        if not verdict or not verdict.options:
            why = [f"- **{name}**: {reason}" for name, reason in (verdict.blocked.items() if verdict else [])]
            text = "\n\n".join(intro + ["Maaf kijiye, abhi koi dukaan yeh saaman nahi de paa rahi."])
            if why:
                text += "\n\nGuardian ne yeh dukaanein roki:\n" + "\n".join(why)
                if any("closed" in r for r in verdict.blocked.values()):
                    text += "\n\nDukaanein abhi band hain. Subah dobara poochiye."
            await reply(ctx, buyer, text)
            return
        await present(ctx, buyer, rid, items, unknown, budget, shops, quotes, verdict, intro)

    async def present(ctx: Context, buyer: str, rid: str, items: list[Item], unknown: list[str], budget: int | None,
                      shops: ShopList, quotes: list, verdict: Verdict, intro: list[str]):
        dist = {s.shop_id: s.distance_m for s in shops.shops}
        tag = {s.shop_id: " · 24x7" for s in shops.shops if s.open_24x7}
        addr = {s.shop_id: s.address for s in shops.shops}
        pending[buyer] = {"rid": rid, "verdict": verdict, "addr": addr, "at": time.time()}
        save_state()
        where = shops.area.split(",")[0] + ", " + shops.area.split(",")[1].strip() if shops.area.count(",") else shops.area
        ring = f" · {shops.radius_m // 1000} km tak" if shops.radius_m >= 1000 else f" · {shops.radius_m} m ke andar" if shops.radius_m else ""
        lines = intro + ([""] if intro else []) + [f"Item list: " + ", ".join(f"{label(i.name)} x{i.qty}" for i in items), f"Area: {where}{ring}"]
        if unknown:
            lines.append("Yeh abhi list mein nahi hai: " + ", ".join(unknown))
        if shops.note:
            lines.append(f"_{shops.note}_")
        lines += ["", f"{len(quotes)} dukaanon ne daam bheje. Aapke options:"]
        for n, o in enumerate(verdict.options, 1):
            lines.append(f"\n**{n}. {o.label}: Rs {o.total}**" + (f" (budget Rs {budget} se zyada)" if o.over_budget else ""))
            for p in o.plan:
                got = ", ".join(f"{label(l.name)} x{l.qty} (Rs {l.price})" for l in p.lines)
                lines.append(f"- **{p.shop_name}** ({dist[p.shop_id]} m{tag.get(p.shop_id, '')}): {got} = Rs {p.subtotal}")
            if o.missing:
                lines.append("- Isme nahi mila: " + ", ".join(label(m) for m in o.missing))
        if verdict.blocked:
            lines.append("")
        for name, why in verdict.blocked.items():
            lines.append(f"Guardian ne **{name}** ko block kiya: {why}")
        if shops.demo_prices:
            lines.append("\n_Yeh asli paas ki dukaanein hain (OpenStreetMap), par abhi Streetwise par nahi hain: daam demo hain._")
        nums = " / ".join(str(n) for n in range(1, len(verdict.options) + 1))
        lines.append(f"\nKaunsa order karein? Sirf number likhein: **{nums}**. Ya **haan** likhein option 1 ke liye.")
        await reply(ctx, buyer, "\n".join(lines))

    def choice(text: str, count: int) -> int | None:
        """'2', 'option 2', '2 wala', 'haan=option 1' -> that option; 'haan', 'ok kar do' -> 1;
        anything with other words or numbers (e.g. '2 maggi') -> None (treated as a new request)."""
        t = text.lower().strip(" .!")
        if t in YES:
            return 1
        nums, words = re.findall(r"\d+", t), re.findall(r"[a-z]+", t)
        if not set(words) <= PICK_WORDS:
            return None
        if len(set(nums)) == 1 and 1 <= int(nums[0]) <= count:
            return int(nums[0])
        if not nums and set(words) & YES_WORDS:
            return 1
        return None

    async def place_order(ctx: Context, buyer: str, pick: int):
        deal = pending.pop(buyer)
        save_state()
        v: Verdict = deal["verdict"]
        chosen = v.options[pick - 1]
        groups: dict[str, list] = {}
        for p in chosen.plan:
            groups.setdefault(deal["addr"][p.shop_id], []).append(p)
        results = await asyncio.gather(*(ask(ctx, a, OrderRequest(request_id=deal["rid"], orders=[
            ShopOrder(shop_id=p.shop_id, lines=p.lines, quoted_total=p.subtotal) for p in ps]), OrderResult, timeout=ORDER_TIMEOUT_S)
            for a, ps in groups.items()))
        got = {c.shop_name: c for r in results if r for c in r.confirms}
        confirms = [got.get(p.shop_name) for p in chosen.plan]
        out = [f"Option {pick} ({chosen.label}):"]
        for p, c in zip(chosen.plan, confirms):
            if c is None:
                out.append(f"- {p.shop_name}: koi jawab nahi aaya")
            elif c.accepted:
                out.append(f"- **{c.shop_name}**: confirmed, Rs {c.final_total}")
            else:
                out.append(f"- **{c.shop_name}**: not confirmed ({c.note})")
        ok = all(c and c.accepted for c in confirms)
        order_id = f"SW-{'DEMO-' if DEMO_MODE else ''}{deal['rid'][:4].upper()}"
        if not ok:
            head = "Order ka status:"
        elif DEMO_MODE:
            head = f"Demo order confirmed! (Order ID: {order_id})"
            out.append("\n_Yeh demo order hai: dukaan ko asli message nahi gaya, koi paisa nahi laga. "
                       "Jab dukaan Streetwise par judegi, tab asli order jaayega._")
        else:
            head = f"Order confirmed! (Order ID: {order_id}) Pickup in about 20 min, pay by UPI at the shop."
        ctx.logger.info(f"{order_id}: {'confirmed' if ok else 'not fully confirmed'}")
        if ok:
            out.append("\n" + receipt(order_id, chosen, confirms, deal["addr"]))
            grid.ordered(order_id, [p.shop_id for p in chosen.plan], [p.shop_name for p in chosen.plan], chosen.total)
            # Streetwise's agent charge, paid by each shop's agent (Fetch.ai AgentPaymentProtocol)
            chain = payments.MODE == "testnet"
            for a in groups:
                await ctx.send(a, RequestPayment(
                    accepted_funds=[Funds(amount=str(payments.AGENT_CHARGE_FET), currency="FET", payment_method="fet_direct") if chain
                                    else Funds(amount=str(payments.AGENT_CHARGE_INR), currency="INR", payment_method="demo")],
                    recipient=str(agent.wallet.address()) if chain else agent.address, deadline_seconds=300, reference=order_id,
                    description=f"Streetwise agent charge (Rs {payments.AGENT_CHARGE_INR} per order)",
                    metadata={"chain": payments.CHAIN} if chain else None))
            out.append(f"\n_Aapko koi extra charge nahi. Dukaan Streetwise ko agent charge deti hai: Fetch.ai Payment Protocol, "
                       + (f"{payments.AGENT_CHARGE_FET} FET on the Fetch.ai testnet" if chain else "demo") + "._")
        await reply(ctx, buyer, head + "\n" + "\n".join(out), end=True)

    @chat.on_message(ChatMessage)
    async def on_chat(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))
        # ASI:One passes the "@agent1q... " / "@streetwise-grahak" mention along with the text: drop it
        text = re.sub(r"@[\w.-]+", " ", msg.text()).strip()
        ctx.logger.info(f"From {sender[:16]}…: {text}")
        sessions[sender] = ctx.session
        if LIVE_ASK.match(text.strip()):  # "live location", "meri location bhejo": send the GPS link
            await reply(ctx, sender, f"📍 Is link par tap karke **Share my live location** dabaiye: {locate.link(sender)}\n\n"
                                     "Location aate hi main aapke paas ki dukaanein dhoondh dunga.")
            return
        if sender in waiting and text.strip(" .!").lower() in {"done", "ho gaya", "sent", "bhej diya", "ok"}:
            await reply(ctx, sender, "Abhi location nahi aayi. " + where_are_you(sender))
            return
        spot, rest = await asyncio.to_thread(locate.find, text)
        if spot and await got_location(ctx, sender, spot, rest):
            return
        text = rest if spot else text
        pick = choice(text, len(pending[sender]["verdict"].options)) if sender in pending else None
        area = area_reply(text) if sender in waiting and not pick else None
        if area:
            places[sender] = area
            got = waiting.pop(sender)
            save_state()
            await get_quotes(ctx, sender, *got)
        elif pick:
            await place_order(ctx, sender, pick)
        elif sender in pending and re.fullmatch(r"\d", text.strip(" .!")):
            n = len(pending[sender]["verdict"].options)
            await reply(ctx, sender, f"Option {text.strip(' .!')} nahi hai. Reply **{' / '.join(str(i) for i in range(1, n + 1))}**.")
        else:
            if waiting.pop(sender, None):
                save_state()
            await find_deal(ctx, sender, text)

    @chat.on_message(ChatAcknowledgement)
    async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        pass

    @agent.on_interval(period=1.0)
    async def live_locations(ctx: Context):
        """GPS from the live-location link (locate.py) -> search shops around it, in the buyer's chat session."""
        for buyer, lat, lng, acc in locate.drain():
            if buyer in sessions:
                ctx._session = sessions[buyer]  # reply in the same ASI:One chat the buyer is in
            await got_location(ctx, buyer, {"lat": lat, "lng": lng, "acc": acc})

    def receipt(order_id: str, chosen, confirms, addr: dict[str, str]) -> str:
        """Verified Receipt card: every line and every charge, checked before the buyer pays."""
        rows, fees = [], 0
        for p, c in zip(chosen.plan, confirms):
            items = sum(l.price for l in p.lines)
            rows += [f"| {label(l.name)} x{l.qty} | {p.shop_name} | {l.price} |" for l in p.lines]
            fees += p.subtotal - items
        rows.append(f"| Published fees | | {fees} |")
        rows.append("| Hidden fees | | **0** |")
        signers = " · ".join(f"{p.shop_name} `{addr[p.shop_id][:10]}…{addr[p.shop_id][-4:]}`" for p in chosen.plan)
        return "\n".join([f"**VERIFIED RECEIPT · {order_id}**", "", "| Item | Shop | Rs |", "|---|---|---|", *rows,
                           f"| **TOTAL** | | **Rs {chosen.total}** |", "",
                           "Trust Agent checked: ✓ shops open ✓ no hidden fee ✓ price same as quote ✓ owner confirmed",
                           f"Agreed by: Shopper Agent `{agent.address[:10]}…{agent.address[-4:]}` · {signers}"])

    @pay.on_message(CommitPayment)
    async def on_commit(ctx: Context, sender: str, msg: CommitPayment):
        tx, f = msg.transaction_id, msg.funds
        if f.payment_method == "fet_direct":
            try:
                good = await payments.verify(tx, str(agent.wallet.address()), f.amount)
            except Exception as err:
                ctx.logger.warning(f"PAYMENT check failed for tx {tx}: {err}")
                good = False
            if not good:
                await ctx.send(sender, CancelPayment(transaction_id=tx, reason="transaction not found or wrong amount"))
                return
            ctx.logger.info(f"PAYMENT verified on-chain: {f.amount} FET for {msg.reference}, tx {tx}")
            grid.paid(f"{f.amount} FET agent charge for {msg.reference} (verified on-chain)", tx)
        else:
            ctx.logger.info(f"PAYMENT received: {f.amount} {f.currency} for {msg.reference}, tx {tx}")
            grid.paid(f"{f.amount} {f.currency} agent charge for {msg.reference} (demo)", tx)
        await ctx.send(sender, CompletePayment(transaction_id=tx))

    @pay.on_message(RejectPayment)
    async def on_reject(ctx: Context, sender: str, msg: RejectPayment):
        ctx.logger.warning(f"PAYMENT rejected by {sender[:16]}…: {msg.reason}")

    agent.include(chat, publish_manifest=True)
    agent.include(pay, publish_manifest=True)
    return agent
