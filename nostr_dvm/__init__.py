"""nostr-dvm: Python library for NIP-90 Nostr Data Vending Machines.

Turn any Python function into a paid service on the Nostr network,
priced in Bitcoin satoshis and discoverable by any AI agent.

Quick start (server)::

    from nostr_dvm import vending_machine, Kind

    @vending_machine(
        kind=Kind.TEXT_SUMMARIZE,
        name="Text Summarizer",
        about="Summarize any text to bullet points",
        price_sat=10,
    )
    async def summarize(job):
        return summarize_with_llm(job.first_input)

    import asyncio
    asyncio.run(summarize.run())

Quick start (client)::

    from nostr_dvm import hire, Kind

    result = await hire(Kind.TEXT_SUMMARIZE, "Long text...", max_sat=20)
    print(result)

NIP-90 spec: https://github.com/nostr-protocol/nostr/blob/master/90.md
"""

from .client import attest, discover, hire, reputation
from .exceptions import DVMError, JobError, NoProviderError, PaymentError, RelayError
from .models import (
    Attestation,
    DVMCapability,
    JobFeedback,
    JobInput,
    JobRequest,
    JobResult,
    Kind,
)
from .vending_machine import VendingMachine, vending_machine

__version__ = "0.1.0"

__all__ = [
    # Server
    "vending_machine",
    "VendingMachine",
    # Client
    "hire",
    "discover",
    "attest",
    "reputation",
    # Models
    "Kind",
    "JobRequest",
    "JobResult",
    "JobInput",
    "JobFeedback",
    "DVMCapability",
    "Attestation",
    # Exceptions
    "DVMError",
    "RelayError",
    "PaymentError",
    "JobError",
    "NoProviderError",
]
