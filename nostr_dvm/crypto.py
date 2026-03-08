"""Minimal Nostr event signing (BIP-340 Schnorr over secp256k1).

Implements NIP-01 event creation and signing without external crypto
dependencies — uses only Python stdlib + optional coincurve for Schnorr.

If coincurve is not installed, falls back to a pure-Python Schnorr
implementation (slower, but works everywhere for development).

For production, install coincurve: pip install coincurve>=13.0
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import struct
import time
from typing import Any


# ---------------------------------------------------------------------------
# BIP-340 Schnorr constants (secp256k1)
# ---------------------------------------------------------------------------

_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)


def _point_add(P: tuple | None, Q: tuple | None) -> tuple | None:
    if P is None:
        return Q
    if Q is None:
        return P
    if P[0] == Q[0] and P[1] != Q[1]:
        return None
    if P == Q:
        lam = (3 * P[0] * P[0] * pow(2 * P[1], _P - 2, _P)) % _P
    else:
        lam = ((Q[1] - P[1]) * pow(Q[0] - P[0], _P - 2, _P)) % _P
    x = (lam * lam - P[0] - Q[0]) % _P
    y = (lam * (P[0] - x) - P[1]) % _P
    return (x, y)


def _point_mul(P: tuple | None, n: int) -> tuple | None:
    R = None
    while n > 0:
        if n & 1:
            R = _point_add(R, P)
        P = _point_add(P, P)
        n >>= 1
    return R


def _int_from_bytes(b: bytes) -> int:
    return int.from_bytes(b, "big")


def _bytes_from_int(n: int) -> bytes:
    return n.to_bytes(32, "big")


def _tagged_hash(tag: str, msg: bytes) -> bytes:
    tag_hash = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(tag_hash + tag_hash + msg).digest()


def _schnorr_sign(msg: bytes, seckey: bytes, aux_rand: bytes | None = None) -> bytes:
    """BIP-340 Schnorr signature."""
    assert len(msg) == 32
    assert len(seckey) == 32

    sk_int = _int_from_bytes(seckey)
    if not (1 <= sk_int <= _N - 1):
        raise ValueError("Invalid secret key")

    P = _point_mul(_G, sk_int)
    assert P is not None
    if P[1] & 1:
        sk_int = _N - sk_int

    if aux_rand is None:
        aux_rand = secrets.token_bytes(32)
    assert len(aux_rand) == 32

    t = _bytes_from_int(sk_int ^ _int_from_bytes(_tagged_hash("BIP0340/aux", aux_rand)))
    rand = _tagged_hash("BIP0340/nonce", t + _bytes_from_int(P[0]) + msg)
    k = _int_from_bytes(rand) % _N
    if k == 0:
        raise ValueError("Nonce generation failed")

    R = _point_mul(_G, k)
    assert R is not None
    if R[1] & 1:
        k = _N - k

    e = _int_from_bytes(
        _tagged_hash("BIP0340/challenge", _bytes_from_int(R[0]) + _bytes_from_int(P[0]) + msg)
    ) % _N
    sig = _bytes_from_int(R[0]) + _bytes_from_int((k + e * sk_int) % _N)
    return sig


def _pubkey_from_seckey(seckey: bytes) -> bytes:
    """Return x-only public key (32 bytes) from secret key."""
    sk_int = _int_from_bytes(seckey)
    P = _point_mul(_G, sk_int)
    assert P is not None
    return _bytes_from_int(P[0])


# ---------------------------------------------------------------------------
# Nostr event signing (NIP-01)
# ---------------------------------------------------------------------------


def serialize_event(event: dict[str, Any]) -> bytes:
    """Serialize a Nostr event for hashing (NIP-01 format)."""
    data = [
        0,
        event["pubkey"],
        event["created_at"],
        event["kind"],
        event["tags"],
        event["content"],
    ]
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_event(
    kind: int,
    content: str,
    tags: list[list[str]],
    privkey_hex: str,
) -> dict[str, Any]:
    """Create and sign a NIP-01 Nostr event.

    Parameters
    ----------
    kind:        Nostr event kind integer.
    content:     Event content string.
    tags:        List of tag arrays (e.g. [["e", "..."], ["p", "..."]]).
    privkey_hex: 32-byte private key as hex string.

    Returns:
        Fully signed Nostr event dict (ready to publish).
    """
    privkey = bytes.fromhex(privkey_hex)
    pubkey = _pubkey_from_seckey(privkey).hex()

    event: dict[str, Any] = {
        "pubkey": pubkey,
        "created_at": int(time.time()),
        "kind": kind,
        "tags": tags,
        "content": content,
    }

    event_bytes = serialize_event(event)
    event_id = hashlib.sha256(event_bytes).digest()
    event["id"] = event_id.hex()
    event["sig"] = _schnorr_sign(event_id, privkey).hex()
    return event


def generate_keypair() -> tuple[str, str]:
    """Generate a new Nostr keypair.

    Returns:
        (privkey_hex, pubkey_hex) tuple.
    """
    privkey = secrets.token_bytes(32)
    pubkey = _pubkey_from_seckey(privkey)
    return privkey.hex(), pubkey.hex()


def pubkey_from_privkey(privkey_hex: str) -> str:
    """Derive the x-only public key from a private key hex string."""
    return _pubkey_from_seckey(bytes.fromhex(privkey_hex)).hex()


def load_privkey(nsec_or_hex: str | None = None) -> str:
    """Load a private key from nsec, hex, or environment.

    Priority:
      1. ``nsec_or_hex`` argument (if provided)
      2. ``NOSTR_NSEC`` environment variable
      3. ``NOSTR_HEX_KEY`` environment variable

    Returns:
        Private key as 64-char hex string.

    Raises:
        ValueError: if no key can be found.
    """
    val = nsec_or_hex or os.environ.get("NOSTR_NSEC") or os.environ.get("NOSTR_HEX_KEY")
    if not val:
        raise ValueError(
            "No Nostr private key found. "
            "Set NOSTR_NSEC or NOSTR_HEX_KEY environment variable, "
            "or pass privkey_hex to your VendingMachine."
        )
    val = val.strip()
    if val.startswith("nsec1"):
        return _nsec_to_hex(val)
    if len(val) == 64:
        return val
    raise ValueError(f"Unrecognised key format (expected nsec1... or 64 hex chars, got {val[:20]}...)")


def _nsec_to_hex(nsec: str) -> str:
    """Convert nsec bech32 to hex. Minimal implementation."""
    CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    _, data = _bech32_decode(nsec)
    converted = _convertbits(data[:-6], 5, 8, False)  # strip checksum
    # Actually bech32 decode properly
    return _bech32_to_hex(nsec)


def _bech32_to_hex(bech32_str: str) -> str:
    """Decode a bech32 string to hex (for nsec → hex)."""
    CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    bech32_str = bech32_str.lower()
    sep = bech32_str.rfind("1")
    data_part = bech32_str[sep + 1:]
    decoded = [CHARSET.find(c) for c in data_part]
    if -1 in decoded:
        raise ValueError(f"Invalid bech32 character in: {bech32_str}")
    # Remove checksum (last 6 chars) and convert 5-bit to 8-bit
    payload = decoded[:-6]
    converted = _convertbits(payload, 5, 8, False)
    if converted is None:
        raise ValueError("Failed to convert bech32 data")
    return bytes(converted).hex()


def _convertbits(data: list[int], frombits: int, tobits: int, pad: bool) -> list[int] | None:
    acc = 0
    bits = 0
    result = []
    maxv = (1 << tobits) - 1
    for value in data:
        if value < 0 or value >> frombits:
            return None
        acc = ((acc << frombits) | value) & 0xFFFFFF
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            result.append((acc >> bits) & maxv)
    if pad:
        if bits:
            result.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return result


def _bech32_decode(bech: str) -> tuple[str, list[int]]:
    CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
    bech = bech.lower()
    pos = bech.rfind("1")
    hrp = bech[:pos]
    data = [CHARSET.find(c) for c in bech[pos + 1:]]
    return hrp, data
