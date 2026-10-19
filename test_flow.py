"""Local end-to-end test: a fake buyer chats with Grahak exactly like ASI:One would.

    python test_flow.py            # uses the ASI1 LLM for the item list (needs ASI1_API_KEY)
    python test_flow.py --no-llm   # keyword parser instead, no API key needed
    python test_flow.py --offline  # no OpenStreetMap: tests the shops.json fallback
    python test_flow.py --say "milk and bread near Lajpat Nagar Delhi" haan
    python test_flow.py --qr       # QR path: scans Bansal General Store (shutter down) -> nearby open shops
    python test_flow.py --map      # also serve the live map on http://localhost:8090/map while it runs
    python test_flow.py --testnet  # agent charge as a real FET transfer on the Fetch.ai testnet (needs internet)
    python test_flow.py --live     # buyer shares live GPS: the test opens the agent's location link and posts a position
    python test_flow.py --say "milk and bread https://maps.google.com/?q=28.4672,77.0710" haan   # Maps link in the chat

Steps: "Hello" -> "Paneer butter masala for 4 near Sector 29 Gurgaon, Rs 500 tak" -> "haan". Prints every reply, then exits."""
import asyncio
import os
import sys

os.environ.setdefault("STREETWISE_CLOCK_HOUR", "12")  # pretend it's noon so every shop is open
os.environ.setdefault("GRAHAK_SEED", "local test seed for streetwise only")
if not os.getenv("STREETWISE_STATE_DIR"):  # keep test memory away from the live agent's state/ folder
    import tempfile
    os.environ["STREETWISE_STATE_DIR"] = tempfile.mkdtemp(prefix="streetwise-test-")
if "--testnet" in sys.argv:
    os.environ["STREETWISE_PAYMENT"] = "testnet"
if "--live" in sys.argv:
    os.environ.setdefault("STREETWISE_LOCATE_PORT", "8191")  # not 8091, so it can run next to the live agent
if "--offline" in sys.argv:
    os.environ["STREETWISE_OFFLINE"] = "1"

from uagents import Agent, Context, Protocol  # noqa: E402
from uagents_core.contrib.protocols.chat import ChatAcknowledgement, ChatMessage, TextContent, chat_protocol_spec  # noqa: E402

from run_all import build  # noqa: E402

if "--say" in sys.argv:
    STEPS = sys.argv[sys.argv.index("--say") + 1:]
elif "--live" in sys.argv:  # "LIVE" = tap the location link the agent sent (Sector 29 Gurgaon GPS)
    STEPS = ["milk, bread and eggs", "LIVE", "haan"]
elif "--qr" in sys.argv:
    STEPS = ["QR bansal: milk, bread and eggs", "haan"]
else:
    STEPS = ["Hello", "Paneer butter masala for 4 near Sector 29 Gurgaon, Rs 500 tak", "haan"]

bureau, grahak, others = build(mailbox=False, use_llm="--no-llm" not in sys.argv)
buyer = Agent(name="test-buyer", seed="streetwise local test buyer")
chat = Protocol(spec=chat_protocol_spec)
state = {"step": 0}


LIVE_GPS = {"lat": 28.4675, "lng": 77.0712, "acc": 18}


async def say(ctx: Context, last_reply: str = ""):
    text = STEPS[state["step"]]
    if text == "LIVE":  # the buyer taps the link and allows GPS: post it like the page's script does
        import re
        import aiohttp
        url = re.search(r"https?://\S+/here/[\w-]+", last_reply).group(0)
        print(f"\n\033[1mBUYER  > (taps {url}, allows GPS {LIVE_GPS['lat']}, {LIVE_GPS['lng']})\033[0m", flush=True)
        async with aiohttp.ClientSession() as http:
            async with http.post(url, json=LIVE_GPS) as r:
                assert r.status == 200, f"location page said {r.status}"
        return
    print(f"\n\033[1mBUYER  > {text}\033[0m", flush=True)
    await ctx.send(grahak.address, ChatMessage(content=[TextContent(type="text", text=text)]))


@buyer.on_event("startup")
async def start(ctx: Context):
    await asyncio.sleep(1)
    await say(ctx)


@chat.on_message(ChatMessage)
async def on_reply(ctx: Context, sender: str, msg: ChatMessage):
    print(f"\033[36mGRAHAK > {msg.text()}\033[0m", flush=True)
    state["step"] += 1
    if state["step"] < len(STEPS):
        await say(ctx, msg.text())
    else:
        await asyncio.sleep(40 if os.getenv("STREETWISE_PAYMENT") == "testnet" else 25 if "--map" in sys.argv else 2)  # let the agent-charge payment finish
        print("\nTEST DONE: all steps answered.", flush=True)
        os._exit(0)


@chat.on_message(ChatAcknowledgement)
async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


buyer.include(chat)
bureau.add(buyer)


async def watchdog():
    await asyncio.sleep(150)
    print("\nTEST FAILED: no full answer within 150 s.", flush=True)
    os._exit(1)

asyncio.get_event_loop().create_task(watchdog())
if "--live" in sys.argv:  # the buyer's live-location page
    import locate  # noqa: E402
    asyncio.get_event_loop().create_task(locate.run_server())
if "--map" in sys.argv:  # also serve the owner screen + live map on http://localhost:8090 during the test
    import owner  # noqa: E402
    asyncio.get_event_loop().create_task(owner.run_panel())
bureau.run()
