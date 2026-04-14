"""Piano plugin — polyphonic SFZ/SF2 instrument player with FX chain.

This module owns instrument loading, pitch-shift caching and the public
plugin interface. Real-time audio mixing is delegated to
:class:`audio_engine.AudioEngine`, which runs on its own producer thread
and feeds a lock-free ring buffer consumed by the sounddevice callback.
"""

import threading
from pathlib import Path

import numpy as np

from base import Plugin

from sfz_parser import SFZInstrument, parse_sfz, load_sfz_audio
from sf2_loader import SF2Instrument, load_sf2, is_available as sf2_available
from fx_engine import FXChain
from audio_engine import AudioEngine


# Ноты падов — не обрабатываем в piano
PAD_NOTES = set(range(16, 32))
# Defaults (overridable via plugin config / piano_audio settings)
_DEFAULT_MAX_VOICES = 8
SAMPLE_RATE = 44100
BLOCK_SIZE = 1024  # Smaller block = lower latency; paired with producer thread

# Максимальный размер кэша pitch-shifted сэмплов на один инструмент
_PITCH_CACHE_MAX = 512


def _resample_pitch(data: np.ndarray, semitones: int) -> np.ndarray | None:
    """Resample *data* by *semitones* half-steps.

    Uses ``scipy.signal.resample_poly`` when available, falls back to
    vectorised linear interpolation.
    """
    ratio = 2.0 ** (semitones / 12.0)
    n_out = int(len(data) / ratio)
    if n_out < 1:
        return None

    try:
        from scipy.signal import resample_poly  # type: ignore
        from math import gcd
        up = n_out
        down = len(data)
        g = gcd(up, down)
        up //= g
        down //= g
        if up <= 4096 and down <= 4096:
            return resample_poly(data, up, down).astype(np.float32)
    except ImportError:
        pass

    step = (len(data) - 1) / max(n_out - 1, 1)
    indices = np.arange(n_out, dtype=np.float64) * step
    idx_int = indices.astype(np.intp)
    frac = (indices - idx_int).astype(np.float32)
    np.minimum(idx_int, len(data) - 2, out=idx_int)
    return data[idx_int] * (1.0 - frac) + data[idx_int + 1] * frac


class PianoPlugin(Plugin):
    name = "Piano"
    version = "1.1.0"
    description = "Polyphonic SFZ/SF2 instrument player with audio FX"

    def __init__(self):
        self._instruments_dir: Path = Path(__file__).resolve().parent.parent.parent / "instruments"
        self._sfz_instrument: SFZInstrument | None = None
        self._sf2_instrument: SF2Instrument | None = None
        self._current_instrument: str = ""

        self._sample_rate: int = SAMPLE_RATE
        self._block_size: int = BLOCK_SIZE
        self._latency: str = "low"
        self._output_device: int | None = None
        self._max_voices: int = _DEFAULT_MAX_VOICES
        self._master_volume: float = 1.0

        # Кэш pitch-shifted сэмплов: (sample_id, semitones) → resampled data.
        # Guarded by ``_pitch_cache_lock`` — mutated from MIDI thread and
        # cleared from reconfigure/load paths on other threads.
        self._pitch_cache: dict[tuple[int, int], np.ndarray] = {}
        self._pitch_cache_lock = threading.Lock()

        self._engine: AudioEngine | None = None
        self._active: bool = True

    # -- lifecycle -----------------------------------------------------------------

    def on_load(self, config: dict) -> None:
        self._instruments_dir.mkdir(parents=True, exist_ok=True)

        # Plugin.toml defaults
        if "max_polyphony" in config:
            self._max_voices = max(1, min(32, int(config["max_polyphony"])))
        if "block_size" in config:
            self._block_size = max(256, min(4096, int(config["block_size"])))

        # User settings overlay (piano_audio key)
        try:
            import settings as _settings
            pa = _settings.get("piano_audio", None)
            if isinstance(pa, dict):
                if "sample_rate" in pa:
                    sr = int(pa["sample_rate"])
                    if sr in (32000, 44100, 48000):
                        self._sample_rate = sr
                if "block_size" in pa:
                    bs = int(pa["block_size"])
                    if bs in (256, 512, 1024, 2048, 4096):
                        self._block_size = bs
                if "max_polyphony" in pa:
                    self._max_voices = max(1, min(32, int(pa["max_polyphony"])))
                if "latency_mode" in pa and pa["latency_mode"] in ("low", "medium", "high"):
                    self._latency = pa["latency_mode"]
                if "output_device" in pa:
                    od = pa["output_device"]
                    self._output_device = int(od) if od is not None else None
                if "master_volume" in pa:
                    self._master_volume = max(0.0, min(1.0, float(pa["master_volume"])))
        except Exception as exc:
            self._log("PIANO", f"Failed to load piano_audio settings: {exc}",
                      color=(255, 160, 60), level="warning")

        with self._pitch_cache_lock:
            self._pitch_cache.clear()
        self._engine = AudioEngine(
            sample_rate=self._sample_rate,
            block_size=self._block_size,
            latency=self._latency,
            output_device=self._output_device,
            max_voices=self._max_voices,
            master_volume=self._master_volume,
            log_fn=self._log,
        )
        self._engine.start()

        default = config.get("default_instrument", "")
        if default:
            self.load_instrument(default)
        elif self.available_instruments():
            self.load_instrument(self.available_instruments()[0])

    def on_unload(self) -> None:
        if self._engine is not None:
            self._engine.stop()
            self._engine = None
        self._sfz_instrument = None
        self._sf2_instrument = None

    # -- runtime reconfiguration ---------------------------------------------------

    def reconfigure(self, cfg: dict) -> tuple[bool, str | None]:
        """Apply new audio settings via the engine command queue.

        Returns ``(ok, error)``. The engine processes the command between
        blocks, so there is no race with ``note_on`` from the MIDI thread.
        """
        if self._engine is None:
            return False, "engine not initialized"

        ok, err = self._engine.reconfigure(cfg)
        if not ok:
            self._log(
                "PIANO",
                f"Reconfigure failed: {err}",
                color=(255, 80, 80),
                level="error",
            )
            return False, err

        # Mirror config into plugin fields for consistency.
        try:
            if "sample_rate" in cfg:
                new_sr = int(cfg["sample_rate"])
                if new_sr != self._sample_rate:
                    self._sample_rate = new_sr
                    # Sample data was loaded at old SR — reload at new SR.
                    with self._pitch_cache_lock:
                        self._pitch_cache.clear()
                    if self._current_instrument:
                        self.load_instrument(self._current_instrument)
            if "block_size" in cfg:
                self._block_size = int(cfg["block_size"])
            if "latency_mode" in cfg:
                self._latency = str(cfg["latency_mode"])
            if "output_device" in cfg:
                od = cfg["output_device"]
                self._output_device = int(od) if od is not None else None
            if "max_polyphony" in cfg:
                self._max_voices = max(1, min(32, int(cfg["max_polyphony"])))
            if "master_volume" in cfg:
                self._master_volume = max(0.0, min(1.0, float(cfg["master_volume"])))
        except (TypeError, ValueError) as exc:
            return False, f"Invalid config: {exc}"

        self._log(
            "PIANO",
            f"Reconfigured: sr={self._sample_rate}, bs={self._block_size}, "
            f"latency={self._latency}, poly={self._max_voices}",
            color=(120, 220, 120),
        )
        return True, None

    # -- MIDI event hooks ----------------------------------------------------------

    def on_pad_press(self, note: int, velocity: int) -> bool:
        if note in PAD_NOTES:
            return False
        self._note_on(note, velocity)
        return True

    def on_pad_release(self, note: int) -> bool:
        if note in PAD_NOTES:
            return False
        self._note_off(note)
        return True

    def on_knob(self, cc: int, value: int) -> bool:
        return False

    # -- plugin action/knob catalog ------------------------------------------------

    def get_knob_catalog(self) -> list[dict]:
        catalog = []
        chain = self.fx_chain
        for target in chain.available_targets:
            fx_name, param = target.split(".", 1)
            catalog.append({
                "id": f"fx_{target}",
                "label": f"{fx_name.title()} {param.title()}",
                "description": f"Control {fx_name} {param} (0-127 → 0.0-1.0)",
                "params_schema": {},
            })
        return catalog

    def execute_plugin_knob(self, action_id: str, value: int, params: dict) -> bool:
        if not action_id.startswith("fx_"):
            return False
        target = action_id[3:]
        normalized = value / 127.0
        if self._engine is not None:
            self._engine.set_fx_param(target, normalized)
        self._log("PIANO", f"FX {target} = {normalized:.2f}", color=(120, 180, 255))
        return True

    def get_action_catalog(self) -> list[dict]:
        return [
            {
                "id": "piano_note",
                "label": "Play Piano Note",
                "description": "Trigger a note on the loaded instrument",
            }
        ]

    # -- instrument management -----------------------------------------------------

    def available_instruments(self) -> list[str]:
        if not self._instruments_dir.exists():
            return []
        instruments = []
        for item in self._instruments_dir.iterdir():
            if item.is_dir():
                sfz_files = list(item.glob("*.sfz"))
                if sfz_files:
                    instruments.append(item.name)
            elif item.suffix.lower() == ".sf2":
                instruments.append(item.stem)
        return sorted(instruments)

    def load_instrument(self, name: str) -> bool:
        if self._engine is not None:
            self._engine.stop_all_voices()
        self._sfz_instrument = None
        self._sf2_instrument = None
        self._current_instrument = ""
        with self._pitch_cache_lock:
            self._pitch_cache.clear()

        sfz_dir = self._instruments_dir / name
        if sfz_dir.is_dir():
            sfz_files = list(sfz_dir.glob("*.sfz"))
            if sfz_files:
                try:
                    inst = parse_sfz(sfz_files[0])
                    load_sfz_audio(inst, target_sample_rate=self._sample_rate)
                    loaded_count = sum(1 for r in inst.regions if r.data is not None)
                    if loaded_count > 0:
                        self._sfz_instrument = inst
                        self._current_instrument = name
                        self._log("PIANO", f"Loaded SFZ: {name} ({loaded_count} regions)",
                                  color=(100, 220, 100))
                        return True
                except Exception as exc:
                    self._log("PIANO", f"Failed to load SFZ {name}: {exc}",
                              color=(255, 80, 80), level="error")

        sf2_path = self._instruments_dir / f"{name}.sf2"
        if sf2_path.exists():
            if not sf2_available():
                self._log("PIANO", "sf2utils not installed — cannot load SF2 files",
                          color=(255, 160, 60), level="warning")
                return False
            try:
                inst = load_sf2(sf2_path, target_sample_rate=self._sample_rate)
                if inst and inst.samples:
                    self._sf2_instrument = inst
                    self._current_instrument = name
                    self._log("PIANO", f"Loaded SF2: {name} ({len(inst.samples)} samples)",
                              color=(100, 220, 100))
                    return True
            except Exception as exc:
                self._log("PIANO", f"Failed to load SF2 {name}: {exc}",
                          color=(255, 80, 80), level="error")

        self._log("PIANO", f"Instrument not found: {name}", color=(255, 160, 60), level="warning")
        return False

    @property
    def current_instrument(self) -> str:
        return self._current_instrument

    @property
    def fx_chain(self) -> FXChain:
        if self._engine is not None:
            return self._engine.fx_chain
        # Fallback FX chain for catalogue queries before start.
        return FXChain(sample_rate=self._sample_rate)

    # -- note on/off ---------------------------------------------------------------

    def _note_on(self, note: int, velocity: int) -> None:
        if self._engine is None:
            return
        samples_data = self._get_samples_for_note(note, velocity)
        if not samples_data:
            return

        prepared: list[tuple[np.ndarray, int]] = []
        for data, root_key, origin_id in samples_data:
            if note != root_key:
                semitones = note - root_key
                cache_key = (origin_id, semitones)
                with self._pitch_cache_lock:
                    cached = self._pitch_cache.get(cache_key)
                if cached is not None:
                    data = cached
                else:
                    resampled = _resample_pitch(data, semitones)
                    if resampled is None:
                        continue
                    data = resampled
                    with self._pitch_cache_lock:
                        if len(self._pitch_cache) < _PITCH_CACHE_MAX:
                            self._pitch_cache[cache_key] = data
            prepared.append((data, origin_id))

        if prepared:
            self._engine.note_on(
                note=note,
                velocity=velocity,
                samples=prepared,
                master_volume_override=self._master_volume,
            )

    def _note_off(self, note: int) -> None:
        if self._engine is not None:
            self._engine.note_off(note)

    def _get_samples_for_note(self, note: int, velocity: int) -> list[tuple[np.ndarray, int]]:
        """Return list of ``(mono_data, root_key, origin_id)`` tuples.

        ``origin_id`` is ``id()`` of the underlying sample array, used as a
        stable key for the pitch-shift cache. Data is returned without copy
        — the producer thread reads samples without mutating them.
        """
        results = []
        if self._sfz_instrument is not None:
            regions = self._sfz_instrument.get_regions_for_note(note, velocity)
            for region in regions:
                if region.data is not None:
                    results.append((region.data, region.pitch_keycenter, id(region.data)))
        elif self._sf2_instrument is not None:
            samples = self._sf2_instrument.get_samples_for_note(note, velocity)
            for sample in samples:
                if sample.data is not None:
                    results.append((sample.data, sample.pitch_keycenter, id(sample.data)))
        return results

    # -- UI ------------------------------------------------------------------------

    def build_ui(self, parent_tag: str) -> None:
        import dearpygui.dearpygui as dpg

        instruments = self.available_instruments()
        if not instruments:
            dpg.add_text("No instruments found", parent=parent_tag)
            dpg.add_text(
                "Add SFZ/SF2 to instruments/ directory",
                parent=parent_tag,
                color=(150, 150, 160),
            )
            return

        err = self._engine.stream_error if self._engine is not None else None
        if err:
            dpg.add_text(
                f"Audio unavailable: {err}",
                parent=parent_tag,
                color=(255, 120, 120),
            )
            dpg.add_spacer(height=8, parent=parent_tag)

        def on_instrument_changed(sender, app_data):
            self.load_instrument(app_data)

        dpg.add_text("Instrument:", parent=parent_tag)
        dpg.add_combo(
            instruments,
            default_value=self._current_instrument or (instruments[0] if instruments else ""),
            callback=on_instrument_changed,
            parent=parent_tag,
            width=250,
        )

        dpg.add_spacer(height=10, parent=parent_tag)
        dpg.add_text("FX Chain:", parent=parent_tag)

        state = self.fx_chain.get_state()
        for fx_name, params in state.items():
            param_str = ", ".join(f"{k}={v:.2f}" for k, v in params.items())
            dpg.add_text(
                f"  {fx_name}: {param_str}",
                parent=parent_tag,
                color=(160, 180, 200),
            )

    def get_status(self) -> tuple[str, tuple[int, int, int]] | None:
        if self._current_instrument:
            voice_count = self._engine.active_voice_count if self._engine is not None else 0
            return (f"Piano: {self._current_instrument} ({voice_count}v)", (120, 180, 255))
        return None

    def status(self) -> dict:
        """Extended status including engine metrics (polyphony, underruns)."""
        base = {
            "instrument": self._current_instrument,
            "instruments": self.available_instruments(),
            "master_volume": self._master_volume,
        }
        if self._engine is not None:
            base["engine"] = self._engine.status()
        return base

    def stop_all(self) -> None:
        if self._engine is not None:
            self._engine.stop_all_voices()
