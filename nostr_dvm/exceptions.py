"""nostr-dvm exceptions."""


class DVMError(Exception):
    """Base exception for nostr-dvm."""


class RelayError(DVMError):
    """Raised when a relay connection or subscription fails."""

    def __init__(self, relay_url: str, reason: str) -> None:
        self.relay_url = relay_url
        super().__init__(f"[{relay_url}] {reason}")


class PaymentError(DVMError):
    """Raised when a Lightning payment fails or times out."""

    def __init__(self, reason: str, bolt11: str | None = None) -> None:
        self.bolt11 = bolt11
        super().__init__(reason)


class JobError(DVMError):
    """Raised when a job request is malformed or cannot be processed."""

    def __init__(self, job_id: str, reason: str) -> None:
        self.job_id = job_id
        super().__init__(f"[job:{job_id[:8]}] {reason}")


class NoProviderError(DVMError):
    """Raised when hire() finds no DVM offering the requested capability."""

    def __init__(self, kind: int) -> None:
        self.kind = kind
        super().__init__(
            f"No Data Vending Machine found for kind {kind}. "
            "Try increasing timeout or checking your relay list."
        )


class KeyError_(DVMError):
    """Raised when a Nostr private key cannot be loaded."""
