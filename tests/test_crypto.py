"""Tests for Nostr event signing (BIP-340 Schnorr)."""

from __future__ import annotations

import hashlib
import json

import pytest

from nostr_dvm.crypto import (
    generate_keypair,
    load_privkey,
    pubkey_from_privkey,
    serialize_event,
    sign_event,
)


class TestGenerateKeypair:
    def test_returns_64_hex_strings(self):
        priv, pub = generate_keypair()
        assert len(priv) == 64
        assert len(pub) == 64
        assert all(c in "0123456789abcdef" for c in priv)
        assert all(c in "0123456789abcdef" for c in pub)

    def test_unique_keys_each_call(self):
        priv1, _ = generate_keypair()
        priv2, _ = generate_keypair()
        assert priv1 != priv2

    def test_pubkey_derivation_deterministic(self):
        priv, pub = generate_keypair()
        assert pubkey_from_privkey(priv) == pub


class TestSignEvent:
    def _privkey(self) -> str:
        priv, _ = generate_keypair()
        return priv

    def test_event_has_required_fields(self):
        priv = self._privkey()
        event = sign_event(1, "hello nostr", [], priv)
        assert "id" in event
        assert "pubkey" in event
        assert "sig" in event
        assert "kind" in event
        assert "content" in event
        assert "tags" in event
        assert "created_at" in event

    def test_event_id_matches_sha256_of_serialized(self):
        priv = self._privkey()
        event = sign_event(1, "hello", [["t", "bitcoin"]], priv)
        serialized = serialize_event(event)
        expected_id = hashlib.sha256(serialized).hexdigest()
        assert event["id"] == expected_id

    def test_kind_set_correctly(self):
        priv = self._privkey()
        event = sign_event(5100, "content", [], priv)
        assert event["kind"] == 5100

    def test_tags_included(self):
        priv = self._privkey()
        tags = [["e", "abc123"], ["p", "def456"]]
        event = sign_event(1, "", tags, priv)
        assert event["tags"] == tags

    def test_sig_is_128_hex_chars(self):
        priv = self._privkey()
        event = sign_event(1, "test", [], priv)
        assert len(event["sig"]) == 128
        assert all(c in "0123456789abcdef" for c in event["sig"])

    def test_pubkey_matches_derived_pubkey(self):
        priv = self._privkey()
        event = sign_event(1, "test", [], priv)
        assert event["pubkey"] == pubkey_from_privkey(priv)

    def test_unicode_content(self):
        priv = self._privkey()
        event = sign_event(1, "Bitcoin: ₿ sound money 🔑", [], priv)
        assert "₿" in event["content"]

    def test_different_events_different_ids(self):
        priv = self._privkey()
        e1 = sign_event(1, "hello", [], priv)
        e2 = sign_event(1, "world", [], priv)
        assert e1["id"] != e2["id"]
        assert e1["sig"] != e2["sig"]


class TestSerializeEvent:
    def test_format_matches_nip01_spec(self):
        """NIP-01 serialization: [0, pubkey, created_at, kind, tags, content]"""
        event = {
            "pubkey": "abc123",
            "created_at": 1700000000,
            "kind": 1,
            "tags": [["t", "bitcoin"]],
            "content": "hello",
        }
        serialized = serialize_event(event)
        parsed = json.loads(serialized)
        assert parsed[0] == 0
        assert parsed[1] == "abc123"
        assert parsed[2] == 1700000000
        assert parsed[3] == 1
        assert parsed[4] == [["t", "bitcoin"]]
        assert parsed[5] == "hello"


class TestLoadPrivkey:
    def test_loads_hex_key(self, monkeypatch):
        priv, _ = generate_keypair()
        monkeypatch.setenv("NOSTR_HEX_KEY", priv)
        monkeypatch.delenv("NOSTR_NSEC", raising=False)
        assert load_privkey() == priv

    def test_raises_when_no_key(self, monkeypatch):
        monkeypatch.delenv("NOSTR_NSEC", raising=False)
        monkeypatch.delenv("NOSTR_HEX_KEY", raising=False)
        with pytest.raises(ValueError, match="No Nostr private key"):
            load_privkey()

    def test_explicit_arg_takes_priority(self, monkeypatch):
        monkeypatch.setenv("NOSTR_HEX_KEY", "a" * 64)
        priv, _ = generate_keypair()
        assert load_privkey(priv) == priv
