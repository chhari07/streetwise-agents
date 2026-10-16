"""Streetwise Grahak — the buyer's agent, chatting in ASI:One.

Day-1 goal: it replies in ASI:One. It already understands a shopping request
and turns it into an item list with ASI1; asking shop agents comes on 24 Oct.
Based on Fetch.ai's "ASI:One-compatible uAgent" example (uagents 0.25.5,
AgentChatProtocol 0.3.0).
"""
import os

from dotenv import load_dotenv
from openai import OpenAI
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    TextContent,
    chat_protocol_spec,
)

load_dotenv()

api_key = os.getenv("ASI1_API_KEY")
seed = os.getenv("GRAHAK_SEED")
if not api_key or not seed:
    raise RuntimeError("Set ASI1_API_KEY and GRAHAK_SEED in the .env file")

llm = OpenAI(base_url="https://api.asi1.ai/v1", api_key=api_key)

SYSTEM = """You are Streetwise, a friendly shopping helper for local Indian shops
(kirana, dairy, chemist, bakery). Reply in the user's language: Hinglish if they
write Hinglish, else simple English. Keep replies short.
- If the user greets you, say: "Namaste! Main Streetwise hoon. Aapko kya chahiye?"
- If the user asks to buy or cook something, make a clear item list with
  quantities, then say: "Main paas ki dukaanon se daam pooch raha hoon…"
  (comparing real shops is coming soon, so do not invent shop prices).
- Never invent shops, prices or stock."""

agent = Agent(
    name="streetwise-grahak",
    seed=seed,
    port=8001,
    mailbox=True,
    publish_agent_details=True,
)

chat = Protocol(spec=chat_protocol_spec)


@chat.on_message(ChatMessage)
async def on_message(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))
    text = "".join(c.text for c in msg.content if isinstance(c, TextContent))
    ctx.logger.info(f"From {sender[:16]}…: {text}")

    reply = "Maaf kijiye, abhi jawab nahi de paa raha. Thodi der baad try karein."
    try:
        out = llm.chat.completions.create(
            model="asi1",
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
            max_tokens=600,
        )
        reply = str(out.choices[0].message.content)
    except Exception as err:
        ctx.logger.exception(f"ASI1 call failed: {err}")

    await ctx.send(sender, ChatMessage(content=[
        TextContent(type="text", text=reply),
        EndSessionContent(type="end-session"),
    ]))


@chat.on_message(ChatAcknowledgement)
async def on_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


agent.include(chat, publish_manifest=True)

if __name__ == "__main__":
    agent.run()
