"""hire() — discover and call NIP-90 Data Vending Machines.

The client side of the DVM economy:

1. Query Nostr for available DVMs (kind 31990 capability announcements)
2. Choose a provider (cheapest, fastest, highest reputation)
3. Publish a job request (kind 5000-5999)
4. Pay any Lightning invoice (kind 7000) if required
5. Wait for and return the result (kind 6000-6999)

Usage::

    from nostr_dvm import hire, Kind

    # Simple: hire the cheapest available summarizer
    result = await hire(
        kind=Kind.TEXT_SUMMARIZE,
        inputs=["Long text here..."],
        max_sat=50,
        timeout=30.0,
    )
    print(result)

    # Advanced: discover and inspect providers first
    providers = await discover(kind=Kind.TEXT_SUMMARIZE)
    for p in providers:
        print(f"{p['name']} — {p['price_sat']} sats — {p['pubkey'][:16]}...")
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from .crypto import load_privkey, pubkey_from_privkey, sign_event
from .exceptions import DVMError, NoProviderError, PaymentError
from .models import JobInput, JobRequest, Kind
from .relay import RelayPool

log = logging.getLogger(__name__)

_DEFAULT_RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.nostr.band",
]

_DISCOVER_TIMEOUT = 10.0
_JOB_TIMEOUT = 60.0


# ---------------------------------------------------------------------------
# Provider discovery
# ---------------------------------------------------------------------------


async def discover(
    kind: int,
    *,
    relays: list[str] | None = None,
    timeout: float = _DISCOVER_TIMEOUT,
    max_price_sat: int | None = None,
) -> list[dict[str, Any]]:
    """Discover available Data Vending Machines for a given job kind.

    Queries Nostr for NIP-89/90 capability announcements (kind 31990)
    matching the requested job kind.

    Parameters
    ----------
    kind:         NIP-90 job kind to look for.
    relays:       Relay URLs to query (defaults to well-known relays).
    timeout:      How long to wait for announcements.
    max_price_sat: Filter out DVMs that charge more than this.

    Returns:
        List of provider dicts with keys: pubkey, name, about, price_sat, raw.
    """
    _relays = relays or _DEFAULT_RELAYS
    providers = []

    async with RelayPool(_relays) as pool:
        filt = {
            "kinds": [31990],
            "limit": 50,
        }
        async for event in pool.subscribe(filt, timeout=timeout):
            try:
                content = json.loads(event.get("content", "{}"))
            except json.JSONDecodeError:
                continue

            # Check if this DVM handles our kind
            nip90 = content.get("nip90Params", {})
            if str(kind) not in nip90:
                # Also check tags
                tag_kinds = [t[1] for t in event.get("tags", []) if t[0] == "k"]
                if str(kind) not in tag_kinds:
                    continue

            params = nip90.get(str(kind), {})
            price_sat = params.get("priceInSats", 0)

            if max_price_sat is not None and price_sat > max_price_sat:
                continue

            providers.append({
                "pubkey": event.get("pubkey", ""),
                "name": content.get("name", "Unknown DVM"),
                "about": content.get("about", ""),
                "price_sat": price_sat,
                "requires_payment": params.get("requiresPayment", False),
                "event_id": event.get("id", ""),
                "raw": event,
            })

    # Sort by price ascending
    providers.sort(key=lambda p: p["price_sat"])
    return providers


# ---------------------------------------------------------------------------
# Job publishing
# ---------------------------------------------------------------------------


async def _publish_job(
    pool: RelayPool,
    privkey_hex: str,
    kind: int,
    inputs: list[str | JobInput],
    *,
    bid_sat: int = 0,
    relays: list[str] | None = None,
    params: dict[str, str] | None = None,
) -> str:
    """Publish a job request. Returns event_id."""
    tags: list[list[str]] = []

    for inp in inputs:
        if isinstance(inp, str):
            tags.append(["i", inp, "text"])
        else:
            tags.append(inp.to_tag())

    tags.append(["output", "text/plain"])

    if bid_sat > 0:
        tags.append(["bid", str(bid_sat * 1000)])  # msat

    if relays:
        tags.append(["relays"] + relays)

    for k, v in (params or {}).items():
        tags.append(["param", k, v])

    event = sign_event(kind, "", tags, privkey_hex)
    await pool.publish(event)
    log.info("Published job request: kind=%d event=%s", kind, event["id"][:8])
    return event["id"]


# ---------------------------------------------------------------------------
# Result waiting
# ---------------------------------------------------------------------------


async def _wait_for_result(
    pool: RelayPool,
    job_event_id: str,
    result_kind: int,
    privkey_hex: str,
    *,
    timeout: float,
    max_sat: int,
) -> str:
    """Wait for a job result, handling payment requests if needed."""
    filt = {
        "kinds": [result_kind, 7000],
        "#e": [job_event_id],
    }

    async for event in pool.subscribe(filt, timeout=timeout):
        evt_kind = event.get("kind")

        if evt_kind == 7000:
            # Job feedback — might be a payment request
            status = ""
            bolt11 = ""
            amount_msat = 0
            for tag in event.get("tags", []):
                if tag[0] == "status" and len(tag) > 1:
                    status = tag[1]
                elif tag[0] == "amount" and len(tag) >= 3:
                    try:
                        amount_msat = int(tag[1])
                    except ValueError:
                        pass
                    bolt11 = tag[2]

            if status == "payment-required" and bolt11:
                amount_sat = amount_msat // 1000
                if amount_sat > max_sat:
                    raise PaymentError(
                        f"DVM requests {amount_sat} sats, exceeds max_sat={max_sat}",
                        bolt11=bolt11,
                    )
                log.info("Paying DVM invoice: %d sats", amount_sat)
                from . import payment as pay
                try:
                    pay.pay_invoice(bolt11, max_sat=max_sat)
                    log.info("Payment sent, waiting for result...")
                except PaymentError as exc:
                    raise DVMError(f"Payment failed: {exc}") from exc

            elif status == "processing":
                log.debug("DVM is processing job...")
            elif status == "error":
                reason = ""
                for tag in event.get("tags", []):
                    if tag[0] == "status" and len(tag) > 2:
                        reason = tag[2]
                raise DVMError(f"DVM returned error: {reason}")

        elif evt_kind == result_kind:
            result_content = event.get("content", "")
            log.info(
                "Received result for job %s: %d chars",
                job_event_id[:8], len(result_content)
            )
            return result_content

    raise DVMError(f"No result received within {timeout}s for job {job_event_id[:8]}")


# ---------------------------------------------------------------------------
# hire() — the main client entry point
# ---------------------------------------------------------------------------


async def hire(
    kind: int,
    inputs: list[str | JobInput] | str,
    *,
    relays: list[str] | None = None,
    privkey_hex: str | None = None,
    max_sat: int = 50,
    timeout: float = _JOB_TIMEOUT,
    params: dict[str, str] | None = None,
) -> str:
    """Discover, hire, and get results from a NIP-90 Data Vending Machine.

    This is the primary client API. It handles the full job lifecycle:
    discover → request → pay → receive.

    Parameters
    ----------
    kind:        NIP-90 job kind (use :class:`~nostr_dvm.models.Kind` constants).
    inputs:      Job input(s) — a string, or list of strings/JobInput objects.
    relays:      Nostr relay URLs (defaults to well-known relays).
    privkey_hex: Your private key for signing the job request. Defaults to
                 NOSTR_NSEC / NOSTR_HEX_KEY environment variables.
    max_sat:     Maximum sats you're willing to pay per job (default: 50).
    timeout:     Total seconds to wait for a result (default: 60).
    params:      Extra key/value parameters for the job request.

    Returns:
        The job result as a string.

    Raises:
        NoProviderError: If no DVM for this kind is found on the relays.
        PaymentError:    If the DVM requests more than max_sat.
        DVMError:        If the DVM returns an error or times out.

    Example::

        result = await hire(
            Kind.TEXT_SUMMARIZE,
            "Bitcoin is the first sound money for the digital age...",
            max_sat=20,
        )
        print(result)
    """
    _relays = relays or _DEFAULT_RELAYS
    privkey = privkey_hex or load_privkey()

    if isinstance(inputs, str):
        inputs = [inputs]

    result_kind = Kind.result_kind(kind)

    async with RelayPool(_relays) as pool:
        # Publish job request
        job_id = await _publish_job(
            pool, privkey, kind, inputs,
            bid_sat=max_sat, relays=_relays, params=params
        )

        # Wait for result (handles payment automatically)
        return await _wait_for_result(
            pool, job_id, result_kind, privkey,
            timeout=timeout, max_sat=max_sat,
        )


# ---------------------------------------------------------------------------
# Reputation: publish attestation after successful job
# ---------------------------------------------------------------------------


async def attest(
    dvm_pubkey: str,
    job_event_id: str,
    job_kind: int,
    sats_paid: int,
    quality: int,
    comment: str = "",
    *,
    relays: list[str] | None = None,
    privkey_hex: str | None = None,
) -> str:
    """Publish a reputation attestation after a completed job (kind 1985).

    Attestations form the reputation layer of the DVM economy.
    Quality ratings (1-5 stars), weighted by sats paid, build
    an unforgeable reputation graph over time.

    Returns:
        The attestation event ID.
    """
    _relays = relays or _DEFAULT_RELAYS
    privkey = privkey_hex or load_privkey()

    tags = [
        ["p", dvm_pubkey, "", "DVM"],
        ["e", job_event_id, "", "job"],
        ["L", "nip90"],
        ["l", "quality", "nip90"],
        ["capability", str(job_kind)],
        ["sats_paid", str(sats_paid)],
        ["quality", str(min(max(quality, 1), 5))],
    ]
    content = comment[:500] if comment else f"Job completed successfully. Quality: {quality}/5."
    event = sign_event(1985, content, tags, privkey)

    async with RelayPool(_relays) as pool:
        await pool.publish(event)
    log.info("Published attestation for DVM %s: %d/5 stars", dvm_pubkey[:8], quality)
    return event["id"]


# ---------------------------------------------------------------------------
# Query reputation
# ---------------------------------------------------------------------------


async def reputation(
    dvm_pubkey: str,
    *,
    relays: list[str] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Fetch and aggregate reputation for a DVM from Nostr attestations.

    Returns:
        dict with keys: pubkey, total_attestations, avg_quality,
        total_sats_paid, attested_by (list of unique attestor pubkeys).
    """
    _relays = relays or _DEFAULT_RELAYS
    attestations = []

    async with RelayPool(_relays) as pool:
        filt = {
            "kinds": [1985],
            "#p": [dvm_pubkey],
            "#L": ["nip90"],
        }
        async for event in pool.subscribe(filt, timeout=timeout):
            tags = {t[0]: t[1:] for t in event.get("tags", []) if t}
            try:
                quality = int(tags.get("quality", ["3"])[0])
                sats = int(tags.get("sats_paid", ["0"])[0])
                attestations.append({
                    "quality": quality,
                    "sats_paid": sats,
                    "attester": event.get("pubkey", ""),
                    "created_at": event.get("created_at", 0),
                })
            except (ValueError, IndexError):
                continue

    if not attestations:
        return {
            "pubkey": dvm_pubkey,
            "total_attestations": 0,
            "avg_quality": None,
            "total_sats_paid": 0,
            "attested_by": [],
        }

    avg_quality = sum(a["quality"] for a in attestations) / len(attestations)
    total_sats = sum(a["sats_paid"] for a in attestations)
    unique_atteters = list({a["attester"] for a in attestations})

    return {
        "pubkey": dvm_pubkey,
        "total_attestations": len(attestations),
        "avg_quality": round(avg_quality, 2),
        "total_sats_paid": total_sats,
        "attested_by": unique_atteters,
        "attestations": attestations,
    }
