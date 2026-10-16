"""Messages the Streetwise agents send to each other (agent-to-agent, not the ASI:One chat)."""
from uagents import Model


class Item(Model):
    name: str  # catalogue key, e.g. "paneer_200g"
    qty: int = 1


class QuoteLine(Model):
    name: str
    qty: int
    price: int  # rupees for the whole line (unit price x qty)
    in_stock: bool


class Fee(Model):
    name: str
    amount: int


class ShopSearch(Model):  # Grahak -> Directory
    lat: float  # used when place is empty or can't be found
    lng: float
    radius_m: int = 500  # first ring; Directory widens 500 m -> 1 km -> 2 km until it has enough shops
    place: str = ""  # area / locality / pincode the buyer typed, e.g. "Sector 14 Gurgaon"
    shop_id: str = ""  # set when the buyer scanned a shop's QR: Directory returns just that shop


class ShopInfo(Model):
    shop_id: str
    name: str
    address: str  # the shop's own Merchant (Dukaan) agent address
    distance_m: int
    open_24x7: bool = False
    lat: float = 0.0
    lng: float = 0.0


class ShopList(Model):  # Directory -> Grahak
    shops: list[ShopInfo]
    area: str = ""  # where Directory searched
    demo_prices: bool = False  # True = real nearby shops, not on Streetwise yet, so prices are demo
    note: str = ""
    radius_m: int = 0  # the ring the search ended at (500, 1000 or 2000)


class QuoteRequest(Model):  # Grahak -> each Dukaan agent (one request per agent, for all its shops)
    request_id: str
    items: list[Item]
    shop_ids: list[str] = []


class Quote(Model):  # Dukaan -> Grahak
    request_id: str
    shop_id: str
    shop_name: str
    open_now: bool
    lines: list[QuoteLine]
    fees: list[Fee]
    total: int  # in-stock lines + fees


class QuoteList(Model):  # Dukaan -> Grahak
    quotes: list[Quote]


class CheckQuotes(Model):  # Grahak -> Guardian
    request_id: str
    items: list[Item]
    quotes: list[Quote]
    budget: int | None = None
    distances: dict[str, int] = {}  # shop_id -> metres from the buyer (for the "nearest" choice)


class PlanPart(Model):
    shop_id: str
    shop_name: str
    address: str
    lines: list[QuoteLine]
    subtotal: int


class DealOption(Model):  # one choice the buyer can pick (reply 1, 2 or 3)
    label: str  # e.g. "Sabse sasta"
    plan: list[PlanPart]
    missing: list[str]  # items this choice doesn't cover
    total: int
    over_budget: bool


class Verdict(Model):  # Guardian -> Grahak
    request_id: str
    blocked: dict[str, str]  # shop_name -> reason
    options: list[DealOption]  # 1-3 checked choices, cheapest first; empty = nothing found


class ShopOrder(Model):
    shop_id: str
    lines: list[QuoteLine]
    quoted_total: int


class OrderRequest(Model):  # Grahak -> each Dukaan agent (one request per agent, for all its shops)
    request_id: str
    orders: list[ShopOrder]


class OrderConfirm(Model):  # Dukaan -> Grahak
    request_id: str
    shop_name: str
    accepted: bool
    final_total: int
    note: str = ""


class OrderResult(Model):  # Dukaan -> Grahak
    confirms: list[OrderConfirm]
