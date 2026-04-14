"""FX chain pipeline — stereo in-place effect processing."""

import numpy as np

from fx import Volume, Reverb, Delay, Filter, Pitch, Pan, Chorus


# Порядок эффектов в цепочке по умолчанию
_DEFAULT_ORDER = ["volume", "filter", "pitch", "chorus", "delay", "reverb", "pan"]

_FX_CLASSES = {
    "volume": Volume,
    "reverb": Reverb,
    "delay": Delay,
    "filter": Filter,
    "pitch": Pitch,
    "pan": Pan,
    "chorus": Chorus,
}


class FXChain:
    """Ordered pipeline of audio effects, operating on stereo ``(N, 2)`` buffers.

    All effects process the buffer in place — callers own the allocation.
    Individual FX may mutate per-channel independently (reverb, delay,
    chorus, filter) or across channels (pan collapses to mono first).
    """

    def __init__(self, sample_rate: int = 44100):
        self.sample_rate = sample_rate
        self._effects: dict[str, object] = {}
        self._order: list[str] = list(_DEFAULT_ORDER)

        for name in self._order:
            cls = _FX_CLASSES[name]
            self._effects[name] = cls(sample_rate=sample_rate)

    def process(self, buf: np.ndarray) -> np.ndarray:
        """Run *buf* through the full FX chain in place.

        *buf* must be float32 with shape ``(N, 2)``. A 1-D mono input is
        expanded to stereo via duplication (costs one allocation — prefer
        passing stereo buffers from the caller).
        Returns the processed buffer (same object when stereo is passed in).
        """
        if buf.ndim == 1:
            stereo = np.empty((buf.shape[0], 2), dtype=np.float32)
            stereo[:, 0] = buf
            stereo[:, 1] = buf
            buf = stereo
        elif buf.ndim != 2 or buf.shape[1] != 2:
            raise ValueError("FXChain.process expects (N, 2) stereo buffer")

        for name in self._order:
            fx = self._effects.get(name)
            if fx is not None:
                fx.process(buf)
        return buf

    def set_param(self, target: str, value: float) -> None:
        """Set an FX parameter.

        *target* is ``"fx_name.param_name"`` (e.g. ``"reverb.mix"``) or a
        shorthand ``"fx_name"`` which maps to that effect's default param.
        *value* is normalized 0.0–1.0.

        Thread-safe: FX implementations guard param writes with their own
        lock so callers from non-audio threads do not race with processing.
        """
        if "." in target:
            fx_name, param_name = target.split(".", 1)
        else:
            fx_name = target
            param_name = self._default_param(fx_name)

        fx = self._effects.get(fx_name)
        if fx is not None:
            fx.set_param(param_name, value)

    @staticmethod
    def _default_param(fx_name: str) -> str:
        defaults = {
            "volume": "gain",
            "reverb": "mix",
            "delay": "mix",
            "filter": "cutoff",
            "pitch": "shift",
            "pan": "pan",
            "chorus": "mix",
        }
        return defaults.get(fx_name, "mix")

    def get_state(self) -> dict[str, dict[str, float]]:
        state = {}
        for name in self._order:
            fx = self._effects.get(name)
            if fx is not None:
                state[name] = fx.get_params()
        return state

    def get_effect(self, name: str):
        return self._effects.get(name)

    @property
    def available_targets(self) -> list[str]:
        targets = []
        for name in self._order:
            fx = self._effects.get(name)
            if fx is not None:
                for param in fx.get_params():
                    targets.append(f"{name}.{param}")
        return targets
