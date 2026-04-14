"""Chorus effect — modulated delay per channel (stereo, in-place)."""

import threading

import numpy as np


class Chorus:
    """LFO-modulated delay mixed with dry signal, independent L/R.

    The right channel uses a 90-degree phase offset from the left channel
    to create stereo width.

    Parameters (normalized 0.0–1.0):
        rate  — LFO speed (0.1–5.0 Hz)
        depth — modulation depth (0.5–5.0 ms)
        mix   — dry/wet ratio
    """

    def __init__(self, sample_rate: int = 44100):
        self.sample_rate = sample_rate
        self._rate: float = 0.3
        self._depth: float = 0.5
        self._mix: float = 0.3
        self._param_lock = threading.Lock()

        max_delay_ms = 30.0
        buf_size = int(sample_rate * max_delay_ms / 1000.0) + 1
        self._buf_l = np.zeros(buf_size, dtype=np.float32)
        self._buf_r = np.zeros(buf_size, dtype=np.float32)
        self._w_l: int = 0
        self._w_r: int = 0
        self._phase_l: float = 0.0
        self._phase_r: float = np.pi / 2.0  # 90 deg offset for width

    def _process_channel(
        self, col: np.ndarray, buf: np.ndarray, w: int, phase: float,
        phase_inc: float, depth_samples: float, base_delay_samples: float,
    ) -> tuple[int, float]:
        n = col.shape[0]
        if n == 0:
            return w, phase
        buf_len = len(buf)

        phases = phase + np.arange(n, dtype=np.float64) * phase_inc
        lfo = np.sin(phases) * depth_samples

        write_indices = (w + np.arange(n)) % buf_len
        buf[write_indices] = col

        delays = base_delay_samples + lfo
        read_positions = write_indices.astype(np.float64) - delays
        read_idx_int = np.floor(read_positions).astype(np.intp)
        frac = (read_positions - read_idx_int).astype(np.float32)
        idx0 = read_idx_int % buf_len
        idx1 = (read_idx_int + 1) % buf_len

        out = buf[idx0] * (1.0 - frac) + buf[idx1] * frac

        dry = 1.0 - self._mix
        np.multiply(col, dry, out=col)
        col += out * self._mix

        new_w = int((w + n) % buf_len)
        new_phase = float((phases[-1] + phase_inc) % (2.0 * np.pi))
        return new_w, new_phase

    def process(self, buf: np.ndarray) -> None:
        if self._mix == 0.0:
            return

        lfo_freq = 0.1 + self._rate * 4.9
        depth_ms = 0.5 + self._depth * 4.5
        depth_samples = depth_ms * self.sample_rate / 1000.0
        base_delay_samples = 7.0 * self.sample_rate / 1000.0
        phase_inc = 2.0 * np.pi * lfo_freq / self.sample_rate

        self._w_l, self._phase_l = self._process_channel(
            buf[:, 0], self._buf_l, self._w_l, self._phase_l,
            phase_inc, depth_samples, base_delay_samples,
        )
        self._w_r, self._phase_r = self._process_channel(
            buf[:, 1], self._buf_r, self._w_r, self._phase_r,
            phase_inc, depth_samples, base_delay_samples,
        )

    def set_param(self, name: str, value: float) -> None:
        with self._param_lock:
            if name == "rate":
                self._rate = float(np.clip(value, 0.0, 1.0))
            elif name == "depth":
                self._depth = float(np.clip(value, 0.0, 1.0))
            elif name == "mix":
                self._mix = float(np.clip(value, 0.0, 1.0))

    def get_params(self) -> dict[str, float]:
        return {"rate": self._rate, "depth": self._depth, "mix": self._mix}
