"""Sample Player plugin — trigger WAV samples from MIDI pads with velocity sensitivity."""

import threading

from base import Plugin

import numpy as np
import soundfile as sf
import sounddevice as sd
import toml
from pathlib import Path
from dataclasses import dataclass
from logger import get_logger

PAD_NOTE_MIN = 16
PAD_NOTE_MAX = 23
VOLUME_CC = 50
log = get_logger("sample_player")


@dataclass
class Sample:
    name: str
    note: int
    label: str
    data: np.ndarray
    sample_rate: int


@dataclass
class Voice:
    """A currently-playing sample instance."""
    data: np.ndarray
    position: int = 0


class SamplePlayerPlugin(Plugin):
    name = "Sample Player"
    version = "1.0.0"
    description = "Play audio samples on MIDI pads"
    mode_name = "Sound Pads"

    def __init__(self):
        self.samples: dict[int, Sample] = {}
        self.volume: float = 1.0
        self.packs_dir: Path = Path(__file__).parent / "packs"
        self.current_pack: str = ""
        self._active = False
        self._voices: list[Voice] = []
        self._lock = threading.Lock()
        self._stream: sd.OutputStream | None = None
        self._sample_rate: int = 44100
        self._stream_error: str | None = None
        self._pack_note_order: list[int] = []

    # -- lifecycle --

    def on_load(self, config: dict) -> None:
        self.volume = config.get("volume", 1.0)
        default_pack = config.get("default_pack", "")
        self.packs_dir.mkdir(parents=True, exist_ok=True)

        if default_pack:
            self.load_pack(default_pack)
        elif self.available_packs():
            self.load_pack(self.available_packs()[0])
        if self.samples:
            self._ensure_stream()

    def on_unload(self) -> None:
        self._stop_stream()
        self.samples.clear()
        self._pack_note_order.clear()

    def on_mode_changed(self, mode_name: str) -> None:
        self._active = mode_name == self.mode_name

    def set_owned_notes(self, notes: set[int]) -> None:
        self._active = bool(notes)
        if not notes or not self._pack_note_order:
            return
        ordered_pad = sorted(notes)
        old_keys = list(self._pack_note_order)
        by_old = {k: self.samples.pop(k, None) for k in old_keys}
        self._pack_note_order = []
        self.samples.clear()
        for i, new_n in enumerate(ordered_pad):
            if i >= len(old_keys):
                break
            old_n = old_keys[i]
            s = by_old.get(old_n)
            if s is None:
                continue
            self._pack_note_order.append(new_n)
            self.samples[new_n] = Sample(
                name=s.name,
                note=new_n,
                label=s.label,
                data=s.data,
                sample_rate=s.sample_rate,
            )

    # -- MIDI event hooks --

    def on_pad_press(self, note: int, velocity: int) -> bool:
        if not self._active:
            return False
        if note not in self.samples:
            return False
        if not self._ensure_stream():
            return False
        sample = self.samples[note]
        vol = self.volume * (velocity / 127.0)
        scaled = (sample.data * vol).astype(np.float32)
        with self._lock:
            self._voices.append(Voice(data=scaled))
        return True

    def on_pad_release(self, note: int) -> bool:
        return self._active and note in self.samples

    def on_knob(self, cc: int, value: int) -> bool:
        if not self._active:
            return False
        if cc == VOLUME_CC:
            self.volume = value / 127.0
            return True
        return False

    def get_pad_labels(self) -> dict[int, str]:
        if not self._active:
            return {}
        return {note: s.label for note, s in self.samples.items()}

    # -- audio stream (polyphonic mixer) --

    def _ensure_stream(self) -> bool:
        if self._stream is not None:
            return True
        try:
            self._stream = sd.OutputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="float32",
                callback=self._audio_callback,
                blocksize=512,
            )
            self._stream.start()
            self._stream_error = None
            return True
        except Exception as exc:
            self._stream = None
            self._stream_error = str(exc)
            self._log("SAMPLE", f"Output stream failed: {exc}", color=(255, 80, 80), level="error")
            return False

    def _stop_stream(self):
        with self._lock:
            self._voices.clear()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _audio_callback(self, outdata: np.ndarray, frames: int,
                        time_info, status):
        """Mix all active voices into the output buffer."""
        buf = np.zeros(frames, dtype=np.float32)
        with self._lock:
            still_playing: list[Voice] = []
            for voice in self._voices:
                remaining = len(voice.data) - voice.position
                if remaining <= 0:
                    continue
                n = min(frames, remaining)
                buf[:n] += voice.data[voice.position:voice.position + n]
                voice.position += n
                if voice.position < len(voice.data):
                    still_playing.append(voice)
            self._voices = still_playing

        np.clip(buf, -1.0, 1.0, out=buf)
        outdata[:, 0] = buf

    # -- pack management --

    def available_packs(self) -> list[str]:
        """List available pack directory names."""
        if not self.packs_dir.exists():
            return []
        packs = []
        for d in self.packs_dir.iterdir():
            if d.is_dir() and (d / "pack.toml").exists():
                packs.append(d.name)
        return sorted(packs)

    def load_pack(self, pack_name: str) -> None:
        """Load a sample pack by name from packs/ subdirectory."""
        pack_dir = self.packs_dir / pack_name
        manifest_path = pack_dir / "pack.toml"
        if not manifest_path.exists():
            return

        with self._lock:
            self._voices.clear()
        self.samples.clear()
        self._pack_note_order.clear()

        manifest = toml.load(str(manifest_path))

        target_sr = self._sample_rate
        for sample_def in manifest.get("samples", []):
            wav_path = pack_dir / sample_def["file"]
            if not wav_path.exists():
                continue
            data, sr = sf.read(str(wav_path), dtype="float32")
            if data.ndim > 1:
                data = data.mean(axis=1)
            if sr != target_sr:
                data = self._resample(data, sr, target_sr)
            note = sample_def["note"]
            self._pack_note_order.append(note)
            self.samples[note] = Sample(
                name=sample_def.get("name", sample_def["file"]),
                note=note,
                label=sample_def.get("label", sample_def["file"]),
                data=data.astype(np.float32),
                sample_rate=target_sr,
            )
        self.current_pack = pack_name
        if self.samples:
            self._ensure_stream()

    @staticmethod
    def _resample(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
        """Naive linear-interpolation resampler for rate conversion."""
        ratio = dst_rate / src_rate
        n_out = int(len(data) * ratio)
        indices = np.linspace(0, len(data) - 1, n_out)
        return np.interp(indices, np.arange(len(data)), data).astype(np.float32)

    def stop_all(self):
        """Silence all currently playing voices."""
        with self._lock:
            self._voices.clear()

    # -- UI --

    def build_ui(self, parent_tag: str) -> None:
        """Build DearPyGui widgets for pack selection and volume."""
        import dearpygui.dearpygui as dpg

        packs = self.available_packs()
        if not packs:
            dpg.add_text("No sample packs found", parent=parent_tag)
            dpg.add_text(
                "Add WAV packs to plugins/sample_player/packs/",
                parent=parent_tag,
                color=(150, 150, 160),
            )
            return

        if self._stream_error:
            dpg.add_text(
                f"Audio output unavailable: {self._stream_error}",
                parent=parent_tag,
                color=(255, 120, 120),
            )
            dpg.add_spacer(height=8, parent=parent_tag)

        def on_pack_changed(sender, app_data):
            self.load_pack(app_data)

        dpg.add_text("Sample Pack:", parent=parent_tag)
        dpg.add_combo(
            packs,
            default_value=self.current_pack or (packs[0] if packs else ""),
            callback=on_pack_changed,
            parent=parent_tag,
            width=250,
        )

        dpg.add_spacer(height=10, parent=parent_tag)
        dpg.add_text(f"Volume: {self.volume:.0%}", parent=parent_tag,
                      tag="sp_vol_label")

        def on_volume_slider(sender, app_data):
            self.volume = app_data
            dpg.set_value("sp_vol_label", f"Volume: {app_data:.0%}")

        dpg.add_slider_float(
            default_value=self.volume,
            min_value=0.0,
            max_value=1.0,
            callback=on_volume_slider,
            parent=parent_tag,
            width=250,
        )
