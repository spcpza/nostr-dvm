"""Example: hire a Nostr DVM to summarize text.

Discovers available summarizers on the Nostr network,
picks the cheapest one, pays via Lightning, and prints the result.

Setup:
  export NOSTR_NSEC=nsec1...         # your Nostr identity (for signing job requests)
  export NWC_CONNECTION_STRING=...   # nostr+walletconnect://... URI (for paying)

Run:
  python3 examples/hire_summarizer.py
"""

import asyncio

from nostr_dvm import Kind, discover, hire, reputation


TEXT_TO_SUMMARIZE = """
Bitcoin is a decentralized digital currency, without a central bank or single
administrator, that can be sent from user to user on the peer-to-peer bitcoin
network without the need for intermediaries. Transactions are verified by network
nodes through cryptography and recorded in a public distributed ledger called a
blockchain. The cryptocurrency was invented in 2008 by an unknown person or group
of people using the name Satoshi Nakamoto. The currency began use in 2009 when
its implementation was released as open-source software.

Bitcoin has been described as an economic bubble by at least eight Nobel Memorial
Prize in Economic Sciences recipients. The Bitcoin network came into existence on
3 January 2009 with the mining of the genesis block ("block 0"), which had a reward
of 50 bitcoins. The proof of work uses the SHA-256 hashing algorithm.
"""


async def main():
    print("=== nostr-dvm client demo ===\n")

    # Step 1: Discover available summarizers
    print("Discovering TEXT_SUMMARIZE DVMs on Nostr...")
    providers = await discover(
        kind=Kind.TEXT_SUMMARIZE,
        max_price_sat=100,
        timeout=10.0,
    )

    if providers:
        print(f"Found {len(providers)} provider(s):")
        for p in providers:
            print(f"  • {p['name']} — {p['price_sat']} sats — {p['pubkey'][:16]}...")
        print()

        # Step 2: Check reputation of the cheapest provider
        cheapest = providers[0]
        print(f"Checking reputation of {cheapest['name']}...")
        rep = await reputation(cheapest["pubkey"], timeout=5.0)
        if rep["total_attestations"] > 0:
            print(f"  {rep['total_attestations']} attestations, avg quality: {rep['avg_quality']}/5")
            print(f"  Total sats earned: {rep['total_sats_paid']}")
        else:
            print("  No reputation yet (new DVM)")
        print()
    else:
        print("No DVMs found on relays — running without specific provider.\n")

    # Step 3: Hire the DVM
    print("Submitting job request...")
    try:
        result = await hire(
            kind=Kind.TEXT_SUMMARIZE,
            inputs=TEXT_TO_SUMMARIZE,
            max_sat=50,
            timeout=60.0,
        )
        print("\n=== Summary ===")
        print(result)
        print()
    except Exception as exc:
        print(f"Error: {exc}")
        return

    print("Done. Bitcoin-native AI service, settled on Lightning. ⚡")


if __name__ == "__main__":
    asyncio.run(main())
