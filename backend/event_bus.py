"""Thread-safe bridge: background threads -> asyncio WebSocket broadcast."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable


class EventBus:
    """Fan-out event bus.

    Two subscription channels:
      * async (WebSocket) — via :meth:`subscribe` returning an asyncio.Queue.
      * sync (in-process) — via :meth:`subscribe_sync` taking a callback
        ``fn(event_name, payload, ts)``. Used by telemetry and other
        local aggregators that must see every event without hopping
        through the asyncio loop.

    publish() safely delivers from any thread: sync callbacks run inline,
    async queues are fed via loop.call_soon_threadsafe().
    """

    def __init__(self):
        self._subscribers: set[asyncio.Queue] = set()
        self._sync_subs: list[Callable[[str, dict, float], None]] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def subscribe_sync(
        self, callback: Callable[[str, dict, float], None]
    ) -> None:
        """Register an in-process subscriber. Called inline from publish()."""
        self._sync_subs.append(callback)

    def unsubscribe_sync(
        self, callback: Callable[[str, dict, float], None]
    ) -> None:
        try:
            self._sync_subs.remove(callback)
        except ValueError:
            pass

    def publish(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Publish an event to all subscribers. Safe to call from any thread."""
        ts = time.time()
        payload = data or {}
        for cb in list(self._sync_subs):
            try:
                cb(event, payload, ts)
            except Exception:
                pass
        if self._loop is None or self._loop.is_closed():
            return
        msg = {"type": "event", "event": event, "payload": payload, "ts": ts}
        for q in list(self._subscribers):
            try:
                self._loop.call_soon_threadsafe(q.put_nowait, msg)
            except (asyncio.QueueFull, RuntimeError):
                pass
