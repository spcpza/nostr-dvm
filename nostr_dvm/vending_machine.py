"""VendingMachine: turn any async Python function into a paid Nostr DVM.

NIP-90 Data Vending Machines are services that:
  1. Announce their capabilities on Nostr (kind 31990)
  2. Listen for job requests (kind 5000-5999)
  3. Optionally request Lightning payment (kind 7000)
  4. Deliver results (kind 6000-6999)

Usage::

    import asyncio
    from nostr_dvm import vending_machine

    @vending_machine(
        kind=5100,
        name="Text Summarizer",
        about="Summarize any text to 3 bullet points",
        price_sat=10,
        relays=["wss://relay.damus.io", "wss://nos.lol"],
    )
    async def summarize(job):
        text = job.first_input
        # ... call your LLM or logic here
        return f"• Key point 1\\n• Key point 2\\n• Key point 3"

    asyncio.run(summarize.run())

The decorated function receives a :class:`~nostr_dvm.models.JobRequest`
and should return a string result.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from .crypto import load_privkey, pubkey_from_privkey, sign_event
from .exceptions import DVMError, PaymentError
from .models import DVMCapability, JobFeedback, JobRequest, JobResult, Kind
from .relay import RelayPool

log = logging.getLogger(__name__)

_DEFAULT_RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.nostr.band",
]

JobHandler = Callable[["JobRequest"], Coroutine[Any, Any, str]]


# ---------------------------------------------------------------------------
# VendingMachine class
# ---------------------------------------------------------------------------


class VendingMachine:
    """A Nostr Data Vending Machine wrapping a Python async function.

    Do not instantiate directly — use the :func:`vending_machine` decorator.
    """

    def __init__(
        self,
        fn: JobHandler,
        *,
        kind: int,
        name: str,
        about: str,
        price_sat: int = 0,
        relays: list[str] | None = None,
        privkey_hex: str | None = None,
        require_payment_before_job: bool = True,
    ) -> None:
        self._fn = fn
        self.kind = kind
        self.name = name
        self.about = about
        self.price_sat = price_sat
        self.relays = relays or _DEFAULT_RELAYS
        self._privkey_hex = privkey_hex
        self.require_payment_before_job = require_payment_before_job
        self._pool: RelayPool | None = None

    def _privkey(self) -> str:
        if self._privkey_hex:
            return self._privkey_hex
        return load_privkey()

    def _pubkey(self) -> str:
        return pubkey_from_privkey(self._privkey())

    # ------------------------------------------------------------------
    # Publishing helpers
    # ------------------------------------------------------------------

    async def _publish(self, kind: int, content: str, tags: list[list[str]]) -> str:
        """Sign and publish a Nostr event. Returns event ID."""
        assert self._pool is not None
        event = sign_event(kind, content, tags, self._privkey())
        await self._pool.publish(event)
        return event["id"]

    async def _announce_capability(self) -> None:
        """Publish NIP-89 capability announcement (kind 31990)."""
        uid = f"dvm-{self.kind}-{self._pubkey()[:8]}"
        cap = DVMCapability(
            name=self.name,
            about=self.about,
            job_kind=self.kind,
            price_sat=self.price_sat,
        )
        tags = cap.to_tags(uid)
        event_id = await self._publish(31990, cap.to_content(), tags)
        log.info("Announced capability: kind=%d name=%r (event %s)", self.kind, self.name, event_id[:8])

    async def _request_payment(self, job: JobRequest) -> str | None:
        """Publish kind 7000 requesting payment. Returns bolt11 or None."""
        if self.price_sat <= 0:
            return None

        from . import payment as pay
        try:
            invoice = pay.create_invoice(self.price_sat, description=f"DVM job: {self.name}")
        except PaymentError as exc:
            log.warning("Could not create invoice for job %s: %s", job.event_id[:8], exc)
            return None

        bolt11 = invoice["bolt11"]
        feedback = JobFeedback(
            request=job,
            status="payment-required",
            extra_info=f"Pay {self.price_sat} sats to process",
            amount_msat=self.price_sat * 1000,
            bolt11=bolt11,
        )
        tags = feedback.to_tags()
        await self._publish(7000, "", tags)
        log.info("Requested payment for job %s: %d sats", job.event_id[:8], self.price_sat)
        return bolt11

    async def _wait_for_payment(self, invoice: dict) -> bool:
        """Poll for Lightning payment confirmation (non-blocking via asyncio)."""
        from . import payment as pay
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: pay.wait_for_payment(
                invoice["payment_hash"],
                invoice["amount_sat"],
            ),
        )

    async def _publish_result(self, job: JobRequest, result_content: str) -> None:
        """Publish kind 6XXX job result."""
        result_kind = Kind.result_kind(job.kind)
        result = JobResult(request=job, content=result_content, status="success")
        tags = [
            ["request", ""],
            ["e", job.event_id, "", "reply"],
            ["p", job.pubkey],
            ["status", "success"],
        ]
        event_id = await self._publish(result_kind, result_content, tags)
        log.info(
            "Published result for job %s → event %s",
            job.event_id[:8], event_id[:8]
        )

    async def _publish_error(self, job: JobRequest, error_msg: str) -> None:
        """Publish kind 7000 with error status."""
        tags = [
            ["e", job.event_id, "", "reply"],
            ["p", job.pubkey],
            ["status", "error", error_msg[:200]],
        ]
        await self._publish(7000, error_msg[:200], tags)

    # ------------------------------------------------------------------
    # Job processing
    # ------------------------------------------------------------------

    async def _process_job(self, job: JobRequest) -> None:
        """Handle a single job request end-to-end."""
        log.info(
            "Job received: kind=%d id=%s from=%s",
            job.kind, job.event_id[:8], job.pubkey[:8]
        )

        # Payment gate (optional)
        if self.price_sat > 0 and self.require_payment_before_job:
            bolt11 = await self._request_payment(job)
            if bolt11:
                from . import payment as pay
                # Create invoice data for polling
                invoice_data = {"payment_hash": "", "amount_sat": self.price_sat}
                paid = await self._wait_for_payment(invoice_data)
                if not paid:
                    log.warning("Payment timeout for job %s — skipping", job.event_id[:8])
                    await self._publish_error(job, "Payment not received within timeout.")
                    return

        # Execute handler
        try:
            result = await self._fn(job)
        except Exception as exc:
            log.exception("Handler raised exception for job %s", job.event_id[:8])
            await self._publish_error(job, str(exc)[:200])
            return

        await self._publish_result(job, result)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self, *, max_jobs: int | None = None) -> None:
        """Start the DVM server loop.

        Connects to relays, announces capability, and processes incoming
        job requests indefinitely (or until max_jobs is reached).

        Parameters
        ----------
        max_jobs: Stop after processing this many jobs (for testing).
        """
        pubkey = self._pubkey()
        log.info("Starting DVM: kind=%d pubkey=%s...", self.kind, pubkey[:16])
        log.info("Relays: %s", self.relays)
        log.info("Price: %d sats", self.price_sat)

        async with RelayPool(self.relays) as pool:
            self._pool = pool

            # Announce capability
            await self._announce_capability()

            # Listen for job requests
            job_filter = {"kinds": [self.kind]}
            count = 0
            async for event in pool.subscribe(job_filter, timeout=86400.0):
                if event.get("pubkey") == pubkey:
                    continue  # ignore our own events

                try:
                    job = JobRequest.from_event(event)
                except Exception as exc:
                    log.warning("Could not parse job request: %s", exc)
                    continue

                asyncio.create_task(self._process_job(job))
                count += 1
                if max_jobs is not None and count >= max_jobs:
                    break

        self._pool = None
        log.info("DVM stopped after %d jobs", count)

    # ------------------------------------------------------------------
    # Make it callable (pass-through to the underlying function for testing)
    # ------------------------------------------------------------------

    async def __call__(self, job: "JobRequest") -> str:
        return await self._fn(job)


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def vending_machine(
    *,
    kind: int,
    name: str,
    about: str = "",
    price_sat: int = 0,
    relays: list[str] | None = None,
    privkey_hex: str | None = None,
    require_payment_before_job: bool = True,
) -> Callable[[JobHandler], VendingMachine]:
    """Decorator that wraps an async function as a NIP-90 Data Vending Machine.

    Parameters
    ----------
    kind:    NIP-90 job kind (5000-5999). Use :class:`~nostr_dvm.models.Kind`
             constants or define your own.
    name:    Human-readable DVM name (shown in capability announcement).
    about:   Description of what this DVM does.
    price_sat: Price in satoshis per job (0 = free).
    relays:  Nostr relay URLs to connect to.
    privkey_hex: Hex private key (defaults to NOSTR_NSEC / NOSTR_HEX_KEY env var).
    require_payment_before_job: If True, wait for Lightning payment before
                                processing. If False, process first and
                                request payment after (not recommended).

    Example::

        @vending_machine(kind=5100, name="Summarizer", price_sat=10)
        async def summarize(job: JobRequest) -> str:
            return summarize_text(job.first_input)

        asyncio.run(summarize.run())
    """
    def decorator(fn: JobHandler) -> VendingMachine:
        return VendingMachine(
            fn,
            kind=kind,
            name=name,
            about=about or fn.__doc__ or "",
            price_sat=price_sat,
            relays=relays,
            privkey_hex=privkey_hex,
            require_payment_before_job=require_payment_before_job,
        )
    return decorator
