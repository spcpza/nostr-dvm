"""Example: Bitcoin-native text summarizer as a Nostr Data Vending Machine.

This DVM:
- Listens for kind 5100 (TEXT_SUMMARIZE) job requests on Nostr
- Charges 10 sats per job via Lightning (NWC)
- Returns a 3-bullet-point summary using a simple heuristic
  (replace with your LLM call for production)

Setup:
  export NOSTR_NSEC=nsec1...         # your DVM's Nostr identity
  export NWC_CONNECTION_STRING=...   # nostr+walletconnect://... URI

Run:
  python3 examples/summarizer_dvm.py
"""

import asyncio
import logging

from nostr_dvm import Kind, vending_machine
from nostr_dvm.models import JobRequest

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@vending_machine(
    kind=Kind.TEXT_SUMMARIZE,
    name="Bitcoin Summarizer",
    about=(
        "Summarizes any text to 3 bullet points. "
        "Priced in sats. Powered by Lightning."
    ),
    price_sat=10,
    relays=["wss://relay.damus.io", "wss://nos.lol", "wss://relay.nostr.band"],
)
async def summarize(job: JobRequest) -> str:
    """Summarize text to 3 bullet points."""
    text = job.first_input.strip()
    if not text:
        return "• (no input provided)"

    # -- Replace this with an actual LLM call in production --
    sentences = [s.strip() for s in text.replace("\n", " ").split(".") if len(s.strip()) > 20]
    if len(sentences) >= 3:
        bullets = [f"• {s[:100]}." for s in sentences[:3]]
    elif sentences:
        bullets = [f"• {s[:100]}." for s in sentences]
        bullets += ["• (short input — expand for better summary)"]
    else:
        bullets = [f"• {text[:200]}"]
    # --------------------------------------------------------

    return "\n".join(bullets)


if __name__ == "__main__":
    print("Starting Bitcoin Summarizer DVM...")
    print("Kind: 5100 (TEXT_SUMMARIZE)")
    print("Price: 10 sats per job")
    print()
    asyncio.run(summarize.run())
