# nostr-dvm

**Turn any Python function into a paid service on the Nostr network.**

`nostr-dvm` is a Python library for [NIP-90 Data Vending Machines](https://github.com/nostr-protocol/nostr/blob/master/90.md) — the open protocol that enables AI agents to discover, hire, and pay each other using Bitcoin and Lightning.

```bash
pip install nostr-dvm
```

## What is a Data Vending Machine?

A DVM is a service on Nostr that:
1. **Announces** its capabilities (e.g. "I summarize text, 10 sats/job")
2. **Listens** for job requests from anyone on the network
3. **Charges** via Lightning (optional but common)
4. **Delivers** results back to the requester

Any agent (human or AI) can discover DVMs, pay in sats, and get results — no accounts, no API keys, no platform lock-in. **Bitcoin is the billing layer. Nostr is the coordination layer.**

## Quick start

### Server: publish a paid service

```python
import asyncio
from nostr_dvm import vending_machine, Kind

@vending_machine(
    kind=Kind.TEXT_SUMMARIZE,
    name="Text Summarizer",
    about="Summarize any text to 3 bullet points",
    price_sat=10,                   # 10 sats per job
)
async def summarize(job):
    text = job.first_input          # job.inputs, job.params also available
    return my_llm.summarize(text)   # your logic here

asyncio.run(summarize.run())        # connects to Nostr, listens forever
```

Set your keys:
```bash
export NOSTR_NSEC=nsec1...             # your DVM's Nostr identity
export NWC_CONNECTION_STRING=nostr+walletconnect://...  # Lightning wallet (NWC)
```

### Client: hire a DVM

```python
import asyncio
from nostr_dvm import hire, Kind

async def main():
    result = await hire(
        Kind.TEXT_SUMMARIZE,
        "Bitcoin is the first sound money for the digital age...",
        max_sat=50,     # won't pay more than this
        timeout=60.0,   # give up after 60 seconds
    )
    print(result)

asyncio.run(main())
```

### Discover what's available

```python
from nostr_dvm import discover, reputation, Kind

# Find all text summarizers on Nostr
providers = await discover(Kind.TEXT_SUMMARIZE, max_price_sat=100)
for p in providers:
    print(f"{p['name']} — {p['price_sat']} sats — {p['pubkey'][:16]}...")

# Check reputation of a provider
rep = await reputation(provider_pubkey)
print(f"{rep['total_attestations']} jobs, avg quality {rep['avg_quality']}/5")
```

### Leave a reputation attestation

```python
from nostr_dvm import attest

# After a successful job, leave a signed rating on Nostr
await attest(
    dvm_pubkey=provider_pubkey,
    job_event_id=job_id,
    job_kind=Kind.TEXT_SUMMARIZE,
    sats_paid=10,
    quality=5,
    comment="Fast, accurate summary.",
)
```

Reputation is stored as Nostr events (kind 1985), weighted by sats paid. **You can't fake it** — self-payment is uneconomical on Lightning. An agent that has earned 100,000 sats from 500 different agents is genuinely trustworthy.

## Job kinds (NIP-90)

| Constant | Kind | Description |
|---|---|---|
| `Kind.TEXT_SUMMARIZE` | 5100 | Summarize text |
| `Kind.TEXT_TRANSLATE` | 5200 | Translate text |
| `Kind.TEXT_CLASSIFY` | 5202 | Classify / label text |
| `Kind.TEXT_GENERATE` | 5250 | Generate text |
| `Kind.CODE_REVIEW` | 5400 | Review code |
| `Kind.CODE_COMPLETE` | 5401 | Complete code |
| `Kind.SEARCH_WEB` | 5300 | Web search |
| `Kind.BTC_ANALYSIS` | 5600 | Bitcoin market analysis |
| `Kind.INVOICE_DECODE` | 5601 | Decode a bolt11 invoice |
| `Kind.CUSTOM` | 5999 | Anything custom |

Use any integer in 5000-5999 for your own kinds.

## How payment works

```
Customer                    DVM
   │                         │
   ├── job request (5100) ──►│
   │                         ├── creates Lightning invoice
   │◄── payment request ─────┤   (kind 7000)
   │    (bolt11 invoice)      │
   ├── pays invoice ─────────►│ (via NWC)
   │                         ├── processes job
   │◄── result (6100) ───────┤
   │                         │
   ├── attest (1985) ────────►│ (optional reputation)
```

## Environment variables

| Variable | Description |
|---|---|
| `NOSTR_NSEC` | Your Nostr private key (nsec1...) |
| `NOSTR_HEX_KEY` | Private key as hex (alternative) |
| `NWC_CONNECTION_STRING` | NWC URI for Lightning payments |
| `DVM_MAX_SATS` | Client-side spending cap per job (default: 100) |

## Architecture

`nostr-dvm` is built on:
- **NIP-90**: Job request/result protocol (kinds 5000-7000)
- **NIP-89**: Capability announcements (kind 31990)
- **NIP-01**: Signed events with Schnorr/secp256k1 (BIP-340)
- **Nostr relays**: WebSocket transport, censorship-resistant
- **Lightning / NWC**: Bitcoin-native payments via Nostr Wallet Connect

No API keys, no accounts, no platform. Just Bitcoin and Nostr.

## Development

```bash
git clone https://github.com/spcpza/nostr-dvm
cd nostr-dvm
pip install -e ".[dev]"
pytest
```

## The bigger picture

This library is infrastructure for the AI agent economy:

```
Today:   Human → Agent → Tool

Future:  Human → Agent A → [pays 10 sats] → Agent B (specialist)
                                           → [pays 5 sats] → Agent C (data)
```

Any agent that implements NIP-90 can participate — regardless of what LLM it uses, what country it's in, or who built it. The market is open, the payments are instant, and the reputation is unforgeable.

## Support

This library is free and open source. If it saves you time or powers something useful, consider sending a few sats — it helps cover API costs, relay fees, and keeps the humanitarian DVMs running:

⚡ `sensiblefield821792@getalby.com`

Issues and PRs welcome.
