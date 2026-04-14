"""Delay effect — feedback delay line with per-channel state (stereo, in-place)."""

import threading

import numpy as np


class Delay:
    """Feedback delay per channel.

    Parameters (normalized 0.0–1.0):
        time     — delay time (50–1000 ms)
        feedback — feedback amount (0.0–0.85)
        mix      — dry/wet ratio
    """

    def __init__(self, sample_rate: int = 44100):
        self.sample_rate = sample_rate
        self._time: float = 0.3
        self._feedback: float = 0.3
        self._mix: float = 0.3
        self._param_lock = threading.Lock()

        max_delay_samples = int(sample_rate * 1.0)
        self._buf_l = np.zeros(max_delay_samples, dtype=np.float32)
        self._buf_r = np.zeros(max_delay_samples, dtype=np.float32)
        self._w_l: int = 0
        self._w_r: int = 0

        self._out_scratch: np.ndarray = np.zeros(0, dtype=np.float32)

    def _ensure_scratch(self, n: int) -> None:
        if self._out_scratch.shape[0] < n:
            self._out_scratch = np.zeros(n, dtype=np.float32)

    def _process_channel(
        self, col: np.ndarray, buf: np.ndarray, w: int, delay_samples: int, feedback: float
    ) -> int:
        n = col.shape[0]
        self._ensure_scratch(n)
        out = self._out_scratch[:n]
        buf_len = len(buf)

        pos = 0
        while pos < n:
            chunk = min(n - pos, buf_len - w)
            read_start = (w - delay_samples) % buf_len
            read_end = read_start + chunk
            if read_end <= buf_len:
                out[pos:pos + chunk] = buf[read_start:read_end]
            else:
                first = buf_len - read_start
                out[pos:pos + chunk][:first] = buf[read_start:]
                out[pos:pos + chunk][first:] = buf[:read_end - buf_len]

            buf[w:w + chunk] = col[pos:pos + chunk] + out[pos:pos + chunk] * feedback
            w = (w + chunk) % buf_len
            pos += chunk

        dry = 1.0 - self._mix
        np.multiply(col, dry, out=col)
        col += out * self._mix
        return w

    def process(self, buf: np.ndarray) -> None:
        if self._mix == 0.0:
            return
        delay_sec = 0.05 + self._time * 0.95
        delay_samples = min(int(delay_sec * self.sample_rate), len(self._buf_l))
        feedback = self._feedback * 0.85

        self._w_l = self._process_channel(buf[:, 0], self._buf_l, self._w_l, delay_samples, feedback)
        self._w_r = self._process_channel(buf[:, 1], self._buf_r, self._w_r, delay_samples, feedback)

    def set_param(self, name: str, value: float) -> None:
        with self._param_lock:
            if name == "time":
                self._time = float(np.clip(value, 0.0, 1.0))
            elif name == "feedback":
                self._feedback = float(np.clip(value, 0.0, 1.0))
            elif name == "mix":
                self._mix = float(np.clip(value, 0.0, 1.0))

    def get_params(self) -> dict[str, float]:
        return {
            "time": self._time,
            "feedback": self._feedback,
            "mix": self._mix,
        }
