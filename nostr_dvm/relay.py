"""Nostr relay WebSocket client.

Handles connect/disconnect, subscriptions, and publishing events
to one or more Nostr relays via the NIP-01 wire protocol.

Wire protocol:
  Publish:   ["EVENT", <event_json>]
  Subscribe: ["REQ", <sub_id>, <filter>]
  Close:     ["CLOSE", <sub_id>]
  Receive:   ["EVENT", <sub_id>, <event_json>]
           | ["EOSE", <sub_id>]
           | ["NOTICE", <message>]
           | ["OK", <event_id>, <true|false>, <message>]
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from .exceptions import RelayError

log = logging.getLogger(__name__)

_DEFAULT_RELAYS = [
    "wss://relay.damus.io",
    "wss://nos.lol",
    "wss://relay.nostr.band",
]

_CONNECT_TIMEOUT = 10.0
_SEND_TIMEOUT = 5.0


# ---------------------------------------------------------------------------
# RelayPool
# ---------------------------------------------------------------------------


class RelayPool:
    """Manages connections to multiple Nostr relays.

    Usage::

        async with RelayPool(["wss://relay.damus.io"]) as pool:
            await pool.publish(event_dict)
            async for event in pool.subscribe({"kinds": [5100]}):
                print(event)
    """

    def __init__(self, relays: list[str] | None = None) -> None:
        self.relays = relays or _DEFAULT_RELAYS
        self._connections: dict[str, Any] = {}
        self._incoming: asyncio.Queue[dict] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._sub_ids: set[str] = set()

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "RelayPool":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Connect / close
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open connections to all configured relays."""
        for url in self.relays:
            try:
                ws = await asyncio.wait_for(
                    websockets.connect(url, ping_interval=30, ping_timeout=10),
                    timeout=_CONNECT_TIMEOUT,
                )
                self._connections[url] = ws
                task = asyncio.create_task(self._listen(url, ws))
                self._tasks.append(task)
                log.debug("Connected to %s", url)
            except Exception as exc:
                log.warning("Failed to connect to %s: %s", url, exc)

        if not self._connections:
            raise RelayError(str(self.relays), "Could not connect to any relay")

    async def close(self) -> None:
        """Close all relay connections."""
        for task in self._tasks:
            task.cancel()
        for ws in self._connections.values():
            try:
                await ws.close()
            except Exception:
                pass
        self._connections.clear()
        self._tasks.clear()

    # ------------------------------------------------------------------
    # Publish
    # ------------------------------------------------------------------

    async def publish(self, event: dict[str, Any]) -> None:
        """Publish a signed Nostr event to all connected relays."""
        msg = json.dumps(["EVENT", event])
        dead = []
        for url, ws in self._connections.items():
            try:
                await asyncio.wait_for(ws.send(msg), timeout=_SEND_TIMEOUT)
                log.debug("Published event %s to %s", event.get("id", "")[:8], url)
            except Exception as exc:
                log.warning("Failed to publish to %s: %s", url, exc)
                dead.append(url)
        for url in dead:
            self._connections.pop(url, None)

    # ------------------------------------------------------------------
    # Subscribe
    # ------------------------------------------------------------------

    async def subscribe(
        self,
        filters: dict[str, Any] | list[dict[str, Any]],
        *,
        timeout: float = 60.0,
        limit: int | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Subscribe to events matching filters.

        Parameters
        ----------
        filters:  A single filter dict or list of filter dicts (NIP-01 format).
        timeout:  Yield StopAsyncIteration after this many seconds.
        limit:    Stop after receiving this many events.

        Yields:
            Raw Nostr event dicts.
        """
        if isinstance(filters, dict):
            filters = [filters]

        sub_id = uuid.uuid4().hex[:16]
        self._sub_ids.add(sub_id)
        req = json.dumps(["REQ", sub_id, *filters])

        for ws in self._connections.values():
            try:
                await asyncio.wait_for(ws.send(req), timeout=_SEND_TIMEOUT)
            except Exception as exc:
                log.warning("Failed to send REQ: %s", exc)

        count = 0
        deadline = asyncio.get_event_loop().time() + timeout
        try:
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    event = await asyncio.wait_for(
                        self._incoming.get(), timeout=min(remaining, 1.0)
                    )
                except asyncio.TimeoutError:
                    continue

                if event.get("_sub_id") != sub_id:
                    # Not for this subscription — put it back for other consumers
                    await self._incoming.put(event)
                    await asyncio.sleep(0)
                    continue

                yield event
                count += 1
                if limit is not None and count >= limit:
                    break
        finally:
            self._sub_ids.discard(sub_id)
            close_msg = json.dumps(["CLOSE", sub_id])
            for ws in self._connections.values():
                try:
                    await ws.send(close_msg)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Internal listener
    # ------------------------------------------------------------------

    async def _listen(self, url: str, ws: Any) -> None:
        """Background task: read messages from a relay and queue them."""
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, list) or len(msg) < 2:
                    continue

                msg_type = msg[0]
                if msg_type == "EVENT" and len(msg) >= 3:
                    sub_id = msg[1]
                    event = msg[2]
                    event["_sub_id"] = sub_id
                    await self._incoming.put(event)
                elif msg_type == "NOTICE":
                    log.debug("[%s] NOTICE: %s", url, msg[1] if len(msg) > 1 else "")
                elif msg_type == "OK":
                    event_id = msg[1] if len(msg) > 1 else ""
                    ok = msg[2] if len(msg) > 2 else False
                    reason = msg[3] if len(msg) > 3 else ""
                    if not ok:
                        log.warning("[%s] Event rejected: %s — %s", url, event_id[:8], reason)
        except ConnectionClosed:
            log.info("Relay %s disconnected", url)
        except Exception as exc:
            log.warning("Relay %s listener error: %s", url, exc)
        finally:
            self._connections.pop(url, None)
