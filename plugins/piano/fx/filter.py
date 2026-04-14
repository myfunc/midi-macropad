"""Filter effect — biquad lowpass / highpass (stereo, in-place)."""

import threading

import numpy as np

try:
    from scipy.signal import lfilter as _lfilter
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


class Filter:
    """Biquad filter (LP or HP) with independent state per channel.

    Parameters (normalized 0.0–1.0):
        cutoff — cutoff frequency (exponential 80 Hz–16 kHz)
        resonance — Q factor (0.5–8.0)
        mode — 0 = LP, 1 = HP (threshold at 0.5)
    """

    def __init__(self, sample_rate: int = 44100):
        self.sample_rate = sample_rate
        self._cutoff: float = 1.0
        self._resonance: float = 0.0
        self._mode: float = 0.0

        # Per-channel filter state (shape: (2, 2) — [channel, zi])
        self._zi_l: np.ndarray = np.zeros(2, dtype=np.float64)
        self._zi_r: np.ndarray = np.zeros(2, dtype=np.float64)

        self._b0: float = 1.0
        self._b1: float = 0.0
        self._b2: float = 0.0
        self._a1: float = 0.0
        self._a2: float = 0.0
        self._bypass: bool = True

        self._param_lock = threading.Lock()
        self._update_coefficients()

    def _update_coefficients(self) -> None:
        freq = 80.0 * (200.0 ** self._cutoff)
        freq = min(freq, self.sample_rate * 0.45)
        q = 0.5 + self._resonance * 7.5

        omega = 2.0 * np.pi * freq / self.sample_rate
        sin_w = np.sin(omega)
        cos_w = np.cos(omega)
        alpha = sin_w / (2.0 * q)
        is_hp = self._mode >= 0.5

        if is_hp:
            b0 = (1.0 + cos_w) / 2.0
            b1 = -(1.0 + cos_w)
            b2 = (1.0 + cos_w) / 2.0
        else:
            b0 = (1.0 - cos_w) / 2.0
            b1 = 1.0 - cos_w
            b2 = (1.0 - cos_w) / 2.0

        a0 = 1.0 + alpha
        self._b0 = b0 / a0
        self._b1 = b1 / a0
        self._b2 = b2 / a0
        self._a1 = (-2.0 * cos_w) / a0
        self._a2 = (1.0 - alpha) / a0

        # Bypass when effectively wide-open LP with no resonance.
        self._bypass = (
            self._mode < 0.5
            and self._cutoff >= 0.999
            and self._resonance < 0.005
        )

    def process(self, buf: np.ndarray) -> None:
        if self._bypass:
            return
        b = np.array([self._b0, self._b1, self._b2], dtype=np.float64)
        a = np.array([1.0, self._a1, self._a2], dtype=np.float64)

        if _HAS_SCIPY:
            xl = buf[:, 0].astype(np.float64)
            xr = buf[:, 1].astype(np.float64)
            yl, self._zi_l = _lfilter(b, a, xl, zi=self._zi_l)
            yr, self._zi_r = _lfilter(b, a, xr, zi=self._zi_r)
            buf[:, 0] = yl.astype(np.float32)
            buf[:, 1] = yr.astype(np.float32)
        else:
            self._process_manual(buf, 0, self._zi_l)
            self._process_manual(buf, 1, self._zi_r)

    def _process_manual(self, buf: np.ndarray, ch: int, zi: np.ndarray) -> None:
        b0, b1, b2 = self._b0, self._b1, self._b2
        a1, a2 = self._a1, self._a2
        z0, z1 = zi[0], zi[1]
        col = buf[:, ch]
        for i in range(col.shape[0]):
            xi = float(col[i])
            yi = b0 * xi + z0
            z0 = b1 * xi - a1 * yi + z1
            z1 = b2 * xi - a2 * yi
            col[i] = yi
        zi[0] = z0
        zi[1] = z1

    def set_param(self, name: str, value: float) -> None:
        with self._param_lock:
            if name == "cutoff":
                self._cutoff = float(np.clip(value, 0.0, 1.0))
            elif name == "resonance":
                self._resonance = float(np.clip(value, 0.0, 1.0))
            elif name == "mode":
                self._mode = float(np.clip(value, 0.0, 1.0))
            else:
                return
            self._update_coefficients()

    def get_params(self) -> dict[str, float]:
        return {
            "cutoff": self._cutoff,
            "resonance": self._resonance,
            "mode": self._mode,
        }
