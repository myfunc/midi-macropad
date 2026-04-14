"""Producer-thread audio engine for the Piano plugin.

Architecture:

- A dedicated producer thread owns the voice list and FX chain. It mixes
  blocks into a ring buffer as fast as the consumer drains them.
- The sounddevice audio callback (consumer, real-time thread) does only a
  numpy copy from the ring buffer into the output buffer — no allocations,
  no locking.
- MIDI events (note_on / note_off / reconfigure / fx_param) arrive via a
  thread-safe ``queue.Queue`` and are processed between blocks by the
  producer. This eliminates the lock contention between the MIDI thread
  and the audio callback that previously caused glitches.

The engine exposes a small stable API consumed by :mod:`piano_plugin`:
``start / stop / note_on / note_off / reconfigure / set_fx_param / status
/ stop_all_voices / active_voice_count``.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

try:
    import sounddevice as sd
except Exception:  # pragma: no cover - absence handled at runtime
    sd = None  # type: ignore

from ring_buffer import RingBuffer
from fx_engine import FXChain


log = logging.getLogger(__name__)


@dataclass
class Voice:
    """Currently playing note voice."""

    note: int
    data: np.ndarray  # mono float32
    position: int = 0
    velocity_gain: float = 1.0
    releasing: bool = False
    age: int = 0  # monotonically increasing — older voices stolen first


@dataclass
class _StreamConfig:
    sample_rate: int = 44100
    block_size: int = 1024
    latency: str = "low"
    output_device: int | None = None


@dataclass
class _EngineState:
    max_voices: int = 8
    master_volume: float = 1.0


# --- command types (sent to producer via queue) ------------------------------


@dataclass
class _CmdNoteOn:
    note: int
    velocity_gain: float
    samples: list[tuple[np.ndarray, int]]  # (data, origin_id-placeholder)


@dataclass
class _CmdNoteOff:
    note: int


@dataclass
class _CmdStopAll:
    pass


@dataclass
class _CmdReconfigure:
    cfg: dict
    done: threading.Event = field(default_factory=threading.Event)
    result: dict = field(default_factory=dict)  # {"ok": bool, "error": str|None}


@dataclass
class _CmdSetFxParam:
    target: str
    value: float


@dataclass
class _CmdShutdown:
    pass


class AudioEngine:
    """Producer/consumer audio engine with WASAPI low-latency output."""

    def __init__(
        self,
        sample_rate: int = 44100,
        block_size: int = 1024,
        latency: str = "low",
        output_device: int | None = None,
        max_voices: int = 8,
        master_volume: float = 1.0,
        log_fn: Callable[..., None] | None = None,
    ):
        self._stream_cfg = _StreamConfig(
            sample_rate=sample_rate,
            block_size=block_size,
            latency=latency,
            output_device=output_device,
        )
        self._state = _EngineState(
            max_voices=max_voices,
            master_volume=master_volume,
        )

        self._log_fn = log_fn or (lambda *a, **kw: None)
        self._fx_chain = FXChain(sample_rate=sample_rate)

        self._voices: list[Voice] = []
        self._voice_age_counter: int = 0

        self._cmd_queue: queue.Queue = queue.Queue()
        self._stream: Any = None
        self._stream_error: str | None = None

        # Ring buffer holds ~4 blocks of stereo frames.
        self._ring: RingBuffer = RingBuffer(
            capacity_frames=max(block_size * 4 + 1, 4096),
            channels=2,
        )

        # Pre-allocated producer scratch buffers (stereo mix + output).
        self._mix_buf: np.ndarray = np.zeros((block_size, 2), dtype=np.float32)

        # Pre-computed fade-out envelope applied to released voices (~5 ms).
        self._fade_out_window: np.ndarray = self._make_fade_window(
            int(sample_rate * 0.005)
        )

        self._producer_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._underrun_count: int = 0
        self._produced_blocks: int = 0

    # -- static helpers -----------------------------------------------------

    @staticmethod
    def _make_fade_window(length: int) -> np.ndarray:
        length = max(length, 1)
        return np.linspace(1.0, 0.0, length, dtype=np.float32)

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> bool:
        """Start the producer thread and the audio stream. Returns True on success."""
        if self._producer_thread is not None and self._producer_thread.is_alive():
            return self._stream is not None

        self._stop_event.clear()
        self._producer_thread = threading.Thread(
            target=self._producer_loop, name="piano-audio-producer", daemon=True,
        )
        self._producer_thread.start()

        return self._open_stream()

    def stop(self) -> None:
        """Stop the audio stream and terminate the producer thread."""
        self._close_stream()
        self._stop_event.set()
        try:
            self._cmd_queue.put_nowait(_CmdShutdown())
        except Exception:
            pass
        t = self._producer_thread
        if t is not None:
            t.join(timeout=2.0)
        self._producer_thread = None
        self._voices.clear()
        self._ring.reset()

    # -- public command API (thread-safe) -----------------------------------

    def note_on(
        self,
        note: int,
        velocity: int,
        samples: list[tuple[np.ndarray, int]],
        master_volume_override: float | None = None,
    ) -> None:
        """Schedule a note_on. *samples* is a list of ``(mono_data, origin_id)``.

        The engine applies master_volume at dispatch time (snapshot) so
        subsequent reconfigures do not retroactively affect ringing notes.
        """
        master = (
            master_volume_override
            if master_volume_override is not None
            else self._state.master_volume
        )
        vg = (velocity / 127.0) * master
        self._cmd_queue.put_nowait(_CmdNoteOn(note=note, velocity_gain=vg, samples=samples))

    def note_off(self, note: int) -> None:
        self._cmd_queue.put_nowait(_CmdNoteOff(note=note))

    def stop_all_voices(self) -> None:
        self._cmd_queue.put_nowait(_CmdStopAll())

    def set_fx_param(self, target: str, value: float) -> None:
        """Thread-safe FX param setter. FX classes guard writes with their own lock."""
        self._cmd_queue.put_nowait(_CmdSetFxParam(target=target, value=value))

    def reconfigure(self, cfg: dict, timeout: float = 5.0) -> tuple[bool, str | None]:
        """Apply new stream config. Blocks until producer acks.

        Returns ``(ok, error)``.
        """
        cmd = _CmdReconfigure(cfg=dict(cfg))
        self._cmd_queue.put_nowait(cmd)
        if not cmd.done.wait(timeout=timeout):
            return False, "reconfigure timeout"
        return bool(cmd.result.get("ok", False)), cmd.result.get("error")

    # -- status -------------------------------------------------------------

    def status(self) -> dict:
        return {
            "sample_rate": self._stream_cfg.sample_rate,
            "block_size": self._stream_cfg.block_size,
            "latency": self._stream_cfg.latency,
            "output_device": self._stream_cfg.output_device,
            "max_voices": self._state.max_voices,
            "master_volume": self._state.master_volume,
            "active_voices": len(self._voices),
            "underrun_count": self._underrun_count,
            "produced_blocks": self._produced_blocks,
            "stream_error": self._stream_error,
            "fx_state": self._fx_chain.get_state(),
        }

    @property
    def active_voice_count(self) -> int:
        return len(self._voices)

    @property
    def fx_chain(self) -> FXChain:
        return self._fx_chain

    @property
    def stream_error(self) -> str | None:
        return self._stream_error

    @property
    def sample_rate(self) -> int:
        return self._stream_cfg.sample_rate

    # -- stream management (consumer side) ----------------------------------

    def _open_stream(self) -> bool:
        if sd is None:
            self._stream_error = "sounddevice not available"
            return False
        if self._stream is not None:
            return True

        cfg = self._stream_cfg
        kwargs: dict = dict(
            samplerate=cfg.sample_rate,
            channels=2,
            dtype="float32",
            callback=self._audio_callback,
            blocksize=cfg.block_size,
            latency=cfg.latency,
        )
        if cfg.output_device is not None:
            kwargs["device"] = cfg.output_device

        # Try WASAPI shared for lower latency first; fall back to default host API.
        for attempt in ("wasapi", "default"):
            try_kwargs = dict(kwargs)
            if attempt == "wasapi":
                try:
                    try_kwargs["extra_settings"] = sd.WasapiSettings(exclusive=False)
                except Exception:
                    # WASAPI not available on this platform/host — skip.
                    continue
            try:
                self._stream = sd.OutputStream(**try_kwargs)
                self._stream.start()
                self._stream_error = None
                return True
            except Exception as exc:
                self._stream = None
                self._stream_error = f"{attempt}: {exc}"
                log.warning("piano audio open failed (%s): %s", attempt, exc)

        self._log_fn(
            "PIANO",
            f"Audio output failed: {self._stream_error}",
            color=(255, 80, 80),
            level="error",
        )
        return False

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    # -- audio callback (REAL-TIME — no allocations, no locking) ------------

    def _audio_callback(self, outdata: np.ndarray, frames: int, time_info, status) -> None:  # noqa: D401
        """Copy frames from the ring buffer into *outdata*. Zero allocations.

        If fewer frames are available than requested (producer underrun),
        the remainder of outdata is silence-filled by ``read_into`` and the
        underrun counter is incremented.
        """
        n = self._ring.read_into(outdata)
        if n < frames:
            self._underrun_count += 1

    # -- producer thread ----------------------------------------------------

    def _producer_loop(self) -> None:
        """Main mixing loop. Owns all voices / FX / state mutation."""
        # Producer-owned scratch buffers, resized if block_size changes.
        while not self._stop_event.is_set():
            self._drain_commands(block_for_idle=True)
            if self._stop_event.is_set():
                break

            # Produce as many blocks as fit into the ring buffer.
            produced_any = False
            while self._ring.available_write() >= self._stream_cfg.block_size:
                # Drain non-blocking so note_on can interleave with mixing.
                self._drain_commands(block_for_idle=False)
                if self._stop_event.is_set():
                    break
                self._produce_block()
                produced_any = True

            if not produced_any:
                # Ring full — sleep briefly until callback consumes.
                time.sleep(0.001)

    def _drain_commands(self, block_for_idle: bool) -> None:
        """Process all pending commands. If *block_for_idle* and the queue is
        empty, wait briefly for the next command (avoid busy-looping when
        stream is not running)."""
        first = True
        while True:
            try:
                if first and block_for_idle and self._stream is None:
                    cmd = self._cmd_queue.get(timeout=0.05)
                else:
                    cmd = self._cmd_queue.get_nowait()
            except queue.Empty:
                return
            first = False
            self._handle_command(cmd)
            if self._stop_event.is_set():
                return

    def _handle_command(self, cmd: Any) -> None:
        if isinstance(cmd, _CmdNoteOn):
            self._apply_note_on(cmd)
        elif isinstance(cmd, _CmdNoteOff):
            self._apply_note_off(cmd)
        elif isinstance(cmd, _CmdStopAll):
            self._voices.clear()
        elif isinstance(cmd, _CmdSetFxParam):
            self._fx_chain.set_param(cmd.target, cmd.value)
        elif isinstance(cmd, _CmdReconfigure):
            try:
                ok, err = self._apply_reconfigure(cmd.cfg)
                cmd.result["ok"] = ok
                cmd.result["error"] = err
            except Exception as exc:
                cmd.result["ok"] = False
                cmd.result["error"] = str(exc)
            finally:
                cmd.done.set()
        elif isinstance(cmd, _CmdShutdown):
            self._stop_event.set()
            # Drain any remaining reconfigure commands so HTTP callers
            # blocked on ``done.wait()`` do not time out after shutdown.
            while True:
                try:
                    pending = self._cmd_queue.get_nowait()
                except queue.Empty:
                    break
                if isinstance(pending, _CmdReconfigure):
                    pending.result["ok"] = False
                    pending.result["error"] = "engine shutting down"
                    pending.done.set()

    # -- command handlers ---------------------------------------------------

    def _apply_note_on(self, cmd: _CmdNoteOn) -> None:
        # Retrigger: drop previous voices on the same note.
        if self._voices:
            self._voices = [v for v in self._voices if v.note != cmd.note]

        max_voices = self._state.max_voices
        for data, _origin in cmd.samples:
            # Voice stealing BEFORE adding — evict oldest if at capacity.
            while len(self._voices) >= max_voices:
                # Prefer releasing voices first, then the oldest.
                steal_idx = 0
                for i, v in enumerate(self._voices):
                    if v.releasing:
                        steal_idx = i
                        break
                    if v.age < self._voices[steal_idx].age:
                        steal_idx = i
                self._voices.pop(steal_idx)

            self._voice_age_counter += 1
            self._voices.append(
                Voice(
                    note=cmd.note,
                    data=data,
                    velocity_gain=cmd.velocity_gain,
                    age=self._voice_age_counter,
                )
            )

    def _apply_note_off(self, cmd: _CmdNoteOff) -> None:
        for v in self._voices:
            if v.note == cmd.note:
                v.releasing = True

    def _apply_reconfigure(self, cfg: dict) -> tuple[bool, str | None]:
        old = _StreamConfig(**self._stream_cfg.__dict__)
        old_state = _EngineState(**self._state.__dict__)

        try:
            new_sr = int(cfg.get("sample_rate", old.sample_rate))
            new_bs = int(cfg.get("block_size", old.block_size))
            new_latency = str(cfg.get("latency_mode", old.latency))
            new_device = cfg.get("output_device", old.output_device)
            new_device = int(new_device) if new_device is not None else None
            new_poly = int(cfg.get("max_polyphony", old_state.max_voices))
            new_master = float(cfg.get("master_volume", old_state.master_volume))
        except (TypeError, ValueError) as exc:
            return False, f"Invalid config: {exc}"

        if new_latency not in ("low", "medium", "high"):
            return False, "latency_mode must be low|medium|high"

        # Hot-swap state-only changes.
        self._state.max_voices = max(1, min(32, new_poly))
        self._state.master_volume = max(0.0, min(1.0, new_master))

        stream_changed = (
            new_sr != old.sample_rate
            or new_bs != old.block_size
            or new_latency != old.latency
            or new_device != old.output_device
        )
        if not stream_changed:
            return True, None

        # Snapshot FX state so user-tuned params survive the rebuild.
        fx_state = self._fx_chain.get_state()

        # Stream rebuild: close, update config, rebuild FX/scratch, reopen.
        self._close_stream()
        self._stream_cfg = _StreamConfig(
            sample_rate=new_sr,
            block_size=new_bs,
            latency=new_latency,
            output_device=new_device,
        )
        # FX chain keeps sample-rate-dependent delay line sizes — rebuild,
        # then restore params from the snapshot.
        self._fx_chain = FXChain(sample_rate=new_sr)
        self._fx_chain.apply_state(fx_state)
        self._mix_buf = np.zeros((new_bs, 2), dtype=np.float32)
        self._fade_out_window = self._make_fade_window(int(new_sr * 0.005))
        # Reset ring buffer — old stale audio must not play at new rate.
        self._ring = RingBuffer(
            capacity_frames=max(new_bs * 4 + 1, 4096),
            channels=2,
        )
        self._voices.clear()

        ok = self._open_stream()
        if not ok:
            err = self._stream_error or "stream open failed"
            # Rollback stream config — also restore the pre-rebuild FX state.
            self._stream_cfg = old
            self._fx_chain = FXChain(sample_rate=old.sample_rate)
            self._fx_chain.apply_state(fx_state)
            self._mix_buf = np.zeros((old.block_size, 2), dtype=np.float32)
            self._fade_out_window = self._make_fade_window(int(old.sample_rate * 0.005))
            self._ring = RingBuffer(
                capacity_frames=max(old.block_size * 4 + 1, 4096),
                channels=2,
            )
            self._state = old_state
            if self._open_stream():
                self._stream_error = None
            return False, err

        return True, None

    # -- block mixing (producer thread) -------------------------------------

    def _produce_block(self) -> None:
        frames = self._stream_cfg.block_size
        if self._mix_buf.shape[0] != frames:
            self._mix_buf = np.zeros((frames, 2), dtype=np.float32)

        buf = self._mix_buf
        buf.fill(0.0)

        fade = self._fade_out_window
        fade_len = fade.shape[0]

        kept: list[Voice] = []
        for voice in self._voices:
            remaining = voice.data.shape[0] - voice.position
            if remaining <= 0:
                continue

            n = frames if frames < remaining else remaining
            chunk = voice.data[voice.position:voice.position + n]

            if voice.releasing:
                fs = n if n < fade_len else fade_len
                # Apply fade and mark voice done.
                gain_fade = fade[:fs] * voice.velocity_gain
                buf[:fs, 0] += chunk[:fs] * gain_fade
                buf[:fs, 1] += chunk[:fs] * gain_fade
                voice.position = voice.data.shape[0]
            else:
                scaled = chunk * voice.velocity_gain
                buf[:n, 0] += scaled
                buf[:n, 1] += scaled
                voice.position += n

            if voice.position < voice.data.shape[0]:
                kept.append(voice)

        if len(kept) != len(self._voices):
            self._voices = kept

        # FX chain is cheap at defaults; run unconditionally for consistent
        # stereo behaviour. Pan/volume skip when at identity params anyway.
        self._fx_chain.process(buf)
        np.clip(buf, -1.0, 1.0, out=buf)

        written = self._ring.write(buf)
        if written < frames:
            # Should not happen — _produce_block only runs when space is available.
            log.debug("ring buffer partial write: %d/%d", written, frames)
        self._produced_blocks += 1
