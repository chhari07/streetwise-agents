"""Streetwise agent charge, paid shop agent -> Streetwise with Fetch.ai's AgentPaymentProtocol.

STREETWISE_PAYMENT=testnet -> a real FET transfer on the Fetch.ai Dorado testnet (testnet FET has no money
                              value). Each agent's wallet comes from its seed; an empty shop wallet is topped
                              up from the testnet faucet. The Shopper Agent checks the transaction on-chain.
STREETWISE_PAYMENT=demo    -> no chain: a DEMO-TX id (used by the offline tests).

AGENT_CHARGE_FET stands in for the planned Rs 2 per order (no FX conversion is implied)."""
import asyncio
import os
from decimal import Decimal
from functools import cache

MODE = os.getenv("STREETWISE_PAYMENT", "demo")
AGENT_CHARGE_INR = 2
AGENT_CHARGE_FET = Decimal(os.getenv("STREETWISE_CHARGE_FET", "0.002"))
DENOM = "atestfet"  # 1 TESTFET = 10**18 atestfet
CHAIN = "dorado-1 (Fetch.ai testnet)"


@cache
def testnet():
    """Our own testnet client. Never the agent's ctx.ledger: that one is the Fetch.ai MAINNET (fetchhub-4)."""
    from cosmpy.aerial.client import LedgerClient, NetworkConfig
    led = LedgerClient(NetworkConfig.fetchai_stable_testnet())
    assert led.network_config.chain_id == "dorado-1", "payments must only run on the Fetch.ai testnet"
    return led


def afet(amount: str | Decimal) -> int:
    return int(Decimal(amount) * 10**18)


async def ensure_funds(address: str, need_afet: int) -> None:
    """Tops up a testnet wallet from the faucet when it can't cover the charge plus gas."""
    if await asyncio.to_thread(testnet().query_bank_balance, address, DENOM) >= need_afet + 10**16:
        return
    from cosmpy.aerial.faucet import FaucetApi
    from cosmpy.aerial.client import NetworkConfig
    await asyncio.to_thread(FaucetApi(NetworkConfig.fetchai_stable_testnet()).get_wealth, address)


async def send(wallet, recipient: str, amount: str) -> str:
    """Sends `amount` TESTFET to recipient and waits for the block. Returns the transaction hash."""
    need = afet(amount)
    await ensure_funds(str(wallet.address()), need)
    tx = await asyncio.to_thread(testnet().send_tokens, recipient, need, DENOM, wallet)
    await asyncio.to_thread(tx.wait_to_complete)
    return tx.tx_hash


async def verify(tx_hash: str, recipient: str, amount: str) -> bool:
    """True when the transaction is on-chain, succeeded, and moved at least `amount` to recipient."""
    resp = await asyncio.to_thread(testnet().query_tx, tx_hash)
    if not resp.is_successful():
        return False
    t = resp.events.get("transfer") or {}  # cosmpy flattens the tx events: {"recipient", "sender", "amount": "<n>atestfet"}
    got = str(t.get("amount", "0"))
    return t.get("recipient") == recipient and got.endswith(DENOM) and int(got.removesuffix(DENOM)) >= afet(amount)
