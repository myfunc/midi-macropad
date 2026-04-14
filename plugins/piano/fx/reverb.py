"""Reverb effect — Schroeder reverb with per-channel state (stereo, in-place)."""

import threading

import numpy as np


class Reverb:
    """Schroeder reverb with independent L/R processing.

    Parameters (all normalized 0.0–1.0):
        mix   — dry/wet ratio
        decay — feedback amount (mapped 0.3–0.95)
    """

    _COMB_DELAYS_44100 = [1557, 1617, 1491, 1422]
    _ALLPASS_DELAYS_44100 = [225, 556]
    _ALLPASS_GAIN = 0.5

    def __init__(self, sample_rate: int = 44100):
        self.sample_rate = sample_rate
        self._mix: float = 0.3
        self._decay: float = 0.5
        self._param_lock = threading.Lock()

        ratio = sample_rate / 44100.0
        self._comb_delays = [int(d * ratio) for d in self._COMB_DELAYS_44100]
        # Slightly offset right channel delays for stereo width.
        self._comb_delays_r = [int(d * ratio * 1.03) + 1 for d in self._COMB_DELAYS_44100]
        self._allpass_delays = [int(d * ratio) for d in self._ALLPASS_DELAYS_44100]

        # Per-channel state
        self._comb_bufs_l: list[np.ndarray] = []
        self._comb_idx_l: list[int] = []
        self._comb_bufs_r: list[np.ndarray] = []
        self._comb_idx_r: list[int] = []
        self._ap_bufs_l: list[np.ndarray] = []
        self._ap_idx_l: list[int] = []
        self._ap_bufs_r: list[np.ndarray] = []
        self._ap_idx_r: list[int] = []
        self._init_buffers()

        # Per-block scratch buffer (grows as needed)
        self._wet_scratch: np.ndarray = np.zeros(0, dtype=np.float32)
        self._out_scratch: np.ndarray = np.zeros(0, dtype=np.float32)

    def _init_buffers(self) -> None:
        self._comb_bufs_l = [np.zeros(d, dtype=np.float32) for d in self._comb_delays]
        self._comb_idx_l = [0] * len(self._comb_delays)
        self._comb_bufs_r = [np.zeros(d, dtype=np.float32) for d in self._comb_delays_r]
        self._comb_idx_r = [0] * len(self._comb_delays_r)
        self._ap_bufs_l = [np.zeros(d, dtype=np.float32) for d in self._allpass_delays]
        self._ap_idx_l = [0] * len(self._allpass_delays)
        self._ap_bufs_r = [np.zeros(d, dtype=np.float32) for d in self._allpass_delays]
        self._ap_idx_r = [0] * len(self._allpass_delays)

    @staticmethod
    def _process_comb(
        mono: np.ndarray, out: np.ndarray, buf: np.ndarray, idx: int, delay: int, feedback: float
    ) -> int:
        n = len(mono)
        pos = 0
        while pos < n:
            chunk = min(n - pos, delay - idx)
            out[pos:pos + chunk] = buf[idx:idx + chunk]
            buf[idx:idx + chunk] = mono[pos:pos + chunk] + out[pos:pos + chunk] * feedback
            idx = (idx + chunk) % delay
            pos += chunk
        return idx

    @staticmethod
    def _process_allpass_inplace(
        signal: np.ndarray, buf: np.ndarray, idx: int, delay: int, g: float
    ) -> int:
        n = len(signal)
        pos = 0
        while pos < n:
            chunk = min(n - pos, delay - idx)
            buf_slice = buf[idx:idx + chunk].copy()
            inp_slice = signal[pos:pos + chunk].copy()
            signal[pos:pos + chunk] = buf_slice - inp_slice * g
            buf[idx:idx + chunk] = inp_slice + buf_slice * g
            idx = (idx + chunk) % delay
            pos += chunk
        return idx

    def _ensure_scratch(self, n: int) -> None:
        if self._wet_scratch.shape[0] < n:
            self._wet_scratch = np.zeros(n, dtype=np.float32)
            self._out_scratch = np.zeros(n, dtype=np.float32)

    def _process_channel(
        self,
        col: np.ndarray,
        comb_bufs: list[np.ndarray],
        comb_idx: list[int],
        comb_delays: list[int],
        ap_bufs: list[np.ndarray],
        ap_idx: list[int],
        feedback: float,
    ) -> None:
        n = col.shape[0]
        self._ensure_scratch(n)
        wet = self._wet_scratch[:n]
        out = self._out_scratch[:n]
        wet[:] = 0.0

        for i, delay in enumerate(comb_delays):
            comb_idx[i] = self._process_comb(
                col, out, comb_bufs[i], comb_idx[i], delay, feedback
            )
            wet += out

        wet *= 1.0 / len(comb_delays)

        for i, delay in enumerate(self._allpass_delays):
            ap_idx[i] = self._process_allpass_inplace(
                wet, ap_bufs[i], ap_idx[i], delay, self._ALLPASS_GAIN
            )

        dry = 1.0 - self._mix
        np.multiply(col, dry, out=col)
        col += wet * self._mix

    def process(self, buf: np.ndarray) -> None:
        if self._mix == 0.0:
            return
        feedback = 0.3 + self._decay * 0.65

        self._process_channel(
            buf[:, 0], self._comb_bufs_l, self._comb_idx_l, self._comb_delays,
            self._ap_bufs_l, self._ap_idx_l, feedback,
        )
        self._process_channel(
            buf[:, 1], self._comb_bufs_r, self._comb_idx_r, self._comb_delays_r,
            self._ap_bufs_r, self._ap_idx_r, feedback,
        )

    def set_param(self, name: str, value: float) -> None:
        with self._param_lock:
            if name == "mix":
                self._mix = float(np.clip(value, 0.0, 1.0))
            elif name == "decay":
                self._decay = float(np.clip(value, 0.0, 1.0))

    def get_params(self) -> dict[str, float]:
        return {"mix": self._mix, "decay": self._decay}
