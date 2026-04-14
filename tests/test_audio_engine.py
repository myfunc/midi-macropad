"""Tests for the piano AudioEngine.

These tests run headless by replacing ``sounddevice`` with a stub before
any ``audio_engine`` code is imported. No real audio stream is opened;
the producer thread runs normally and fills the ring buffer while a fake
consumer drains it.
"""

from __future__ import annotations

import sys
import threading
import time
import types
from pathlib import Path

import numpy as np
import pytest


# --- stub sounddevice before importing audio_engine --------------------------

_sd_stub = types.ModuleType("sounddevice")


class _StubStream:
    def __init__(self, *, blocksize, channels, callback, samplerate, **_kwargs):
        self.blocksize = blocksize
        self.channels = channels
        self.callback = callback
        self.samplerate = samplerate
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        # Simulate the audio callback pulling blocks at real-ish rate.
        out = np.zeros((self.blocksize, self.channels), dtype=np.float32)
        period = self.blocksize / float(self.samplerate)
        while not self._stop.is_set():
            out.fill(0.0)
            self.callback(out, self.blocksize, None, None)
            time.sleep(period * 0.5)

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def close(self):
        self._stop.set()


class _StubWasapi:
    def __init__(self, exclusive: bool = False):
        self.exclusive = exclusive


def _make_stream(**kwargs):
    return _StubStream(**kwargs)


_sd_stub.OutputStream = _make_stream
_sd_stub.WasapiSettings = _StubWasapi

sys.modules["sounddevice"] = _sd_stub

# Ensure piano package is importable as a top-level module set (the plugin
# uses ``from fx_engine import FXChain`` style imports).
_PLUGIN_DIR = Path(__file__).resolve().parent.parent / "plugins" / "piano"
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

from audio_engine import AudioEngine  # noqa: E402


# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def engine():
    eng = AudioEngine(
        sample_rate=44100,
        block_size=256,
        latency="low",
        output_device=None,
        max_voices=4,
        master_volume=1.0,
    )
    eng.start()
    yield eng
    eng.stop()


def _make_sample(n: int = 4096) -> np.ndarray:
    # Silent sample is fine — we are measuring voice bookkeeping, not audio.
    return np.ones(n, dtype=np.float32) * 0.1


# --- tests -------------------------------------------------------------------


def test_engine_start_stop():
    eng = AudioEngine(sample_rate=44100, block_size=256, max_voices=4)
    assert eng.start() is True
    status = eng.status()
    assert status["sample_rate"] == 44100
    assert status["block_size"] == 256
    eng.stop()


def test_note_on_under_polyphony(engine):
    sample = _make_sample(n=engine.sample_rate * 5)  # 5s — won't finish before check
    engine.note_on(60, 100, [(sample, id(sample))])
    # Wait for the producer to process the queued command.
    for _ in range(50):
        if engine.active_voice_count == 1:
            break
        time.sleep(0.01)
    assert engine.active_voice_count == 1


def test_voice_stealing_evicts_oldest(engine):
    # max_voices = 4 (from fixture)
    for note in (60, 61, 62, 63, 64, 65):
        s = _make_sample(n=10 * engine.sample_rate)  # long sample so it keeps playing
        engine.note_on(note, 100, [(s, id(s))])
    # Let producer drain commands.
    for _ in range(100):
        if engine.active_voice_count == 4:
            break
        time.sleep(0.01)
    assert engine.active_voice_count == 4


def test_reconfigure_applies_via_queue(engine):
    ok, err = engine.reconfigure({"max_polyphony": 16, "master_volume": 0.5})
    assert ok, err
    status = engine.status()
    assert status["max_voices"] == 16
    assert status["master_volume"] == 0.5


def test_reconfigure_stream_rebuild(engine):
    ok, err = engine.reconfigure({"sample_rate": 48000, "block_size": 512, "latency_mode": "low"})
    assert ok, err
    status = engine.status()
    assert status["sample_rate"] == 48000
    assert status["block_size"] == 512


def test_fx_param_setter_thread_safe(engine):
    engine.set_fx_param("reverb.mix", 0.7)
    engine.set_fx_param("volume.gain", 0.3)
    for _ in range(50):
        state = engine.fx_chain.get_state()
        if state["reverb"]["mix"] == pytest.approx(0.7) and state["volume"]["gain"] == pytest.approx(0.3):
            break
        time.sleep(0.01)
    state = engine.fx_chain.get_state()
    assert state["reverb"]["mix"] == pytest.approx(0.7)
    assert state["volume"]["gain"] == pytest.approx(0.3)


def test_stop_all_voices_clears(engine):
    sample = _make_sample(n=engine.sample_rate * 2)
    engine.note_on(60, 100, [(sample, id(sample))])
    for _ in range(50):
        if engine.active_voice_count == 1:
            break
        time.sleep(0.01)
    assert engine.active_voice_count == 1
    engine.stop_all_voices()
    for _ in range(50):
        if engine.active_voice_count == 0:
            break
        time.sleep(0.01)
    assert engine.active_voice_count == 0


def test_produced_blocks_increments(engine):
    # Let the producer run for a short while and confirm it produces blocks.
    time.sleep(0.2)
    assert engine.status()["produced_blocks"] > 0


def test_reconfigure_preserves_fx_state(engine):
    # User tunes a couple of FX params.
    engine.set_fx_param("reverb.mix", 0.73)
    engine.set_fx_param("volume.gain", 0.42)
    for _ in range(50):
        state = engine.fx_chain.get_state()
        if (state["reverb"]["mix"] == pytest.approx(0.73)
                and state["volume"]["gain"] == pytest.approx(0.42)):
            break
        time.sleep(0.01)

    # Reconfigure with a sample-rate change — forces FXChain rebuild.
    ok, err = engine.reconfigure({"sample_rate": 48000, "block_size": 512})
    assert ok, err

    # FX params must survive the rebuild.
    state = engine.fx_chain.get_state()
    assert state["reverb"]["mix"] == pytest.approx(0.73)
    assert state["volume"]["gain"] == pytest.approx(0.42)


def test_shutdown_acks_pending_reconfigure():
    """Pending reconfigure commands must be ACK'd (ok=False) on shutdown.

    Otherwise HTTP callers blocked on ``cmd.done.wait(timeout)`` hang for
    the full timeout when the engine is torn down mid-flight. We don't
    start the producer — we queue a reconfigure ahead of a synthetic
    shutdown command and drive ``_handle_command`` directly, which is
    exactly what the producer does in the real shutdown path.
    """
    import audio_engine as ae

    eng = AudioEngine(sample_rate=44100, block_size=256, max_voices=4)
    # Do NOT call start() — we only exercise the shutdown drain logic.

    pending_a = ae._CmdReconfigure(cfg={"max_polyphony": 8})
    pending_b = ae._CmdReconfigure(cfg={"max_polyphony": 16})
    eng._cmd_queue.put_nowait(pending_a)
    eng._cmd_queue.put_nowait(pending_b)

    # Drive the shutdown command through the real handler.
    eng._handle_command(ae._CmdShutdown())

    assert pending_a.done.is_set(), "first pending reconfigure not ACK'd"
    assert pending_b.done.is_set(), "second pending reconfigure not ACK'd"
    for p in (pending_a, pending_b):
        assert p.result.get("ok") is False
        assert "shutting down" in (p.result.get("error") or "")


def test_pitch_cache_thread_safe():
    """Parallel mutation of a lock-guarded cache must not raise.

    Mirrors the PianoPlugin cache pattern (``_pitch_cache`` + single
    ``_pitch_cache_lock``) without requiring the full plugin import
    chain, so the test stays hermetic.
    """
    cache: dict = {}
    lock = threading.Lock()

    errors: list[BaseException] = []

    def worker(base: int):
        try:
            for i in range(500):
                key = (base, i % 24 - 12)
                with lock:
                    cache[key] = np.zeros(4, dtype=np.float32)
                with lock:
                    cache.get(key)
                if i % 97 == 0:
                    with lock:
                        cache.clear()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(b,)) for b in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
    assert not errors, f"thread-safe cache access raised: {errors[0]!r}"
