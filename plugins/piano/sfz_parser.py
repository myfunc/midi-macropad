"""SFZ format parser — loads region-based instrument definitions.

SFZ is a text format defining how WAV samples map to MIDI keys:

    <group>
    lokey=36 hikey=96

    <region>
    sample=piano_c4.wav
    lokey=60 hikey=60
    pitch_keycenter=60
    lovel=0 hivel=63

    <region>
    sample=piano_c4_loud.wav
    lokey=60 hikey=60
    pitch_keycenter=60
    lovel=64 hivel=127
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import soundfile as sf


@dataclass
class SFZRegion:
    """A single SFZ region — one sample mapped to a key/velocity range."""
    sample: str = ""
    lokey: int = 0
    hikey: int = 127
    pitch_keycenter: int = 60
    lovel: int = 0
    hivel: int = 127
    volume: float = 0.0       # dB
    tune: int = 0             # cents
    offset: int = 0           # sample offset
    loop_mode: str = "no_loop"
    loop_start: int = 0
    loop_end: int = 0

    # Loaded audio data (populated by load())
    data: np.ndarray | None = field(default=None, repr=False)
    sample_rate: int = 44100


@dataclass
class SFZInstrument:
    """Parsed SFZ instrument with all regions loaded."""
    name: str
    path: Path
    regions: list[SFZRegion] = field(default_factory=list)

    def get_regions_for_note(self, note: int, velocity: int = 100) -> list[SFZRegion]:
        """Find all regions matching a given note and velocity."""
        return [
            r for r in self.regions
            if r.lokey <= note <= r.hikey
            and r.lovel <= velocity <= r.hivel
            and r.data is not None
        ]


# Маппинг нотных имён на MIDI номера
_NOTE_NAMES = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}
_NOTE_RE = re.compile(r"^([a-gA-G])([#b]?)(-?\d+)$")


def _note_name_to_midi(name: str) -> int | None:
    """Convert note name like 'c4', 'f#3', 'bb5' to MIDI number."""
    m = _NOTE_RE.match(name.strip().lower())
    if not m:
        return None
    letter, accidental, octave_str = m.groups()
    midi = _NOTE_NAMES[letter] + (int(octave_str) + 1) * 12
    if accidental == "#":
        midi += 1
    elif accidental == "b":
        midi -= 1
    return midi


def _parse_key(value: str) -> int:
    """Parse a key value — either a MIDI number or a note name."""
    try:
        return int(value)
    except ValueError:
        result = _note_name_to_midi(value)
        if result is not None:
            return result
        raise ValueError(f"Cannot parse key: {value}")


def parse_sfz(sfz_path: Path) -> SFZInstrument:
    """Parse an SFZ file and return an SFZInstrument (without loading audio).

    Audio is loaded separately via ``load_sfz_audio()``.
    """
    text = sfz_path.read_text(encoding="utf-8", errors="replace")
    name = sfz_path.stem
    instrument = SFZInstrument(name=name, path=sfz_path.parent)

    # Удаляем комментарии
    text = re.sub(r"//[^\n]*", "", text)

    # Парсим секции <group> и <region>
    group_defaults: dict[str, str] = {}
    current_region: dict[str, str] | None = None

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        # Обработка заголовков секций (может быть несколько на строке)
        parts = re.split(r"(<\w+>)", line)
        for part in parts:
            part = part.strip()
            if not part:
                continue

            if part == "<group>":
                # Сохраняем текущий region если есть
                if current_region is not None:
                    instrument.regions.append(_build_region(current_region))
                current_region = None
                group_defaults = {}
                continue

            if part == "<region>":
                if current_region is not None:
                    instrument.regions.append(_build_region(current_region))
                current_region = dict(group_defaults)
                continue

            if part.startswith("<"):
                # Другие секции (<control>, <global>, etc.) — пропускаем
                if current_region is not None:
                    instrument.regions.append(_build_region(current_region))
                    current_region = None
                continue

            # Опкоды: key=value пары
            for opcode_match in re.finditer(r"(\w+)=(\S+)", part):
                key, value = opcode_match.groups()
                if current_region is not None:
                    current_region[key] = value
                else:
                    group_defaults[key] = value

    # Последний region
    if current_region is not None:
        instrument.regions.append(_build_region(current_region))

    return instrument


def _build_region(opcodes: dict[str, str]) -> SFZRegion:
    """Build an SFZRegion from parsed opcode key-value pairs."""
    region = SFZRegion()

    if "sample" in opcodes:
        # Нормализуем слэши (SFZ часто использует обратные)
        region.sample = opcodes["sample"].replace("\\", "/")

    if "key" in opcodes:
        # Shorthand: key=60 sets lokey=hikey=pitch_keycenter=60
        k = _parse_key(opcodes["key"])
        region.lokey = region.hikey = region.pitch_keycenter = k

    if "lokey" in opcodes:
        region.lokey = _parse_key(opcodes["lokey"])
    if "hikey" in opcodes:
        region.hikey = _parse_key(opcodes["hikey"])
    if "pitch_keycenter" in opcodes:
        region.pitch_keycenter = _parse_key(opcodes["pitch_keycenter"])

    if "lovel" in opcodes:
        region.lovel = int(opcodes["lovel"])
    if "hivel" in opcodes:
        region.hivel = int(opcodes["hivel"])
    if "volume" in opcodes:
        region.volume = float(opcodes["volume"])
    if "tune" in opcodes:
        region.tune = int(opcodes["tune"])
    if "offset" in opcodes:
        region.offset = int(opcodes["offset"])
    if "loop_mode" in opcodes:
        region.loop_mode = opcodes["loop_mode"]
    if "loop_start" in opcodes:
        region.loop_start = int(opcodes["loop_start"])
    if "loop_end" in opcodes:
        region.loop_end = int(opcodes["loop_end"])

    return region


def load_sfz_audio(
    instrument: SFZInstrument,
    target_sample_rate: int = 44100,
) -> SFZInstrument:
    """Load WAV files for all regions in the instrument.

    Regions with missing samples are silently skipped (data remains None).
    Returns the same instrument with audio data populated.
    """
    for region in instrument.regions:
        if not region.sample:
            continue

        wav_path = instrument.path / region.sample
        if not wav_path.exists():
            continue

        try:
            data, sr = sf.read(str(wav_path), dtype="float32")
        except Exception:
            continue

        # Convert to mono
        if data.ndim > 1:
            data = data.mean(axis=1)

        # Apply sample offset
        if region.offset > 0 and region.offset < len(data):
            data = data[region.offset:]

        # Resample if needed
        if sr != target_sample_rate:
            ratio = target_sample_rate / sr
            n_out = int(len(data) * ratio)
            indices = np.linspace(0, len(data) - 1, n_out)
            data = np.interp(indices, np.arange(len(data)), data).astype(np.float32)

        # Apply volume (dB)
        if region.volume != 0.0:
            data = data * (10.0 ** (region.volume / 20.0))

        region.data = data.astype(np.float32)
        region.sample_rate = target_sample_rate

    return instrument
