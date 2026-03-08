"""Tests for VendingMachine decorator and job processing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nostr_dvm import Kind, VendingMachine, vending_machine
from nostr_dvm.crypto import generate_keypair
from nostr_dvm.models import JobRequest


def _make_job(kind: int = 5100, text: str = "hello world") -> JobRequest:
    return JobRequest.from_event({
        "id": "a" * 64,
        "pubkey": "b" * 64,
        "created_at": 1700000000,
        "kind": kind,
        "tags": [["i", text, "text"]],
        "content": "",
    })


class TestVendingMachineDecorator:
    def test_decorator_creates_vending_machine(self):
        priv, _ = generate_keypair()

        @vending_machine(kind=5100, name="Test DVM", privkey_hex=priv)
        async def handler(job):
            return "result"

        assert isinstance(handler, VendingMachine)
        assert handler.kind == 5100
        assert handler.name == "Test DVM"

    def test_decorator_copies_docstring_to_about(self):
        priv, _ = generate_keypair()

        @vending_machine(kind=5100, name="X", privkey_hex=priv)
        async def handler(job):
            """Summarizes text documents."""
            return "ok"

        assert "Summarizes text" in handler.about

    def test_explicit_about_takes_priority_over_docstring(self):
        priv, _ = generate_keypair()

        @vending_machine(kind=5100, name="X", about="Explicit about", privkey_hex=priv)
        async def handler(job):
            """Docstring."""
            return "ok"

        assert handler.about == "Explicit about"

    def test_price_sat_stored(self):
        priv, _ = generate_keypair()

        @vending_machine(kind=5100, name="X", price_sat=42, privkey_hex=priv)
        async def handler(job):
            return "ok"

        assert handler.price_sat == 42

    async def test_callable_calls_underlying_function(self):
        priv, _ = generate_keypair()

        @vending_machine(kind=5100, name="X", privkey_hex=priv)
        async def handler(job):
            return f"processed: {job.first_input}"

        job = _make_job(text="bitcoin")
        result = await handler(job)
        assert result == "processed: bitcoin"


class TestVendingMachineProcessJob:
    async def test_process_job_free_publishes_result(self):
        priv, _ = generate_keypair()

        @vending_machine(kind=5100, name="Free DVM", price_sat=0, privkey_hex=priv)
        async def handler(job):
            return "summary result"

        vm = handler
        mock_pool = AsyncMock()
        vm._pool = mock_pool

        job = _make_job()
        await vm._process_job(job)

        # Should have published result (kind 6100)
        calls = mock_pool.publish.call_args_list
        assert len(calls) == 1
        event = calls[0][0][0]
        assert event["kind"] == 6100
        assert event["content"] == "summary result"

    async def test_process_job_handler_exception_publishes_error(self):
        priv, _ = generate_keypair()

        @vending_machine(kind=5100, name="Failing DVM", price_sat=0, privkey_hex=priv)
        async def handler(job):
            raise ValueError("Something went wrong")

        vm = handler
        mock_pool = AsyncMock()
        vm._pool = mock_pool

        job = _make_job()
        await vm._process_job(job)

        calls = mock_pool.publish.call_args_list
        assert len(calls) == 1
        event = calls[0][0][0]
        assert event["kind"] == 7000  # error feedback
        tags = {t[0]: t[1:] for t in event["tags"]}
        assert tags["status"][0] == "error"

    async def test_announce_capability_publishes_kind_31990(self):
        priv, _ = generate_keypair()

        @vending_machine(kind=5100, name="My DVM", about="Does stuff", privkey_hex=priv)
        async def handler(job):
            return "ok"

        vm = handler
        mock_pool = AsyncMock()
        vm._pool = mock_pool

        await vm._announce_capability()

        calls = mock_pool.publish.call_args_list
        assert len(calls) == 1
        event = calls[0][0][0]
        assert event["kind"] == 31990

    async def test_pubkey_derived_from_privkey(self):
        priv, pub = generate_keypair()

        @vending_machine(kind=5100, name="X", privkey_hex=priv)
        async def handler(job):
            return "ok"

        assert handler._pubkey() == pub
