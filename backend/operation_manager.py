"""Async operation manager — background tasks with idempotency and progress tracking."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Callable

from backend.event_bus import EventBus

log = logging.getLogger("midi_macropad.ops")


class OpStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class Operation:
    id: str
    op_type: str
    params: dict
    status: OpStatus = OpStatus.PENDING
    progress: float = 0.0
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "op_type": self.op_type,
            "params": self.params,
            "status": self.status.value,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _idempotency_key(op_type: str, params: dict) -> str:
    raw = json.dumps({"type": op_type, **params}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class OperationManager:
    """Manages background operations with idempotency and progress.

    Usage:
        mgr = OperationManager(event_bus)
        mgr.register("obs.stitch_video", stitch_fn)
        op = mgr.start("obs.stitch_video", {"session_dir": "/path"})
        # op.status == "running", progress updates via WebSocket
    """

    def __init__(self, event_bus: EventBus, max_workers: int = 4):
        self._ops: dict[str, Operation] = {}
        self._idempotency: dict[str, str] = {}  # hash -> op_id
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="op")
        self._event_bus = event_bus
        self._handlers: dict[str, Callable] = {}

    def register(self, op_type: str, handler: Callable) -> None:
        """Register a handler: fn(params, progress_fn, cancel_event) -> result."""
        self._handlers[op_type] = handler

    def start(self, op_type: str, params: dict) -> Operation:
        """Start an operation. Returns existing if same type+params already running."""
        if op_type not in self._handlers:
            raise ValueError(f"Unknown operation type: {op_type}")

        key = _idempotency_key(op_type, params)

        with self._lock:
            # Idempotency check
            if key in self._idempotency:
                existing_id = self._idempotency[key]
                existing = self._ops.get(existing_id)
                if existing and existing.status in (OpStatus.PENDING, OpStatus.RUNNING):
                    log.info("Op %s already running (id=%s), returning existing",
                             op_type, existing_id)
                    return existing

            op_id = str(uuid.uuid4())[:8]
            op = Operation(id=op_id, op_type=op_type, params=params)
            self._ops[op_id] = op
            self._idempotency[key] = op_id
            cancel_evt = threading.Event()
            self._cancel_events[op_id] = cancel_evt

        log.info("Starting op %s (id=%s, params=%s)", op_type, op_id, params)
        self._notify(op)

        def progress_fn(value: float) -> None:
            op.progress = min(1.0, max(0.0, value))
            op.updated_at = time.time()
            self._notify(op)

        self._executor.submit(self._run, op_id, self._handlers[op_type],
                              params, progress_fn, cancel_evt)
        return op

    def _run(self, op_id: str, handler: Callable, params: dict,
             progress_fn: Callable, cancel_event: threading.Event) -> None:
        op = self._ops[op_id]
        op.status = OpStatus.RUNNING
        op.updated_at = time.time()
        self._notify(op)

        try:
            result = handler(params, progress_fn, cancel_event)
            if cancel_event.is_set():
                op.status = OpStatus.CANCELLED
                log.info("Op %s cancelled (id=%s)", op.op_type, op_id)
            else:
                op.status = OpStatus.DONE
                op.result = result
                op.progress = 1.0
                log.info("Op %s done (id=%s)", op.op_type, op_id)
        except Exception as exc:
            op.status = OpStatus.ERROR
            op.error = str(exc)
            log.error("Op %s failed (id=%s): %s", op.op_type, op_id, exc)

        op.updated_at = time.time()
        self._notify(op)

        # Cleanup idempotency key so the same op can be started again
        key = _idempotency_key(op.op_type, params)
        with self._lock:
            self._idempotency.pop(key, None)
            self._cancel_events.pop(op_id, None)

    def get(self, op_id: str) -> Operation | None:
        return self._ops.get(op_id)

    def get_all(self) -> list[dict]:
        return [op.to_dict() for op in self._ops.values()]

    def cancel(self, op_id: str) -> bool:
        evt = self._cancel_events.get(op_id)
        if evt is None:
            return False
        evt.set()
        return True

    def _notify(self, op: Operation) -> None:
        self._event_bus.publish("ops.update", op.to_dict())

    def shutdown(self) -> None:
        # Cancel all running ops
        for evt in self._cancel_events.values():
            evt.set()
        self._executor.shutdown(wait=False)
