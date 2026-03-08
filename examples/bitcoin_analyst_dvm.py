"""Example: Bitcoin analysis DVM using bitcoin-mcp data.

A more complete example: a DVM that fetches live mempool/price data
and provides Bitcoin market summaries on demand.

Combines nostr-dvm + bitcoin-mcp's BitcoinAPIClient.
Kind: 5600 (BTC_ANALYSIS)

Setup:
  pip install bitcoin-mcp nostr-dvm
  export NOSTR_NSEC=nsec1...
  export NWC_CONNECTION_STRING=...

Run:
  python3 examples/bitcoin_analyst_dvm.py
"""

import asyncio
import logging

from nostr_dvm import Kind, vending_machine
from nostr_dvm.models import JobRequest

logging.basicConfig(level=logging.INFO)


@vending_machine(
    kind=Kind.BTC_ANALYSIS,
    name="Bitcoin Market Analyst",
    about=(
        "Real-time Bitcoin market summary: price, mempool, fees, latest block. "
        "Data from mempool.space and blockchain.info. 5 sats per report."
    ),
    price_sat=5,
    relays=["wss://relay.damus.io", "wss://nos.lol"],
)
async def btc_analyst(job: JobRequest) -> str:
    """Fetch live Bitcoin data and return a market summary."""
    try:
        from bitcoin_mcp import BitcoinAPIClient
        async with BitcoinAPIClient() as api:
            price, mempool, block = await asyncio.gather(
                api.get_price(),
                api.get_mempool_info(),
                api.get_latest_block(),
            )

        fees = mempool["fees"]
        return (
            f"**Bitcoin Market Summary**\n\n"
            f"💰 Price: ${price.get('usd', 0):,.0f} USD\n"
            f"⛏️  Block: #{block['height']:,} ({block['tx_count']:,} txs)\n"
            f"📦 Mempool: {mempool['count']:,} txs | {mempool['vsize'] / 1e6:.1f} MB\n"
            f"⚡ Fees (sat/vB): fast={fees['fastest_sat_vb']} | "
            f"1h={fees['hour_sat_vb']} | min={fees['minimum_sat_vb']}\n"
        )
    except ImportError:
        return (
            "bitcoin-mcp not installed. "
            "Run: pip install bitcoin-mcp\n"
            "Then retry your job request."
        )
    except Exception as exc:
        return f"Error fetching Bitcoin data: {exc}"


if __name__ == "__main__":
    print("Starting Bitcoin Analyst DVM...")
    asyncio.run(btc_analyst.run())
