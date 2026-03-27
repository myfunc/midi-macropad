"""Shared MIDI feedback service tuned for MPK Mini Play."""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass

import mido


@dataclass(frozen=True)
class MidiStep:
    notes: tuple[int, ...]
    duration_ms: int
    velocity: int = 88
    gap_ms: int = 18
    channel: int = 0


@dataclass(frozen=True)
class MidiCue:
    steps: tuple[MidiStep, ...]
    program: int | None = 80
    channel: int = 0
    volume: int = 92
    expression: int = 112


MIDI_CUES: dict[str, MidiCue] = {
    # Favor steel/clean guitar-style arpeggios and simple intervals.
    # On MPK Mini Play these longer low-mid phrases read more musically than
    # short bright bleeps.
    "action.default": MidiCue(
        program=25,
        steps=(
            MidiStep((45,), 56, velocity=88, gap_ms=18),
            MidiStep((52,), 62, velocity=94, gap_ms=18),
            MidiStep((57, 64), 138, velocity=100, gap_ms=26),
        ),
        volume=112,
        expression=124,
    ),
    "action.navigation": MidiCue(
        program=27,
        steps=(
            MidiStep((50,), 46, velocity=84, gap_ms=16),
            MidiStep((54,), 50, velocity=90, gap_ms=16),
            MidiStep((57,), 58, velocity=96, gap_ms=16),
            MidiStep((62,), 88, velocity=102, gap_ms=22),
        ),
        volume=110,
        expression=123,
    ),
    "action.toggle_on": MidiCue(
        program=25,
        steps=(
            MidiStep((45,), 58, velocity=90, gap_ms=16),
            MidiStep((52,), 66, velocity=96, gap_ms=16),
            MidiStep((57,), 74, velocity=102, gap_ms=16),
            MidiStep((61, 64), 132, velocity=106, gap_ms=24),
        ),
        volume=114,
        expression=125,
    ),
    "action.toggle_off": MidiCue(
        program=27,
        steps=(
            MidiStep((64,), 52, velocity=86, gap_ms=16),
            MidiStep((57,), 56, velocity=82, gap_ms=16),
            MidiStep((52,), 62, velocity=78, gap_ms=16),
            MidiStep((45,), 120, velocity=86, gap_ms=24),
        ),
        volume=110,
        expression=122,
    ),
    "action.danger": MidiCue(
        program=38,
        steps=(
            MidiStep((40,), 72, velocity=102, gap_ms=18),
            MidiStep((39,), 118, velocity=106, gap_ms=22),
        ),
        volume=118,
        expression=127,
    ),
    "action.error": MidiCue(
        program=38,
        steps=(
            MidiStep((41, 42), 128, velocity=108, gap_ms=24),
        ),
        volume=120,
        expression=127,
    ),
    "voice.record_start": MidiCue(
        program=25,
        steps=(
            MidiStep((45,), 64, velocity=92, gap_ms=16),
            MidiStep((52,), 72, velocity=98, gap_ms=16),
            MidiStep((57,), 82, velocity=104, gap_ms=16),
            MidiStep((61, 64), 148, velocity=110, gap_ms=26),
        ),
        volume=118,
        expression=126,
    ),
    "voice.context_added": MidiCue(
        program=26,
        steps=(
            MidiStep((57,), 42, velocity=82, gap_ms=14),
            MidiStep((61,), 48, velocity=88, gap_ms=14),
            MidiStep((64,), 56, velocity=94, gap_ms=14),
            MidiStep((69,), 104, velocity=100, gap_ms=22),
        ),
        volume=112,
        expression=124,
    ),
    "voice.record_stop": MidiCue(
        program=27,
        steps=(
            MidiStep((64,), 48, velocity=82, gap_ms=16),
            MidiStep((57,), 54, velocity=86, gap_ms=16),
            MidiStep((52,), 62, velocity=84, gap_ms=16),
            MidiStep((45,), 132, velocity=90, gap_ms=24),
        ),
        volume=114,
        expression=124,
    ),
    "voice.processing_start": MidiCue(
        program=26,
        steps=(
            MidiStep((45,), 38, velocity=84, gap_ms=14),
            MidiStep((50,), 42, velocity=90, gap_ms=14),
            MidiStep((54,), 46, velocity=96, gap_ms=14),
            MidiStep((57,), 54, velocity=102, gap_ms=14),
            MidiStep((62,), 94, velocity=108, gap_ms=24),
        ),
        volume=114,
        expression=125,
    ),
    "voice.done": MidiCue(
        program=25,
        steps=(
            MidiStep((45,), 62, velocity=90, gap_ms=18),
            MidiStep((52,), 76, velocity=96, gap_ms=18),
            MidiStep((57,), 88, velocity=102, gap_ms=18),
            MidiStep((61,), 102, velocity=106, gap_ms=18),
            MidiStep((64, 69), 180, velocity=112, gap_ms=30),
        ),
        volume=120,
        expression=127,
    ),
    "voice.cancel_requested": MidiCue(
        program=28,
        steps=(
            MidiStep((59,), 34, velocity=84, gap_ms=14),
            MidiStep((57,), 40, velocity=82, gap_ms=14),
            MidiStep((54,), 50, velocity=86, gap_ms=14),
            MidiStep((50,), 96, velocity=92, gap_ms=22),
        ),
        volume=112,
        expression=122,
    ),
    "voice.cancelled": MidiCue(
        program=28,
        steps=(
            MidiStep((57,), 34, velocity=82, gap_ms=14),
            MidiStep((54,), 42, velocity=84, gap_ms=14),
            MidiStep((50,), 54, velocity=88, gap_ms=14),
            MidiStep((45,), 108, velocity=94, gap_ms=24),
        ),
        volume=114,
        expression=123,
    ),
    "voice.warn": MidiCue(
        program=None,
        channel=9,
        steps=(
            MidiStep((37,), 28, velocity=116, gap_ms=18, channel=9),
            MidiStep((37,), 28, velocity=110, gap_ms=24, channel=9),
        ),
        volume=122,
        expression=127,
    ),
    # Mode-switch chords — each mode gets a unique short guitar voicing
    # so the user can identify which mode they landed on by ear alone.
    "mode.obs": MidiCue(
        program=25,  # steel guitar
        steps=(
            MidiStep((40, 47, 52, 55), 180, velocity=100, gap_ms=30),
        ),
        volume=108,
        expression=120,
    ),
    "mode.voice_scribe": MidiCue(
        program=25,
        steps=(
            MidiStep((45, 52, 57, 61), 180, velocity=98, gap_ms=30),
        ),
        volume=108,
        expression=120,
    ),
    "mode.sound_pads": MidiCue(
        program=27,  # clean guitar
        steps=(
            MidiStep((50, 57, 62, 66), 180, velocity=96, gap_ms=30),
        ),
        volume=108,
        expression=120,
    ),
    "mode.voicemeeter": MidiCue(
        program=25,
        steps=(
            MidiStep((43, 50, 55, 59), 180, velocity=100, gap_ms=30),
        ),
        volume=108,
        expression=120,
    ),
    "mode.obs_session": MidiCue(
        program=27,
        steps=(
            MidiStep((48, 55, 60, 64), 180, velocity=98, gap_ms=30),
        ),
        volume=108,
        expression=120,
    ),
    "mode.spotify": MidiCue(
        program=25,
        steps=(
            MidiStep((41, 48, 53, 57), 180, velocity=102, gap_ms=30),
        ),
        volume=110,
        expression=122,
    ),
    "voice.error": MidiCue(
        program=None,
        channel=9,
        steps=(
            MidiStep((36,), 36, velocity=124, gap_ms=14, channel=9),
            MidiStep((38,), 42, velocity=126, gap_ms=14, channel=9),
            MidiStep((49,), 80, velocity=122, gap_ms=24, channel=9),
        ),
        volume=124,
        expression=127,
    ),
}


class MidiCuePlayer:
    """Serial MIDI-out helper for short notification phrases."""

    def __init__(self, device_name: str, log_fn=None):
        self.device_name = device_name.lower()
        self._log = log_fn or (lambda *args, **kwargs: None)
        self._lock = threading.Lock()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._port = None
        self._port_name: str | None = None
        self._warned_missing = False
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def _find_port_name(self) -> str | None:
        try:
            for name in mido.get_output_names():
                if self.device_name in name.lower():
                    return name
        except Exception as exc:
            self._log("FEEDBACK", f"MIDI output enumeration failed: {exc}", color=(255, 80, 80))
        return None

    def _ensure_port(self):
        if self._port is not None:
            return self._port
        port_name = self._find_port_name()
        if not port_name:
            if not self._warned_missing:
                self._log("FEEDBACK", "MIDI cue output not available", color=(255, 180, 80))
                self._warned_missing = True
            return None
        try:
            self._port = mido.open_output(port_name)
            self._port_name = port_name
            self._warned_missing = False
            self._log("FEEDBACK", f"MIDI cue output connected: {port_name}", color=(100, 255, 150))
            return self._port
        except Exception as exc:
            self._log("FEEDBACK", f"Failed to open MIDI output '{port_name}': {exc}", color=(255, 80, 80))
            return None

    def play(self, cue_id: str) -> bool:
        if cue_id not in MIDI_CUES:
            return False
        self._queue.put(cue_id)
        return True

    def send_messages(self, *messages: mido.Message) -> bool:
        if not messages:
            return False
        with self._lock:
            port = self._ensure_port()
            if port is None:
                return False
            try:
                for message in messages:
                    port.send(message)
                return True
            except Exception as exc:
                self._log("FEEDBACK", f"Direct MIDI send failed: {exc}", color=(255, 80, 80))
                self._port = None
                return False

    def _run(self) -> None:
        while True:
            cue_id = self._queue.get()
            if cue_id is None:
                return
            try:
                self._play_cue(cue_id)
            finally:
                self._queue.task_done()

    def _send_level_controls(self, cue: MidiCue) -> None:
        if self._port is None:
            return
        try:
            self._port.send(mido.Message("control_change", channel=cue.channel, control=7, value=cue.volume))
            self._port.send(mido.Message("control_change", channel=cue.channel, control=11, value=cue.expression))
        except Exception:
            self._port = None

    def _play_cue(self, cue_id: str) -> None:
        cue = MIDI_CUES.get(cue_id)
        if cue is None:
            return
        with self._lock:
            port = self._ensure_port()
            if port is None:
                return
            try:
                if cue.program is not None and cue.channel != 9:
                    port.send(mido.Message("program_change", channel=cue.channel, program=cue.program))
                self._send_level_controls(cue)
                for step in cue.steps:
                    channel = step.channel
                    for note in step.notes:
                        port.send(mido.Message("note_on", channel=channel, note=note, velocity=step.velocity))
                    time.sleep(step.duration_ms / 1000.0)
                    for note in step.notes:
                        port.send(mido.Message("note_off", channel=channel, note=note, velocity=0))
                    if step.gap_ms:
                        time.sleep(step.gap_ms / 1000.0)
            except Exception as exc:
                self._log("FEEDBACK", f"MIDI cue playback failed: {exc}", color=(255, 80, 80))
                self._port = None
            finally:
                self.all_notes_off()

    def all_notes_off(self, channels: tuple[int, ...] = (0, 9)) -> None:
        if self._port is None:
            return
        try:
            for channel in channels:
                self._port.send(mido.Message("control_change", channel=channel, control=123, value=0))
        except Exception:
            self._port = None

    def close(self) -> None:
        self._queue.put(None)
        self.all_notes_off()
        if self._port is not None:
            try:
                self._port.close()
            except Exception:
                pass
            self._port = None


class FeedbackService:
    """Single entry point for user-facing MIDI cues."""

    def __init__(self, device_name: str, log_fn=None):
        self._midi = MidiCuePlayer(device_name=device_name, log_fn=log_fn)

    def emit(self, cue_id: str) -> bool:
        return self._midi.play(cue_id)

    def emit_action(self, cue_id: str = "action.default") -> bool:
        return self.emit(cue_id)

    def emit_error(self) -> bool:
        return self.emit("action.error")

    def send_midi(self, *messages: mido.Message) -> bool:
        return self._midi.send_messages(*messages)

    def all_notes_off(self, *channels: int) -> None:
        if channels:
            self._midi.all_notes_off(tuple(channels))
            return
        self._midi.all_notes_off()

    def close(self) -> None:
        self._midi.close()
