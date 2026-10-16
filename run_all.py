"""Runs every Streetwise agent in one process.

    python run_all.py           # real run: Grahak gets a mailbox so ASI:One can reach it,
                                # + shop owner's YES / NO screen on http://localhost:8090, live map on /map
                                # + agent charge paid in FET on the Fetch.ai testnet (STREETWISE_PAYMENT=testnet)
    python test_flow.py         # local test without ASI:One (see that file)

Every demo shop in shops.json runs its own Merchant (Dukaan) agent with its own address.
Real nearby shops from OpenStreetMap haven't joined yet, so one shared demo Dukaan answers for them.

Agents in the same process talk to each other directly (no network needed).
With the mailbox, Grahak runs alone on port 8001 so the Agentverse inspector can find it
(one server with several agents answers "Multiple handlers found"); all the other agents share port 8002.
Seeds come from GRAHAK_SEED, so every address stays the same between runs."""
import asyncio
import os

from dotenv import load_dotenv

load_dotenv()
if __name__ == "__main__":  # real run (set before owner.py / payments.py are imported)
    os.environ.setdefault("STREETWISE_OWNER", "panel")  # the owner taps YES / NO
    os.environ.setdefault("STREETWISE_PAYMENT", "testnet")  # agent charge = real FET transfer on the Fetch.ai testnet

from uagents import Agent, Bureau  # noqa: E402

from directory import make_directory  # noqa: E402
from dukaan import make_dukaan  # noqa: E402
from grahak import make_grahak  # noqa: E402
from guardian import make_guardian  # noqa: E402
from shop_data import SHOPS  # noqa: E402


def build(mailbox: bool = True, use_llm: bool = True) -> tuple[Bureau, Agent, list[Agent]]:
    seed = os.getenv("GRAHAK_SEED")
    if not seed:
        raise RuntimeError("Set GRAHAK_SEED in the .env file")
    llm = None
    if use_llm:
        key = os.getenv("ASI1_API_KEY")
        if not key:
            raise RuntimeError("Set ASI1_API_KEY in the .env file (or run with --no-llm)")
        from openai import OpenAI
        llm = OpenAI(base_url="https://api.asi1.ai/v1", api_key=key)

    dukaan = make_dukaan(seed=f"{seed}/dukaan")  # shared: map shops that haven't joined Streetwise yet
    shop_agents = [make_dukaan(seed=f"{seed}/dukaan/{s['id']}", shop=s) for s in SHOPS]  # one agent per shop
    directory = make_directory(seed=f"{seed}/directory", dukaan_address=dukaan.address,
                               shop_agents={s["id"]: a.address for s, a in zip(SHOPS, shop_agents)})
    guardian = make_guardian(seed=f"{seed}/guardian")
    grahak = make_grahak(seed=seed, directory=directory.address, guardian=guardian.address, llm=llm,
                         port=8001, mailbox=mailbox, publish_agent_details=mailbox)
    others = [directory, guardian, dukaan, *shop_agents]
    # test mode: everyone in one Bureau on 8011 (can run next to the live agent); mailbox mode: Grahak gets its own server (see top of file)
    bureau = Bureau(agents=others, port=8002) if mailbox else Bureau(agents=[grahak, *others], port=8011)
    return bureau, grahak, others


async def run_split(bureau: Bureau, grahak: Agent):
    import owner
    await asyncio.gather(grahak.run_async(), bureau.run_async(), owner.run_panel())  # owner screen + /map


if __name__ == "__main__":
    import sys
    bureau, grahak, others = build(use_llm="--no-llm" not in sys.argv)
    print("\nStreetwise agents:")
    for a in [grahak, *others]:
        print(f"  {a.name:32} {a.address}")
    print("\nGrahak has the mailbox: open the Agent inspector link below -> Connect -> Mailbox.\n")
    loop = asyncio.get_event_loop()  # the loop the agents were created on
    try:
        loop.run_until_complete(run_split(bureau, grahak))
    except KeyboardInterrupt:
        pass
