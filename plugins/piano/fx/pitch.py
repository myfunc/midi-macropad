"""Pitch shift effect — resampling-based (stereo, in-place)."""

import threading

import numpy as np


class Pitch:
    """Pitch shift via resampling.

    Processes each channel of the ``(N, 2)`` stereo buffer in place.

    Parameters (normalized 0.0–1.0):
        shift — 0.5 = no shift, 0.0 = -12 semitones, 1.0 = +12 semitones
    """

    def __init__(self, sample_rate: int = 44100):
        self.sample_rate = sample_rate
        self._shift: float = 0.5
        self._param_lock = threading.Lock()

    def process(self, buf: np.ndarray) -> None:
        shift = self._shift
        if abs(shift - 0.5) < 0.005:
            return

        semitones = (shift - 0.5) * 24.0
        ratio = 2.0 ** (semitones / 12.0)

        n_in = buf.shape[0]
        n_out = int(n_in / ratio)
        if n_out < 1:
            return

        indices = np.linspace(0, n_in - 1, n_out)
        base = np.arange(n_in)

        for ch in range(buf.shape[1]):
            shifted = np.interp(indices, base, buf[:, ch]).astype(np.float32)
            if len(shifted) < n_in:
                buf[:len(shifted), ch] = shifted
                buf[len(shifted):, ch] = 0.0
            else:
                buf[:, ch] = shifted[:n_in]

    def set_param(self, name: str, value: float) -> None:
        if name == "shift":
            with self._param_lock:
                self._shift = float(np.clip(value, 0.0, 1.0))

    def get_params(self) -> dict[str, float]:
        return {"shift": self._shift}
