"""Session telemetry aggregator.

Subscribes to EventBus via a sync callback and counts events per session.
On shutdown (or on-demand) dumps a JSON snapshot to ``logs/telemetry/``
and calls ``log_session_summary`` from logger.py.

This is a pure observer — never publishes back, never blocks the bus.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from logger import log_session_summary


class TelemetryAggregator:
    """In-memory counters for one app session.

    Thread-safe: publish() callbacks run from whichever thread fired the
    event, so all counter mutations are guarded by a single lock.
    """

    def __init__(self, project_root: Path):
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._session_id = datetime.fromtimestamp(self._started_at).strftime(
            "%Y%m%d_%H%M%S"
        )
        self._dump_dir = project_root / "logs" / "telemetry"
        self._dump_dir.mkdir(parents=True, exist_ok=True)

        self._pad_presses: dict[int, int] = defaultdict(int)
        self._knob_moves: dict[int, int] = defaultdict(int)
        self._preset_changes: list[dict] = []
        self._plugin_errors = 0
        self._plugin_toggles: list[dict] = []
        self._midi_events_total = 0
        self._last_event_ts = self._started_at
        self._plugins_enabled: list[str] = []

    # ------------------------------------------------------------------
    # EventBus hook
    # ------------------------------------------------------------------

    def on_event(self, event: str, payload: dict, ts: float) -> None:
        with self._lock:
            self._last_event_ts = ts
            if event == "midi.pad_press":
                note = int(payload.get("note", -1))
                if note >= 0:
                    self._pad_presses[note] += 1
                self._midi_events_total += 1
            elif event == "midi.pad_release":
                self._midi_events_total += 1
            elif event == "midi.knob":
                cc = int(payload.get("cc", -1))
                if cc >= 0:
                    self._knob_moves[cc] += 1
                self._midi_events_total += 1
            elif event == "midi.pitch_bend":
                self._midi_events_total += 1
            elif event == "preset.changed":
                self._preset_changes.append({
                    "ts": ts,
                    "index": payload.get("index"),
                    "name": payload.get("name"),
                })
            elif event == "plugins.changed":
                self._plugin_toggles.append({
                    "ts": ts,
                    "name": payload.get("name"),
                    "enabled": payload.get("enabled"),
                })
            elif event == "error.unhandled":
                self._plugin_errors += 1

    # ------------------------------------------------------------------
    # Snapshot / dump
    # ------------------------------------------------------------------

    def set_plugins_enabled(self, names: list[str]) -> None:
        with self._lock:
            self._plugins_enabled = list(names)

    def snapshot(self) -> dict:
        with self._lock:
            now = time.time()
            return {
                "session_id": self._session_id,
                "started_at": datetime.fromtimestamp(
                    self._started_at).isoformat(timespec="seconds"),
                "last_event_at": datetime.fromtimestamp(
                    self._last_event_ts).isoformat(timespec="seconds"),
                "duration_s": round(now - self._started_at, 1),
                "midi_events_total": self._midi_events_total,
                "pad_presses": {str(n): c for n, c in
                                sorted(self._pad_presses.items())},
                "knob_moves": {str(cc): c for cc, c in
                               sorted(self._knob_moves.items())},
                "preset_changes": list(self._preset_changes),
                "plugin_toggles": list(self._plugin_toggles),
                "plugin_errors": self._plugin_errors,
                "plugins_enabled": list(self._plugins_enabled),
                "top_pads": sorted(
                    ((str(n), c) for n, c in self._pad_presses.items()),
                    key=lambda x: -x[1],
                )[:5],
            }

    def dump(self) -> Path:
        """Write current snapshot to disk atomically. Returns the file path."""
        snap = self.snapshot()
        path = self._dump_dir / f"session_{self._session_id}.json"
        fd, tmp = tempfile.mkstemp(
            prefix=".tele_", suffix=".tmp", dir=str(self._dump_dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(snap, f, indent=2, ensure_ascii=False)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

        # Bridge to the old log_session_summary stub
        try:
            log_session_summary(
                midi_events=snap["midi_events_total"],
                plugin_errors=snap["plugin_errors"],
                duration_s=snap["duration_s"],
            )
        except Exception:
            pass
        return path
