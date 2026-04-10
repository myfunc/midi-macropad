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


_transpose: int = 0


def set_transpose(semitones: int) -> None:
    global _transpose
    _transpose = max(-24, min(24, semitones))


def get_transpose() -> int:
    return _transpose


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
    # ── Mode mini-melodies (all in C major, transposable) ──────────────────
    # Each ~1.5-2s, unique rhythm so you recognise the mode by ear.
    # Transpose via set_transpose() shifts all mode.* cues equally.

    # Spotify — bouncy ascending arpeggio, steel guitar
    "mode.spotify": MidiCue(
        program=25,
        steps=(
            MidiStep((48,), 80, velocity=88, gap_ms=40),    # C3
            MidiStep((52,), 80, velocity=92, gap_ms=40),    # E3
            MidiStep((55,), 80, velocity=96, gap_ms=40),    # G3
            MidiStep((60,), 100, velocity=100, gap_ms=50),  # C4
            MidiStep((64,), 80, velocity=96, gap_ms=40),    # E4
            MidiStep((60, 67), 280, velocity=104, gap_ms=0),  # C4+G4 ring
        ),
        volume=112,
        expression=124,
    ),

    # Voicemeeter — descending then resolve up, clean guitar
    "mode.voicemeeter": MidiCue(
        program=27,
        steps=(
            MidiStep((71,), 90, velocity=90, gap_ms=30),    # B4
            MidiStep((67,), 90, velocity=88, gap_ms=30),    # G4
            MidiStep((64,), 90, velocity=86, gap_ms=30),    # E4
            MidiStep((60,), 110, velocity=84, gap_ms=50),   # C4
            MidiStep((59,), 80, velocity=88, gap_ms=30),    # B3
            MidiStep((60, 67), 300, velocity=96, gap_ms=0),   # C4+G4 resolve
        ),
        volume=110,
        expression=122,
    ),

    # Voice Scribe — gentle rising phrase, jazz guitar
    "mode.voice_scribe": MidiCue(
        program=26,
        steps=(
            MidiStep((57,), 100, velocity=82, gap_ms=40),   # A3
            MidiStep((60,), 80, velocity=86, gap_ms=35),    # C4
            MidiStep((64,), 80, velocity=90, gap_ms=35),    # E4
            MidiStep((67,), 100, velocity=94, gap_ms=45),   # G4
            MidiStep((69,), 120, velocity=98, gap_ms=50),   # A4
            MidiStep((72, 76), 320, velocity=102, gap_ms=0),  # C5+E5 bloom
        ),
        volume=108,
        expression=122,
    ),

    # OBS — punchy power stab + harmonic ring, overdriven
    "mode.obs": MidiCue(
        program=29,
        steps=(
            MidiStep((36, 43, 48), 60, velocity=110, gap_ms=50),  # C2+G2+C3
            MidiStep((36, 43, 48), 60, velocity=114, gap_ms=80),  # repeat hit
            MidiStep((48, 55, 60), 400, velocity=108, gap_ms=0),  # C3+G3+C4 ring
        ),
        volume=116,
        expression=126,
    ),

    # Sound Pads — funky staccato pattern, muted guitar
    "mode.sound_pads": MidiCue(
        program=28,
        steps=(
            MidiStep((48,), 40, velocity=100, gap_ms=40),   # C3
            MidiStep((48,), 40, velocity=90, gap_ms=30),
            MidiStep((52,), 40, velocity=102, gap_ms=40),   # E3
            MidiStep((55,), 40, velocity=96, gap_ms=30),    # G3
            MidiStep((55,), 40, velocity=88, gap_ms=40),
            MidiStep((60,), 40, velocity=104, gap_ms=30),   # C4
            MidiStep((62, 64), 250, velocity=108, gap_ms=0),  # D4+E4 pop
        ),
        volume=114,
        expression=125,
    ),

    # Session — steel guitar, low-mid; mirrors voice record_start/stop energy
    "session.start": MidiCue(
        program=25,
        steps=(
            MidiStep((48,), 70, velocity=90, gap_ms=16),
            MidiStep((52,), 72, velocity=94, gap_ms=16),
            MidiStep((55,), 74, velocity=98, gap_ms=16),
            MidiStep((57, 60), 142, velocity=104, gap_ms=24),
        ),
        volume=118,
        expression=126,
    ),
    "session.stop": MidiCue(
        program=25,
        steps=(
            MidiStep((60,), 68, velocity=88, gap_ms=16),
            MidiStep((55,), 70, velocity=86, gap_ms=16),
            MidiStep((52,), 72, velocity=84, gap_ms=16),
            MidiStep((48,), 132, velocity=90, gap_ms=24),
        ),
        volume=114,
        expression=124,
    ),
    "session.segment_start": MidiCue(
        program=25,
        steps=(
            MidiStep((50,), 58, velocity=92, gap_ms=14),
            MidiStep((57,), 78, velocity=100, gap_ms=18),
        ),
        volume=116,
        expression=125,
    ),
    "session.segment_stop": MidiCue(
        program=25,
        steps=(
            MidiStep((48,), 54, velocity=94, gap_ms=12),
            MidiStep((55,), 68, velocity=100, gap_ms=20),
        ),
        volume=116,
        expression=125,
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

    _RECONNECT_DELAYS = (0.1, 0.3, 1.0, 3.0)

    def __init__(self, device_name: str, log_fn=None):
        self.device_name = device_name.lower()
        self._log = log_fn or (lambda *args, **kwargs: None)
        self._lock = threading.Lock()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._port = None
        self._port_name: str | None = None
        self._warned_missing = False
        self._consecutive_failures = 0
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
            if self._consecutive_failures > 0:
                self._log("FEEDBACK", f"MIDI cue output reconnected: {port_name}", color=(100, 255, 150))
            else:
                self._log("FEEDBACK", f"MIDI cue output connected: {port_name}", color=(100, 255, 150))
            self._consecutive_failures = 0
            return self._port
        except Exception as exc:
            self._log("FEEDBACK", f"Failed to open MIDI output '{port_name}': {exc}", color=(255, 80, 80))
            return None

    def _reconnect_with_retry(self) -> bool:
        """Try to reopen the MIDI output port with exponential backoff."""
        for delay in self._RECONNECT_DELAYS:
            time.sleep(delay)
            port = self._ensure_port()
            if port is not None:
                return True
        return False

    def play(self, cue_id: str) -> bool:
        if cue_id not in MIDI_CUES:
            return False
        self._queue.put(cue_id)
        return True

    def send_messages(self, *messages: mido.Message) -> bool:
        if not messages:
            return False
        need_reconnect = False
        with self._lock:
            port = self._ensure_port()
            if port is None:
                return False
            try:
                for message in messages:
                    port.send(message)
                self._consecutive_failures = 0
                return True
            except Exception as exc:
                self._consecutive_failures += 1
                self._log("FEEDBACK", f"Direct MIDI send failed: {exc}", color=(255, 80, 80))
                try:
                    self._port.close()
                except Exception:
                    pass
                self._port = None
                need_reconnect = True
        if need_reconnect:
            self._reconnect_with_retry()
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

    def _play_cue(self, cue_id: str, _retry: bool = False) -> None:
        cue = MIDI_CUES.get(cue_id)
        if cue is None:
            return
        shift = _transpose if cue_id.startswith("mode.") else 0
        need_reconnect = False
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
                    notes = [max(0, min(127, n + shift)) for n in step.notes]
                    for note in notes:
                        port.send(mido.Message("note_on", channel=channel, note=note, velocity=step.velocity))
                    time.sleep(step.duration_ms / 1000.0)
                    for note in notes:
                        port.send(mido.Message("note_off", channel=channel, note=note, velocity=0))
                    if step.gap_ms:
                        time.sleep(step.gap_ms / 1000.0)
                self._consecutive_failures = 0
            except Exception as exc:
                self._consecutive_failures += 1
                self._log(
                    "FEEDBACK",
                    f"MIDI cue playback failed (attempt {self._consecutive_failures}): {exc}",
                    color=(255, 80, 80),
                )
                try:
                    self._port.close()
                except Exception:
                    pass
                self._port = None
                need_reconnect = True
            finally:
                self.all_notes_off()

        if need_reconnect and not _retry:
            if self._reconnect_with_retry():
                self._play_cue(cue_id, _retry=True)

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


class AudioCuePlayer:
    """Synthesize cues as audio and play through system output device."""

    _SAMPLE_RATE = 44100

    def __init__(self, log_fn=None):
        self._log = log_fn or (lambda *args, **kwargs: None)
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    @staticmethod
    def _midi_to_freq(note: int) -> float:
        return 440.0 * (2.0 ** ((note - 69) / 12.0))

    def _synth_step(self, step: MidiStep, shift: int = 0) -> "numpy.ndarray":
        import numpy as np
        sr = self._SAMPLE_RATE
        dur = step.duration_ms / 1000.0
        t = np.linspace(0, dur, int(sr * dur), endpoint=False, dtype=np.float32)
        signal = np.zeros_like(t)
        amp = (step.velocity / 127.0) * 0.35
        for note in step.notes:
            freq = self._midi_to_freq(max(0, min(127, note + shift)))
            # Sine + soft overtone for warmth
            signal += amp * np.sin(2 * np.pi * freq * t)
            signal += amp * 0.2 * np.sin(2 * np.pi * freq * 2 * t)
        # Envelope: fast attack, smooth release
        env_len = min(int(sr * 0.01), len(t))
        rel_len = min(int(sr * 0.05), len(t))
        envelope = np.ones_like(t)
        envelope[:env_len] = np.linspace(0, 1, env_len)
        envelope[-rel_len:] = np.linspace(1, 0, rel_len)
        signal *= envelope
        return np.clip(signal, -1.0, 1.0)

    def play(self, cue_id: str) -> bool:
        if cue_id not in MIDI_CUES:
            return False
        self._queue.put(cue_id)
        return True

    def _run(self) -> None:
        while True:
            cue_id = self._queue.get()
            if cue_id is None:
                return
            try:
                self._play_cue(cue_id)
            except Exception as exc:
                self._log("AUDIO_FB", f"Audio cue failed: {exc}", color=(255, 80, 80))
            finally:
                self._queue.task_done()

    def _play_cue(self, cue_id: str) -> None:
        import numpy as np
        import sounddevice as sd

        cue = MIDI_CUES.get(cue_id)
        if cue is None:
            return
        shift = _transpose if cue_id.startswith("mode.") else 0

        # Build full waveform
        segments = []
        for step in cue.steps:
            segments.append(self._synth_step(step, shift))
            if step.gap_ms > 0:
                gap_samples = int(self._SAMPLE_RATE * step.gap_ms / 1000.0)
                segments.append(np.zeros(gap_samples, dtype=np.float32))

        audio = np.concatenate(segments)
        vol = cue.volume / 127.0
        audio *= vol

        sd.play(audio, self._SAMPLE_RATE, blocking=True)

    def close(self) -> None:
        self._queue.put(None)


# Feedback modes
FEEDBACK_MODE_MIDI = "midi"
FEEDBACK_MODE_AUDIO = "audio"
FEEDBACK_MODE_BOTH = "both"
FEEDBACK_MODE_OFF = "off"


class FeedbackService:
    """Single entry point for user-facing feedback cues.

    Supports two output backends:
      - MIDI: sends to MPK Mini Play's GM synth
      - Audio: synthesizes and plays through system audio output
    """

    def __init__(self, device_name: str, log_fn=None, mode: str = FEEDBACK_MODE_MIDI):
        self._log = log_fn or (lambda *args, **kwargs: None)
        self._midi = MidiCuePlayer(device_name=device_name, log_fn=log_fn)
        self._audio = AudioCuePlayer(log_fn=log_fn)
        self._mode = mode

    @property
    def mode(self) -> str:
        return self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        if value in (FEEDBACK_MODE_MIDI, FEEDBACK_MODE_AUDIO, FEEDBACK_MODE_BOTH, FEEDBACK_MODE_OFF):
            self._mode = value

    def emit(self, cue_id: str) -> bool:
        if self._mode == FEEDBACK_MODE_OFF:
            return False
        sent = False
        if self._mode in (FEEDBACK_MODE_MIDI, FEEDBACK_MODE_BOTH):
            sent = self._midi.play(cue_id) or sent
        if self._mode in (FEEDBACK_MODE_AUDIO, FEEDBACK_MODE_BOTH):
            sent = self._audio.play(cue_id) or sent
        return sent

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
        self._audio.close()
