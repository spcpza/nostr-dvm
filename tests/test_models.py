"""Tests for NIP-90 data models."""

from __future__ import annotations

import json

import pytest

from nostr_dvm.models import (
    Attestation,
    DVMCapability,
    JobFeedback,
    JobInput,
    JobRequest,
    Kind,
)


class TestKind:
    def test_result_kind_adds_1000(self):
        assert Kind.result_kind(5100) == 6100
        assert Kind.result_kind(5999) == 6999
        assert Kind.result_kind(5000) == 6000

    def test_constants_in_correct_range(self):
        for attr in dir(Kind):
            val = getattr(Kind, attr)
            if isinstance(val, int):
                assert 5000 <= val <= 5999, f"Kind.{attr}={val} out of 5000-5999 range"


class TestJobInput:
    def test_to_tag_text(self):
        inp = JobInput(value="hello world", input_type="text")
        tag = inp.to_tag()
        assert tag == ["i", "hello world", "text"]

    def test_to_tag_url(self):
        inp = JobInput(value="https://example.com", input_type="url")
        tag = inp.to_tag()
        assert tag[0] == "i"
        assert tag[1] == "https://example.com"
        assert tag[2] == "url"

    def test_to_tag_with_relay_and_marker(self):
        inp = JobInput(
            value="event123",
            input_type="event",
            relay="wss://relay.damus.io",
            marker="summary",
        )
        tag = inp.to_tag()
        assert tag == ["i", "event123", "event", "wss://relay.damus.io", "summary"]

    def test_to_tag_without_optional_fields(self):
        inp = JobInput(value="text", input_type="text")
        tag = inp.to_tag()
        assert len(tag) == 3  # no relay, no marker


class TestJobRequest:
    def _event(self, **overrides) -> dict:
        base = {
            "id": "abc123def456" * 5,
            "pubkey": "pubkey_hex" * 6 + "ab",
            "created_at": 1700000000,
            "kind": 5100,
            "tags": [
                ["i", "summarize this text", "text"],
                ["output", "text/plain"],
                ["bid", "10000"],
                ["relays", "wss://relay.damus.io", "wss://nos.lol"],
                ["param", "language", "en"],
            ],
            "content": "",
        }
        base.update(overrides)
        return base

    def test_from_event_parses_inputs(self):
        job = JobRequest.from_event(self._event())
        assert len(job.inputs) == 1
        assert job.inputs[0].value == "summarize this text"
        assert job.inputs[0].input_type == "text"

    def test_from_event_parses_bid(self):
        job = JobRequest.from_event(self._event())
        assert job.bid_msat == 10000

    def test_from_event_parses_relays(self):
        job = JobRequest.from_event(self._event())
        assert "wss://relay.damus.io" in job.relays

    def test_from_event_parses_params(self):
        job = JobRequest.from_event(self._event())
        assert job.params.get("language") == "en"

    def test_first_input_returns_value(self):
        job = JobRequest.from_event(self._event())
        assert job.first_input == "summarize this text"

    def test_first_input_empty_when_no_inputs(self):
        event = self._event(tags=[])
        job = JobRequest.from_event(event)
        assert job.first_input == ""

    def test_text_inputs_filters_non_text(self):
        event = self._event(tags=[
            ["i", "text content", "text"],
            ["i", "https://example.com", "url"],
            ["i", "event_id", "event"],
        ])
        job = JobRequest.from_event(event)
        assert job.text_inputs == ["text content"]

    def test_from_event_unknown_kind(self):
        event = self._event(kind=5999)
        job = JobRequest.from_event(event)
        assert job.kind == 5999

    def test_from_event_malformed_bid_ignored(self):
        event = self._event(tags=[["bid", "not_a_number"]])
        job = JobRequest.from_event(event)
        assert job.bid_msat == 0


class TestDVMCapability:
    def test_to_content_includes_kind(self):
        cap = DVMCapability(
            name="Summarizer",
            about="Summarizes text",
            job_kind=5100,
            price_sat=10,
        )
        content = json.loads(cap.to_content())
        assert "5100" in content.get("nip90Params", {})
        assert content["name"] == "Summarizer"

    def test_to_content_price(self):
        cap = DVMCapability(name="X", about="", job_kind=5100, price_sat=50)
        content = json.loads(cap.to_content())
        assert content["nip90Params"]["5100"]["priceInSats"] == 50
        assert content["nip90Params"]["5100"]["requiresPayment"] is True

    def test_free_dvm_requires_payment_false(self):
        cap = DVMCapability(name="Free", about="", job_kind=5100, price_sat=0)
        content = json.loads(cap.to_content())
        assert content["nip90Params"]["5100"]["requiresPayment"] is False

    def test_to_tags_includes_kind_and_id(self):
        cap = DVMCapability(name="X", about="", job_kind=5100)
        tags = cap.to_tags("unique-id-123")
        tag_dict = {t[0]: t[1:] for t in tags}
        assert tag_dict.get("k") == ["5100"]
        assert tag_dict.get("d") == ["unique-id-123"]


class TestAttestation:
    def test_to_tags_includes_quality_and_sats(self):
        att = Attestation(
            dvm_pubkey="pub123",
            job_event_id="event456",
            job_kind=5100,
            sats_paid=10,
            quality=5,
            comment="Excellent!",
        )
        tags = att.to_tags()
        tag_dict = {}
        for t in tags:
            tag_dict[t[0]] = t[1:]
        assert tag_dict["quality"] == ["5"]
        assert tag_dict["sats_paid"] == ["10"]
        assert tag_dict["p"] == ["pub123", "", "DVM"]
