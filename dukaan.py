"""Streetwise Dukaan (Merchant Agent): gives quotes and takes orders for a shop.

Every Streetwise shop runs its own Dukaan: make_dukaan(seed, shop=...). Real nearby shops from
OpenStreetMap haven't joined yet, so one shared demo Dukaan (shop=None) answers for all of them.

Orders: the shop owner taps YES / NO (owner.py). Payment: after a confirmed order, Streetwise asks
the shop's agent for its agent charge with Fetch.ai's AgentPaymentProtocol (this agent is the buyer
role). With STREETWISE_PAYMENT=testnet it is a real FET transfer on the Fetch.ai testnet (payments.py)."""
import uuid

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.payment import (CancelPayment, CommitPayment, CompletePayment, RejectPayment,
                                                    RequestPayment, payment_protocol_spec)

import owner
import payments
from models import Fee, OrderConfirm, OrderRequest, OrderResult, Quote, QuoteLine, QuoteList, QuoteRequest
from shop_data import DEMO_MODE, get_shop, is_open, label


def quote_for(shop: dict, req: QuoteRequest) -> Quote:
    lines = []
    for it in req.items:
        price = shop["prices"].get(it.name)
        in_stock = price is not None and it.name not in shop["out_of_stock"]
        lines.append(QuoteLine(name=it.name, qty=it.qty, price=(price or 0) * it.qty if in_stock else 0, in_stock=in_stock))
    fees = [Fee(**f) for f in shop["published_fees"] + shop["extra_fees"]]
    subtotal = sum(l.price for l in lines if l.in_stock)
    total = subtotal + (sum(f.amount for f in fees) if subtotal else 0)
    return Quote(request_id=req.request_id, shop_id=shop["id"], shop_name=shop["name"],
                 open_now=is_open(shop), lines=lines, fees=fees if subtotal else [], total=total)


async def confirm_order(shop: dict, request_id: str, lines: list[QuoteLine], quoted_total: int) -> OrderConfirm:
    # Re-price from today's list so a changed price can't slip through silently.
    final = sum(shop["prices"].get(l.name, 0) * l.qty for l in lines)
    final += sum(f["amount"] for f in shop["published_fees"])
    if final > quoted_total:
        return OrderConfirm(request_id=request_id, shop_name=shop["name"], accepted=False, final_total=final,
                            note=f"price changed: Rs {quoted_total} -> Rs {final}")
    if owner.MODE == "panel":
        ans = await owner.ask(shop["name"], [(label(l.name), l.qty, l.price) for l in lines], final)
        accepted = bool(ans)
        note = {True: "owner tapped YES", False: "owner tapped NO"}.get(ans, f"no reply from the owner in {owner.OWNER_TIMEOUT_S} s")
    elif final <= shop["auto_accept_under"]:
        accepted, note = True, "auto-accepted by the owner's rule"
    else:
        accepted, note = False, f"above Rs {shop['auto_accept_under']}: waiting for the owner to accept"
    return OrderConfirm(request_id=request_id, shop_name=shop["name"], accepted=accepted, final_total=final, note=note)


def make_dukaan(seed: str, shop: dict | None = None, **agent_kwargs) -> Agent:
    # concurrent: one order waiting for the owner's tap must not hold up quotes for other buyers
    agent = Agent(name=f"streetwise-dukaan-{shop['id']}" if shop else "streetwise-dukaan", seed=seed,
                  handle_messages_concurrently=True, **agent_kwargs)
    pay = Protocol(spec=payment_protocol_spec, role="buyer")  # the shop pays Streetwise's agent charge

    def shops_for(ids: list[str]) -> list[dict]:
        return [shop] if shop else [get_shop(i) for i in ids]

    @agent.on_message(QuoteRequest, replies=QuoteList)
    async def on_quote(ctx: Context, sender: str, req: QuoteRequest):
        quotes = [quote_for(s, req) for s in shops_for(req.shop_ids)]
        for q in quotes:
            ctx.logger.info(f"{q.shop_name}: quote Rs {q.total} for {sum(l.in_stock for l in q.lines)}/{len(q.lines)} items"
                            + ("" if q.open_now else " (shop is closed)"))
        await ctx.send(sender, QuoteList(quotes=quotes))

    @agent.on_message(OrderRequest, replies=OrderResult)
    async def on_order(ctx: Context, sender: str, req: OrderRequest):
        confirms = []
        for o in req.orders:
            c = await confirm_order(shop or get_shop(o.shop_id), req.request_id, o.lines, o.quoted_total)
            ctx.logger.info(f"{'DEMO ORDER ' if DEMO_MODE else ''}{c.shop_name}: order Rs {c.final_total} -> {'ACCEPTED' if c.accepted else 'NOT accepted'} ({c.note})")
            confirms.append(c)
        await ctx.send(sender, OrderResult(confirms=confirms))

    @pay.on_message(RequestPayment)
    async def on_request_payment(ctx: Context, sender: str, req: RequestPayment):
        funds = req.accepted_funds[0]
        ctx.logger.info(f"PAYMENT requested: {funds.amount} {funds.currency} ({funds.payment_method}) for {req.reference}")
        if funds.payment_method == "fet_direct":
            try:
                tx = await payments.send(agent.wallet, req.recipient, funds.amount)
            except Exception as err:
                ctx.logger.warning(f"PAYMENT failed: {err}")
                await ctx.send(sender, RejectPayment(reason=f"testnet transfer failed: {str(err)[:120]}"))
                return
            ctx.logger.info(f"PAYMENT sent on {payments.CHAIN}: {funds.amount} FET -> {req.recipient}, tx {tx}")
        else:
            tx = f"DEMO-TX-{uuid.uuid4().hex[:8].upper()}"
            ctx.logger.info(f"PAYMENT (demo, no chain): tx {tx}")
        await ctx.send(sender, CommitPayment(funds=funds, recipient=req.recipient, transaction_id=tx,
                                             reference=req.reference, description=req.description))

    @pay.on_message(CompletePayment)
    async def on_complete(ctx: Context, sender: str, msg: CompletePayment):
        ctx.logger.info(f"PAYMENT complete: {msg.transaction_id}")

    @pay.on_message(CancelPayment)
    async def on_cancel(ctx: Context, sender: str, msg: CancelPayment):
        ctx.logger.warning(f"PAYMENT cancelled: {msg.transaction_id} ({msg.reason})")

    agent.include(pay)
    return agent
