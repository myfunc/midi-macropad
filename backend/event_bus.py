"""Thread-safe bridge: background threads -> asyncio WebSocket broadcast."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any


class EventBus:
    """Fan-out event bus. Background threads call publish(), WebSocket handlers subscribe().

    Thread safety: publish() uses loop.call_soon_threadsafe() to safely deliver
    messages from any thread into the asyncio event loop.
    """

    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Publish an event to all subscribers. Safe to call from any thread."""
        if self._loop is None or self._loop.is_closed():
            return
        msg = {"type": "event", "event": event, "payload": data or {}, "ts": time.time()}
        for q in list(self._subscribers):
            try:
                self._loop.call_soon_threadsafe(q.put_nowait, msg)
            except (asyncio.QueueFull, RuntimeError):
                pass
