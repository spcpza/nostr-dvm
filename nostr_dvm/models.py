"""Data models for NIP-90 Data Vending Machine protocol.

NIP-90 Reference: https://github.com/nostr-protocol/nostr/blob/master/90.md

Job lifecycle:
  1. Customer publishes JobRequest  (kind 5000-5999)
  2. DVM optionally publishes JobFeedback (kind 7000) requesting payment
  3. Customer pays Lightning invoice
  4. DVM publishes JobResult (kind 6000-6999)
  5. Optional: reputation attestation (kind 1985)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# NIP-90 job kinds (partial — add your own in the 5000-5999 range)
# ---------------------------------------------------------------------------

class Kind:
    """NIP-90 job request kinds. Response kind = request kind + 1000."""

    # Text / language
    TEXT_SUMMARIZE = 5100
    TEXT_TRANSLATE = 5200
    TEXT_CLASSIFY = 5202
    TEXT_GENERATE = 5250

    # Code
    CODE_REVIEW = 5400
    CODE_COMPLETE = 5401
    CODE_EXPLAIN = 5402

    # Data / research
    SEARCH_WEB = 5300
    SEARCH_NOSTR = 5302
    DATA_LOOKUP = 5350

    # Bitcoin / Lightning
    BTC_ANALYSIS = 5600
    INVOICE_DECODE = 5601
    MEMPOOL_SUMMARY = 5602

    # Generic / custom
    CUSTOM = 5999

    @staticmethod
    def result_kind(request_kind: int) -> int:
        """Convert a job request kind to its result kind (+ 1000)."""
        return request_kind + 1000


# ---------------------------------------------------------------------------
# Job Request (kind 5000-5999, published by customer)
# ---------------------------------------------------------------------------

@dataclass
class JobInput:
    """A single input to a job request."""
    value: str
    input_type: str = "text"   # "text" | "url" | "event" | "job"
    relay: str | None = None
    marker: str | None = None  # optional label for multi-input jobs

    def to_tag(self) -> list[str]:
        tag = ["i", self.value, self.input_type]
        if self.relay:
            tag.append(self.relay)
        if self.marker:
            tag.append(self.marker)
        return tag


@dataclass
class JobRequest:
    """A NIP-90 job request published by a customer."""
    kind: int
    inputs: list[JobInput]
    event_id: str = ""
    pubkey: str = ""
    created_at: int = 0

    # Optional parameters
    output_type: str = "text/plain"     # MIME type of expected output
    bid_msat: int = 0                   # max the customer will pay in msat
    relays: list[str] = field(default_factory=list)
    params: dict[str, str] = field(default_factory=dict)  # extra k/v params

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> "JobRequest":
        """Parse a JobRequest from a raw Nostr event dict."""
        inputs = []
        output_type = "text/plain"
        bid_msat = 0
        relays = []
        params = {}

        for tag in event.get("tags", []):
            if not tag:
                continue
            name = tag[0]
            if name == "i" and len(tag) >= 2:
                inputs.append(JobInput(
                    value=tag[1],
                    input_type=tag[2] if len(tag) > 2 else "text",
                    relay=tag[3] if len(tag) > 3 else None,
                    marker=tag[4] if len(tag) > 4 else None,
                ))
            elif name == "output" and len(tag) >= 2:
                output_type = tag[1]
            elif name == "bid" and len(tag) >= 2:
                try:
                    bid_msat = int(tag[1])
                except (ValueError, IndexError):
                    pass
            elif name == "relays" and len(tag) >= 2:
                relays = tag[1:]
            elif name == "param" and len(tag) >= 3:
                params[tag[1]] = tag[2]

        return cls(
            kind=event.get("kind", 5999),
            inputs=inputs,
            event_id=event.get("id", ""),
            pubkey=event.get("pubkey", ""),
            created_at=event.get("created_at", 0),
            output_type=output_type,
            bid_msat=bid_msat,
            relays=relays,
            params=params,
        )

    @property
    def text_inputs(self) -> list[str]:
        """Return the values of all text-type inputs."""
        return [i.value for i in self.inputs if i.input_type == "text"]

    @property
    def first_input(self) -> str:
        """Return the first input value, or empty string."""
        return self.inputs[0].value if self.inputs else ""


# ---------------------------------------------------------------------------
# Job Result (kind 6000-6999, published by DVM)
# ---------------------------------------------------------------------------

@dataclass
class JobResult:
    """A NIP-90 job result published by a Data Vending Machine."""
    request: JobRequest
    content: str
    status: str = "success"          # "success" | "error" | "partial"
    amount_msat: int = 0             # sats to pay (if post-payment model)
    bolt11: str = ""                 # Lightning invoice for post-payment

    def to_tags(self, dvm_pubkey: str) -> list[list[str]]:
        """Build the tags list for the result event."""
        tags = [
            ["request", ""],  # filled in by event builder
            ["e", self.request.event_id, "", "reply"],
            ["p", self.request.pubkey],
            ["status", self.status],
        ]
        if self.amount_msat > 0 and self.bolt11:
            tags.append(["amount", str(self.amount_msat), self.bolt11])
        return tags


# ---------------------------------------------------------------------------
# Job Feedback (kind 7000, published by DVM to request payment or give status)
# ---------------------------------------------------------------------------

@dataclass
class JobFeedback:
    """A NIP-90 feedback event (kind 7000) — payment request or status."""
    request: JobRequest
    status: str           # "payment-required" | "processing" | "error" | "success"
    extra_info: str = ""
    amount_msat: int = 0
    bolt11: str = ""

    def to_tags(self) -> list[list[str]]:
        tags = [
            ["e", self.request.event_id, "", "reply"],
            ["p", self.request.pubkey],
            ["status", self.status, self.extra_info],
        ]
        if self.amount_msat > 0 and self.bolt11:
            tags.append(["amount", str(self.amount_msat), self.bolt11])
        return tags


# ---------------------------------------------------------------------------
# DVM Capability (NIP-89 kind 31990, published by DVM to announce itself)
# ---------------------------------------------------------------------------

@dataclass
class DVMCapability:
    """NIP-89/90 capability announcement (kind 31990)."""
    name: str
    about: str
    job_kind: int
    price_sat: int = 0
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    nip90_params: list[dict] = field(default_factory=list)

    def to_content(self) -> str:
        import json
        return json.dumps({
            "name": self.name,
            "about": self.about,
            "nip90Params": {
                str(self.job_kind): {
                    "requiresPayment": self.price_sat > 0,
                    "priceInSats": self.price_sat,
                    "inputSchema": self.input_schema,
                    "outputSchema": self.output_schema,
                    "params": self.nip90_params,
                }
            }
        })

    def to_tags(self, unique_id: str) -> list[list[str]]:
        return [
            ["k", str(self.job_kind)],
            ["d", unique_id],
        ]


# ---------------------------------------------------------------------------
# Reputation Attestation (kind 1985, published after successful job)
# ---------------------------------------------------------------------------

@dataclass
class Attestation:
    """A signed reputation attestation after a completed job."""
    dvm_pubkey: str
    job_event_id: str
    job_kind: int
    sats_paid: int
    quality: int     # 1-5 stars
    comment: str = ""

    def to_tags(self) -> list[list[str]]:
        return [
            ["p", self.dvm_pubkey, "", "DVM"],
            ["e", self.job_event_id, "", "job"],
            ["L", "nip90"],
            ["l", "quality", "nip90"],
            ["capability", str(self.job_kind)],
            ["sats_paid", str(self.sats_paid)],
            ["quality", str(self.quality)],
        ]
