"""Pan effect — stereo panning (stereo in/out, in-place)."""

import threading

import numpy as np


class Pan:
    """Stereo panner using constant-power panning law.

    Collapses incoming stereo to mono (``mean(L, R)``) then repans to the
    configured position. Modifies the ``(N, 2)`` buffer in place.

    Parameters (normalized 0.0–1.0):
        pan — 0.0 = hard left, 0.5 = center, 1.0 = hard right
    """

    def __init__(self, sample_rate: int = 44100):
        self.sample_rate = sample_rate
        self._pan: float = 0.5
        self._param_lock = threading.Lock()

    def process(self, buf: np.ndarray) -> None:
        pan = self._pan
        # At center with stereo input, pass through unchanged.
        if pan == 0.5:
            return
        angle = pan * (np.pi / 2.0)
        gain_l = np.float32(np.cos(angle))
        gain_r = np.float32(np.sin(angle))

        # Collapse to mono without extra allocations beyond the single mono buf.
        mono = buf[:, 0] + buf[:, 1]
        mono *= np.float32(0.5)
        np.multiply(mono, gain_l, out=buf[:, 0])
        np.multiply(mono, gain_r, out=buf[:, 1])

    def set_param(self, name: str, value: float) -> None:
        if name == "pan":
            with self._param_lock:
                self._pan = float(np.clip(value, 0.0, 1.0))

    def get_params(self) -> dict[str, float]:
        return {"pan": self._pan}
