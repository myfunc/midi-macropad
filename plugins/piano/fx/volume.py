"""Volume effect — simple gain control (stereo, in-place)."""

import threading

import numpy as np


class Volume:
    """Adjustable gain from 0.0 (silence) to 1.0 (unity).

    Operates on a stereo ``(N, 2)`` float32 buffer in place.
    """

    def __init__(self, sample_rate: int = 44100):
        self.sample_rate = sample_rate
        self._gain: float = 1.0
        self._param_lock = threading.Lock()

    def process(self, buf: np.ndarray) -> None:
        """Apply gain to *buf* in place. Buffer must be ``(N, 2)`` float32."""
        gain = self._gain
        if gain == 1.0:
            return
        np.multiply(buf, gain, out=buf)

    def set_param(self, name: str, value: float) -> None:
        """Set parameter. *value* is normalized 0.0–1.0."""
        if name == "gain":
            with self._param_lock:
                self._gain = float(np.clip(value, 0.0, 1.0))

    def get_params(self) -> dict[str, float]:
        return {"gain": self._gain}
