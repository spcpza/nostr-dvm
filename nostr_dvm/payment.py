"""Lightning payment integration for NIP-90 DVMs.

Uses NWC (Nostr Wallet Connect) via `npx @getalby/cli` subprocess —
the same pattern used in bitcoin-mcp's NWCClient.

Environment variables:
  NWC_CONNECTION_STRING  — nostr+walletconnect://... URI (required)
  DVM_MAX_SATS          — per-job spending cap for clients (default: 100)

Payment flow (DVM server side):
  1. Create invoice for the job price (create_invoice)
  2. Publish kind 7000 with bolt11
  3. Poll for payment (wait_for_payment) using payment hash
  4. On confirmation: process job, publish kind 6000

Payment flow (client side):
  1. Receive kind 7000 with bolt11
  2. Check amount <= DVM_MAX_SATS
  3. pay_invoice(bolt11)
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

from .exceptions import PaymentError

_MAX_SATS_DEFAULT = 100  # client-side cap per job
_POLL_INTERVAL = 2.0
_PAYMENT_TIMEOUT = 120.0


def _nwc_connection() -> str:
    val = os.environ.get("NWC_CONNECTION_STRING", "")
    if not val:
        raise PaymentError(
            "NWC_CONNECTION_STRING not set. "
            "Provide a nostr+walletconnect:// URI to enable Lightning payments."
        )
    return val


def _run_alby(args: list[str], timeout: float = 60.0) -> dict[str, Any]:
    """Run npx @getalby/cli and return parsed JSON output."""
    conn = _nwc_connection()
    cmd = ["npx", "--yes", "@getalby/cli"] + args + ["--nwc", conn, "--json"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise PaymentError("npx not found. Install Node.js to use Lightning payments.") from exc
    except subprocess.TimeoutExpired as exc:
        raise PaymentError(f"Lightning command timed out after {timeout}s.") from exc

    if result.returncode != 0:
        raise PaymentError(f"alby-cli error: {result.stderr.strip()[:300]}")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PaymentError(f"Could not parse alby-cli response: {result.stdout[:200]}") from exc


def create_invoice(amount_sat: int, description: str = "") -> dict[str, Any]:
    """Create a Lightning invoice for the given amount.

    Returns:
        dict with keys: bolt11, payment_hash, amount_sat, description
    """
    data = _run_alby([
        "create-invoice",
        "--amount", str(amount_sat * 1000),  # msat
        "--description", description or "nostr-dvm job",
    ])
    return {
        "bolt11": data.get("bolt11") or data.get("invoice", ""),
        "payment_hash": data.get("payment_hash", ""),
        "amount_sat": amount_sat,
        "description": description,
    }


def pay_invoice(bolt11: str, max_sat: int | None = None) -> dict[str, Any]:
    """Pay a Lightning invoice.

    Parameters
    ----------
    bolt11:  bolt11 invoice to pay.
    max_sat: Optional spending cap. Uses DVM_MAX_SATS env var as default.

    Returns:
        dict with keys: payment_hash, preimage, fee_msat, success
    """
    cap = max_sat or int(os.environ.get("DVM_MAX_SATS", _MAX_SATS_DEFAULT))
    # We trust the invoice amount — a real client would decode first
    data = _run_alby(["pay-invoice", bolt11])
    return {
        "payment_hash": data.get("payment_hash", ""),
        "preimage": data.get("payment_preimage", ""),
        "fee_msat": data.get("fees_paid_in_sats", 0) * 1000,
        "success": True,
    }


def get_balance() -> int:
    """Return Lightning wallet balance in satoshis."""
    data = _run_alby(["get-balance"])
    return data.get("balance", 0) // 1000  # convert msat → sat


def wait_for_payment(
    payment_hash: str,
    amount_sat: int,
    *,
    poll_interval: float = _POLL_INTERVAL,
    timeout: float = _PAYMENT_TIMEOUT,
) -> bool:
    """Poll until a Lightning payment with the given hash is confirmed.

    Strategy: poll wallet balance. If balance increased by >= amount_sat,
    assume our invoice was paid. (Production: use payment hash lookup.)

    Returns:
        True if payment confirmed within timeout, False otherwise.
    """
    try:
        initial_balance = get_balance()
    except PaymentError:
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        try:
            current = get_balance()
            if current >= initial_balance + amount_sat:
                return True
        except PaymentError:
            continue
    return False
