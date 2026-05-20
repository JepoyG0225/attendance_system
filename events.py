"""
Live event bus — server-sent events (SSE) backend.
=====================================================
A tiny in-memory pub/sub so the dashboard can react to scans, SMS, and other
events the moment they happen, without polling.

Design notes:
- One asyncio.Queue per connected dashboard client. Slow clients can't block
  the others, and if a queue fills up (e.g. tab hidden for ages) we drop the
  client rather than backpressuring the publisher.
- publish() is **sync-safe** so route handlers (which FastAPI runs in a
  worker threadpool) can fire events without awaiting anything. Internally we
  hop back to the event loop with call_soon_threadsafe.
- No persistence — events that happen while a tab is closed are simply missed.
  The next page load will re-fetch from /attendance anyway.
"""

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self, queue_size: int = 200) -> None:
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── Lifecycle hooks ───────────────────────────────────────────────────────

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once at app startup so publish() can hop threads safely."""
        self._loop = loop

    # ── Subscriber side (async) ───────────────────────────────────────────────

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    # ── Publisher side (callable from sync OR async code) ─────────────────────

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """Fan-out an event to every connected client. Safe from any thread."""
        payload = {"type": event_type, "data": data}
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(self._fan_out, payload)
            except RuntimeError:
                # Loop is shutting down — drop silently.
                logger.debug("EventBus.publish: loop not accepting callbacks")
        else:
            # No loop running (e.g. unit tests) — best-effort sync fan-out.
            self._fan_out(payload)

    def _fan_out(self, payload: dict) -> None:
        dropped = []
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dropped.append(q)
        for q in dropped:
            logger.warning("Dropping slow SSE subscriber (queue full)")
            self._subscribers.discard(q)


# Module-level singleton — import and use directly.
bus = EventBus()
