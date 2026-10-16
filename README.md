![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

# Streetwise: AI agents for every local shop

**A local shop connector, not a warehouse.** Buyers ask once in ASI:One. A network of Fetch.ai agents
finds real shops nearby, collects live quotes from each shop's own agent, blocks hidden fees, and places
the order. The shop owner confirms with one tap. If the shop you scanned is closed, its QR still helps:
it sends you to the nearest open shop.

Built by **Team SOLOX** (Aman Kumar Chhari, solo developer) for **Fetch-A-Thon 3.0**.

**Try it in ASI:One:** `agent1qfv4qg39ul92cqj29degg2zs36gkr04ga59kwd85eqr0xlmedsr07zveljw`

## How it works

```
Buyer: scan a shop's QR  ─┐
       chat from anywhere ─┤
                           ▼
              ① Shopper Agent reads the item list (ASI1 LLM)
                           ▼
        Scanned shop open and has every item? ── yes ──► ③ that shop's Merchant Agent
                           │ no / closed / chat search
                           ▼
        ② Store Locator: nearby shops, 500 m → 1 km → 2 km ──► ③ each shop's own Merchant Agent
                                                                   ▼
                                                 Shop owner taps YES / NO
                                                                   ▼
                              ④ Trust Agent: blocks hidden fees, closed shops, wrong totals
                                                                   ▼
                              Buyer picks an option → order confirmed (Verified Receipt)
                                                                   ▼
                     Shop pays Streetwise ₹2 agent charge (Fetch.ai Payment Protocol)
```

## Agents

| Agent | Code name | File | What it does |
|---|---|---|---|
| ① Shopper Agent | `streetwise-grahak` | `grahak.py` | Chats in ASI:One (Agent Chat Protocol), makes the item list with the ASI1 LLM, asks shops, shows up to 3 checked options, places the order, collects the agent charge |
| ② Store Locator Agent | `streetwise-directory` | `directory.py` | Finds the buyer's area and real shops (OpenStreetMap), widening 500 m → 1 km → 2 km until it has 3; looks up a scanned shop by its QR |
| ③ Merchant Agent | `streetwise-dukaan-<shop>` | `dukaan.py` | **One per shop.** Quotes from the shop's own price list, asks the owner YES / NO for each order, pays the agent charge |
| ④ Trust Agent | `streetwise-guardian` | `guardian.py` | Blocks hidden fees, raised fees and closed shops; builds the cheapest / nearest / one-shop options |

Typed messages between agents are in `models.py`:
`ShopSearch → ShopList`, `QuoteRequest → QuoteList`, `CheckQuotes → Verdict`, `OrderRequest → OrderResult`,
plus Fetch.ai's `AgentPaymentProtocol` (`RequestPayment → CommitPayment → CompletePayment`).

## Fetch.ai stack

- **uAgents** (Python, `uagents==0.25.5`): 4 agent roles, one Merchant Agent per shop
- **Agentverse**: the Shopper Agent is registered with a mailbox
- **Agent Chat Protocol**: reachable and discoverable from **ASI:One**
- **ASI1 LLM**: turns free text or Hinglish ("Paneer butter masala for 4, Rs 500 tak") into an item list.
  Prices, totals and fee checks are plain code, so the LLM can't make them up.
- **AgentPaymentProtocol**: Streetwise (seller role) requests ₹2 per confirmed order from each shop's agent
  (buyer role). The buyer never pays anything extra.

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # add your ASI1 key and a long random seed

python test_flow.py --no-llm --offline   # full flow, no API key or internet needed (demo shops)
python test_flow.py --no-llm --qr --offline   # QR path: scanned shop is closed -> nearby open shops
python test_flow.py --no-llm             # live OpenStreetMap shops
python test_flow.py                      # with the ASI1 LLM

python run_all.py                 # real run: Shopper Agent reachable from ASI:One
                                  # + the shop owner's YES / NO screen on http://localhost:8090
```

For `run_all.py`: open the Agent inspector link from the log → Connect → Mailbox. Then chat with the agent in ASI:One.

## Try in ASI:One

- `Paneer butter masala for 4 near Sector 29 Gurgaon, Rs 500 tak`, then `haan`
- `milk, bread and eggs near Lajpat Nagar Delhi`, then `1`
- `QR bansal: milk, bread and eggs`: what a QR sticker on Bansal General Store sends. The shop is closed,
  so you get the nearest open shops.
- With `run_all.py`, open http://localhost:8090 and tap **YES** to confirm the order as the shop owner

## What is real and what is demo

- **Real:** the agents and messages, the Agentverse / ASI:One connection, the OpenStreetMap shop search
  (names, locations, opening hours), the Trust Agent checks, and the payment protocol messages.
- **Demo:** prices. Shops haven't joined Streetwise yet, so real map shops get steady demo prices
  (`shop_data.py`), and one shared demo Merchant Agent answers for them. The shops in `shops.json` are
  made-up demo shops, each with its own Merchant Agent. One of them hides a ₹30 packing fee so the Trust
  Agent has something to block, and Bansal General Store has its shutter down to show the QR path.
- **Orders and payments are demo:** no shop is messaged and no money moves (`STREETWISE_DEMO=1`).

## Files

| File | |
|---|---|
| `run_all.py` | starts every agent (one Merchant Agent per shop) and the owner screen |
| `test_flow.py` | local end-to-end test: a fake buyer chats exactly like ASI:One |
| `owner.py` | shop owner's YES / NO screen (`STREETWISE_OWNER=panel`), or an auto rule for tests |
| `nearby.py` | OpenStreetMap search (Nominatim, free, no key) with the 500 m → 1 km → 2 km ripple |
| `shop_data.py` | shop registry, demo prices, opening hours |
| `catalogue.json`, `shops.json` | items (~120) and demo shops |
| `state.py` | small JSON files so a waiting deal survives a restart (`state/`, git-ignored) |
| `agent.py` | the first single-agent version (chat only), kept for a quick check |
