"""Streetwise Guardian: checks every quote, blocks tricks, and builds up to 3 choices for the buyer."""
from uagents import Agent, Context

from models import CheckQuotes, DealOption, PlanPart, Quote, Verdict
import grid
from shop_data import get_shop


def find_problem(q: Quote) -> str | None:
    shop = get_shop(q.shop_id)
    published = {f["name"]: f["amount"] for f in shop["published_fees"]}
    for f in q.fees:
        if f.name not in published:
            return f"hidden Rs {f.amount} {f.name} fee"
        if f.amount > published[f.name]:
            return f"{f.name} fee raised to Rs {f.amount}"
    if not q.open_now:
        return "closed right now"
    return None


def stock(q: Quote) -> dict:
    return {l.name: l for l in q.lines if l.in_stock}


def fee(q: Quote) -> int:
    return sum(f.amount for f in q.fees)


def fill(items: list[str], quotes: list[Quote], start: list[Quote], cost) -> tuple[list[PlanPart], list[str]]:
    """Starts with the given shops and adds others only for the gaps. cost(q, item, plan) picks a shop."""
    plan = {q.shop_id: (q, []) for q in start}
    missing = []
    for i in items:
        options = [q for q, _ in plan.values() if i in stock(q)] or [q for q in quotes if i in stock(q)]
        if not options:
            missing.append(i)
            continue
        q = min(options, key=lambda q: cost(q, i, plan))
        plan.setdefault(q.shop_id, (q, []))[1].append(stock(q)[i])
    parts = [PlanPart(shop_id=q.shop_id, shop_name=q.shop_name, address="", lines=ls, subtotal=sum(l.price for l in ls) + fee(q))
             for q, ls in plan.values() if ls]
    return parts, missing


def plans(items: list[str], quotes: list[Quote], dist: dict[str, int]) -> dict[str, tuple[list[PlanPart], list[str]]]:
    """Three ways to buy the list. Fees count once per shop used."""
    if not quotes:
        return {}
    by_price = lambda q, i, plan: stock(q)[i].price + (0 if q.shop_id in plan else fee(q))  # noqa: E731
    by_dist = lambda q, i, plan: (dist.get(q.shop_id, 10**6), stock(q)[i].price)  # noqa: E731
    full = [q for q in quotes if set(items) <= set(stock(q))]
    # fewest stops: cheapest shop that has everything, else the shop with most items + gap fillers
    main = min(full, key=lambda q: q.total) if full else max(quotes, key=lambda q: (len(set(items) & set(stock(q))), -q.total))
    near_pool = full or quotes
    nearest = min(near_pool, key=lambda q: dist.get(q.shop_id, 10**6))
    out = {"cheapest": fill(items, quotes, [], by_price),
           "fewest": fill(items, quotes, [main], by_price),
           "nearest": fill(items, quotes, [nearest], by_dist)}
    for q in quotes:  # alternatives: each shop as the main shop, so the buyer gets real choices
        out[f"alt:{q.shop_id}"] = fill(items, quotes, [q], by_price)
    return out


def options(items: list[str], quotes: list[Quote], dist: dict[str, int], budget: int | None) -> list[DealOption]:
    """Up to 3 different choices, cheapest first (on a tie, the nearer one). Each label is used once."""
    found: dict[frozenset, dict] = {}
    for kind, (parts, missing) in plans(items, quotes, dist).items():
        if not parts:
            continue
        key = frozenset((p.shop_id, l.name) for p in parts for l in p.lines)
        found.setdefault(key, {"parts": parts, "missing": missing, "kinds": set()})["kinds"].add(kind)
    rank = lambda f: (len(f["missing"]), sum(p.subtotal for p in f["parts"]),  # noqa: E731
                      max(dist.get(p.shop_id, 10**6) for p in f["parts"]))
    main = {"cheapest", "fewest", "nearest"}
    chosen = sorted((f for f in found.values() if f["kinds"] & main), key=rank)[:3]
    chosen += sorted((f for f in found.values() if not f["kinds"] & main), key=rank)[:3 - len(chosen)]
    chosen.sort(key=rank)
    out, near_done = [], False
    for n, f in enumerate(chosen):
        total = sum(p.subtotal for p in f["parts"])
        names = ["Sabse sasta"] if n == 0 and not f["missing"] else []
        if "nearest" in f["kinds"] and not near_done:
            names.append("Sabse paas")
            near_done = True
        if len(f["parts"]) == 1 and not f["missing"] and n > 0:
            names.append("Ek hi dukaan")
        out.append(DealOption(label=" + ".join(names) or "Doosra option", plan=f["parts"], missing=f["missing"],
                              total=total, over_budget=budget is not None and total > budget))
    return out


def make_guardian(seed: str, **agent_kwargs) -> Agent:
    agent = Agent(name="streetwise-guardian", seed=seed, **agent_kwargs)

    @agent.on_message(CheckQuotes, replies=Verdict)
    async def on_check(ctx: Context, sender: str, req: CheckQuotes):
        blocked, ok = {}, []
        for q in req.quotes:
            problem = find_problem(q)
            if problem:
                blocked[q.shop_name] = problem
                grid.blocked(q.shop_id, q.shop_name, problem)
                ctx.logger.warning(f"BLOCKED {q.shop_name}: {problem}")
            else:
                ok.append(q)
        opts = options([i.name for i in req.items], ok, req.distances, req.budget)
        for n, o in enumerate(opts, 1):
            ctx.logger.info(f"option {n} ({o.label}) Rs {o.total}: " + " + ".join(f"{p.shop_name} Rs {p.subtotal}" for p in o.plan))
        await ctx.send(sender, Verdict(request_id=req.request_id, blocked=blocked, options=opts))

    return agent
