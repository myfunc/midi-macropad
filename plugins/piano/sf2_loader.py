"""SF2 (SoundFont 2) loader — extracts instrument samples from .sf2 files.

Uses ``sf2utils`` for parsing. Falls back gracefully if not installed.
"""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

try:
    from sf2utils.sf2parse import Sf2File
    SF2_AVAILABLE = True
except ImportError:
    SF2_AVAILABLE = False


@dataclass
class SF2Sample:
    """A single sample extracted from an SF2 preset."""
    name: str
    lokey: int = 0
    hikey: int = 127
    pitch_keycenter: int = 60
    lovel: int = 0
    hivel: int = 127
    data: np.ndarray | None = field(default=None, repr=False)
    sample_rate: int = 44100


@dataclass
class SF2Instrument:
    """An instrument loaded from an SF2 file."""
    name: str
    path: Path
    samples: list[SF2Sample] = field(default_factory=list)

    def get_samples_for_note(self, note: int, velocity: int = 100) -> list[SF2Sample]:
        """Find all samples matching a given note and velocity."""
        return [
            s for s in self.samples
            if s.lokey <= note <= s.hikey
            and s.lovel <= velocity <= s.hivel
            and s.data is not None
        ]


def is_available() -> bool:
    """Check if sf2utils is installed."""
    return SF2_AVAILABLE


def load_sf2(
    sf2_path: Path,
    preset_index: int = 0,
    target_sample_rate: int = 44100,
) -> SF2Instrument | None:
    """Load an SF2 file and extract samples for the given preset.

    Returns None if sf2utils is not installed or loading fails.
    """
    if not SF2_AVAILABLE:
        return None

    try:
        with open(sf2_path, "rb") as f:
            sf2 = Sf2File(f)
    except Exception:
        return None

    name = sf2_path.stem
    instrument = SF2Instrument(name=name, path=sf2_path)

    try:
        _extract_samples(sf2, instrument, preset_index, target_sample_rate)
    except Exception:
        # SF2 structure can vary — fail gracefully
        pass

    return instrument


def _extract_samples(
    sf2: "Sf2File",
    instrument: SF2Instrument,
    preset_index: int,
    target_sample_rate: int,
) -> None:
    """Extract sample data from the SF2 file into the instrument."""
    if not sf2.presets:
        return

    # Ограничиваем индекс пресета
    if preset_index >= len(sf2.presets):
        preset_index = 0

    preset = sf2.presets[preset_index]

    # Итерируемся по инструментам в пресете через bags
    for bag in getattr(preset, "bags", []):
        inst = getattr(bag, "instrument", None)
        if inst is None:
            continue

        for ibag in getattr(inst, "bags", []):
            sample = getattr(ibag, "sample", None)
            if sample is None:
                continue

            # Извлекаем аудио данные
            raw_data = getattr(sample, "raw_sample_data", None)
            if raw_data is None:
                continue

            # SF2 raw data is 16-bit signed PCM
            try:
                pcm = np.frombuffer(raw_data, dtype=np.int16)
                data = pcm.astype(np.float32) / 32768.0
            except Exception:
                continue

            sr = getattr(sample, "sample_rate", 44100) or 44100

            # Resample if needed
            if sr != target_sample_rate:
                ratio = target_sample_rate / sr
                n_out = int(len(data) * ratio)
                if n_out < 1:
                    continue
                indices = np.linspace(0, len(data) - 1, n_out)
                data = np.interp(indices, np.arange(len(data)), data).astype(np.float32)

            # Key range from bag generators
            lokey = _get_generator(ibag, "keyRange", 0, 0)
            hikey = _get_generator(ibag, "keyRange", 1, 127)
            lovel = _get_generator(ibag, "velRange", 0, 0)
            hivel = _get_generator(ibag, "velRange", 1, 127)
            root_key = getattr(sample, "original_pitch", 60) or 60

            sf2_sample = SF2Sample(
                name=getattr(sample, "name", "unknown") or "unknown",
                lokey=lokey,
                hikey=hikey,
                pitch_keycenter=root_key,
                lovel=lovel,
                hivel=hivel,
                data=data,
                sample_rate=target_sample_rate,
            )
            instrument.samples.append(sf2_sample)


def _get_generator(bag, gen_name: str, index: int, default: int) -> int:
    """Safely extract a generator value from a bag."""
    try:
        gens = getattr(bag, "generators", {})
        if gen_name in gens:
            val = gens[gen_name]
            if hasattr(val, "__getitem__"):
                return int(val[index])
            if hasattr(val, "lo" if index == 0 else "hi"):
                return int(getattr(val, "lo" if index == 0 else "hi"))
            return int(val)
    except (KeyError, IndexError, TypeError, AttributeError):
        pass
    return default
