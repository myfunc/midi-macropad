"""Voice Scribe plugin — speak Russian, type English with configurable style prompts."""

import sys
import os
import io
import threading
import time
import wave
import ctypes
import ctypes.wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from base import Plugin

import numpy as np
import sounddevice as sd
import toml
from pathlib import Path
from logger import get_logger

log = get_logger("voice_scribe")

MODE_NAME = "Voice Scribe"
PAD_NOTE_OFFSET = 15
CLIPBOARD_DEFAULT_PROMPT = (
    "Translate the following Russian text into professional English. "
    "Output only the translation."
)
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"
_KEY_FILE = Path(__file__).parent / ".api_key"


def _load_api_key_from_files() -> str:
    if _KEY_FILE.exists():
        key = _KEY_FILE.read_text(encoding="utf-8").strip()
        if key:
            return key
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("OPENAI_API_KEY", "")


# ---------------------------------------------------------------------------
# Windows clipboard helpers (64-bit safe)
# ---------------------------------------------------------------------------

_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002
_GMEM_ZEROINIT = 0x0040
_GHND = _GMEM_MOVEABLE | _GMEM_ZEROINIT

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_kernel32.GlobalAlloc.restype = ctypes.c_void_p
_kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalUnlock.restype = ctypes.wintypes.BOOL
_kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalFree.restype = ctypes.c_void_p
_kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
_user32.OpenClipboard.restype = ctypes.wintypes.BOOL
_user32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]
_user32.CloseClipboard.restype = ctypes.wintypes.BOOL
_user32.EmptyClipboard.restype = ctypes.wintypes.BOOL
_user32.SetClipboardData.restype = ctypes.wintypes.HANDLE
_user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
_user32.GetClipboardData.restype = ctypes.c_void_p
_user32.GetClipboardData.argtypes = [ctypes.c_uint]

_CLIPBOARD_RETRIES = 10
_CLIPBOARD_RETRY_MS = 30


def _open_clipboard_safe() -> bool:
    for _ in range(_CLIPBOARD_RETRIES):
        if _user32.OpenClipboard(None):
            return True
        time.sleep(_CLIPBOARD_RETRY_MS / 1000)
    return False


def _clipboard_write(text: str) -> None:
    data = text.encode("utf-16-le") + b"\x00\x00"
    if not _open_clipboard_safe():
        raise OSError("Cannot open clipboard (locked by another app?)")
    try:
        _user32.EmptyClipboard()
        h = _kernel32.GlobalAlloc(_GHND, len(data))
        if not h:
            raise OSError("GlobalAlloc returned NULL")
        ptr = _kernel32.GlobalLock(h)
        if not ptr:
            _kernel32.GlobalFree(h)
            raise OSError("GlobalLock returned NULL")
        ctypes.memmove(ptr, data, len(data))
        _kernel32.GlobalUnlock(h)
        if not _user32.SetClipboardData(_CF_UNICODETEXT, h):
            _kernel32.GlobalFree(h)
            raise OSError("SetClipboardData failed")
    finally:
        _user32.CloseClipboard()


def _clipboard_read() -> str:
    if not _open_clipboard_safe():
        return ""
    try:
        h = _user32.GetClipboardData(_CF_UNICODETEXT)
        if not h:
            return ""
        ptr = _kernel32.GlobalLock(h)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            _kernel32.GlobalUnlock(h)
    finally:
        _user32.CloseClipboard()


# ---------------------------------------------------------------------------
# Mic enumeration helper
# ---------------------------------------------------------------------------

def _list_input_devices() -> list[tuple[int, str]]:
    """Return [(device_index, device_name), ...] for input-capable devices."""
    result = []
    try:
        for i, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                result.append((i, dev["name"]))
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

class VoiceScribePlugin(Plugin):
    name = "Voice Scribe"
    version = "1.1.0"
    description = "Speak Russian -> type English with custom style prompts"

    def __init__(self):
        self.api_key: str = ""
        self.chat_model: str = "gpt-4o-mini"
        self.transcription_model: str = "whisper-1"
        self.input_language: str = "ru"
        self.output_language: str = "en"
        self.sample_rate: int = 16000
        self.mic_device: int | None = None

        self._prompts: dict[int, dict] = {}
        self._plugin_dir: Path = Path(__file__).parent

        self._active: bool = False
        self._recording: bool = False
        self._active_note: int | None = None
        self._audio_chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self._processing: bool = False

        self._status: str = "Idle"
        self._last_original: str = ""
        self._last_result: str = ""
        self._last_prompt_label: str = ""
        self._whisper_prompt: str = ""

    # -- lifecycle ------------------------------------------------------------

    def on_load(self, config: dict) -> None:
        self.api_key = config.get("openai_api_key", "") or _load_api_key_from_files()
        self.chat_model = config.get("chat_model", "gpt-4o-mini")
        self.transcription_model = config.get("transcription_model", "whisper-1")
        self.input_language = config.get("input_language", "ru")
        self.output_language = config.get("output_language", "en")
        self.sample_rate = config.get("sample_rate", 16000)
        self._load_mic_device()
        self._load_prompts()
        log.info("Voice Scribe loaded (chat=%s, whisper=%s, %s->%s, mic=%s)",
                 self.chat_model, self.transcription_model,
                 self.input_language, self.output_language,
                 self.mic_device)

    def on_unload(self) -> None:
        self._stop_recording()

    def on_mode_changed(self, mode_name: str) -> None:
        self._active = mode_name == MODE_NAME
        if not self._active:
            self._stop_recording()

    # -- mic device persistence -----------------------------------------------

    def _load_mic_device(self):
        path = self._plugin_dir / "mic_device.txt"
        if path.exists():
            try:
                val = path.read_text(encoding="utf-8").strip()
                if val:
                    self.mic_device = int(val)
                    return
            except (ValueError, OSError):
                pass
        self.mic_device = None

    def _save_mic_device(self):
        path = self._plugin_dir / "mic_device.txt"
        try:
            path.write_text(str(self.mic_device or ""), encoding="utf-8")
        except OSError:
            pass

    # -- prompts --------------------------------------------------------------

    def _load_prompts(self) -> None:
        path = self._plugin_dir / "prompts.toml"
        if not path.exists():
            log.warning("prompts.toml not found, no pads configured")
            return
        data = toml.load(str(path))
        self._whisper_prompt = data.get("whisper_prompt", "")
        self._prompts.clear()
        for p in data.get("prompts", []):
            note = p["pad"] + PAD_NOTE_OFFSET
            p["_note"] = note
            self._prompts[note] = p
        log.info("Loaded %d prompt pads", len(self._prompts))

    # -- MIDI hooks -----------------------------------------------------------

    def on_pad_press(self, note: int, velocity: int) -> bool:
        if not self._active or note not in self._prompts:
            return False
        info = self._prompts[note]
        label = info.get("label", "")

        if label == "Cancel":
            self._stop_recording()
            self._set_status("Cancelled", "warn")
            return True

        if label == "Clipboard":
            if not self._processing:
                threading.Thread(target=self._process_clipboard, daemon=True).start()
            return True

        if self._recording and self._active_note == note:
            self._stop_recording()
            if not self._processing:
                threading.Thread(target=self._process_audio, args=(note,),
                                 daemon=True).start()
        elif not self._recording and not self._processing:
            self._active_note = note
            self._start_recording()
            self._set_status(f"Recording [{label}]", "recording")
        return True

    def on_pad_release(self, note: int) -> bool:
        if not self._active or note not in self._prompts:
            return False
        return self._recording and self._active_note == note

    def get_pad_labels(self) -> dict[int, str]:
        if not self._active:
            return {}
        return {
            note: p.get("label", f"Pad {p.get('pad', '?')}")
            for note, p in self._prompts.items()
        }

    def get_status(self) -> tuple[str, tuple[int, int, int]] | None:
        if not self._active:
            return None
        color = self._STATUS_COLORS.get(
            "recording" if self._recording else
            "busy" if self._processing else "idle",
            (150, 150, 160),
        )
        return f"Voice Scribe: {self._status}", color

    # -- recording ------------------------------------------------------------

    def _start_recording(self) -> None:
        with self._lock:
            self._audio_chunks.clear()
            self._recording = True
        try:
            kwargs: dict = dict(
                samplerate=self.sample_rate,
                channels=1, dtype="int16",
                callback=self._audio_cb,
                blocksize=1024,
            )
            if self.mic_device is not None:
                kwargs["device"] = self.mic_device
            self._stream = sd.InputStream(**kwargs)
            self._stream.start()
        except Exception as exc:
            self._recording = False
            self._set_status(f"Mic error: {exc}", "error")
            log.error("Mic start failed: %s", exc)

    def _stop_recording(self) -> None:
        self._recording = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def _audio_cb(self, indata, frames, time_info, status):
        if self._recording:
            with self._lock:
                self._audio_chunks.append(indata.copy())

    def _collect_wav(self) -> bytes:
        with self._lock:
            if not self._audio_chunks:
                return b""
            audio = np.concatenate(self._audio_chunks, axis=0)
        rms = int(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        peak = int(np.max(np.abs(audio)))
        log.info("Audio RMS: %d, peak: %d", rms, peak)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio.tobytes())
        return buf.getvalue()

    # -- processing pipeline --------------------------------------------------

    def _process_audio(self, note: int) -> None:
        self._processing = True
        try:
            self._set_status("Transcribing...", "busy")
            wav = self._collect_wav()
            if not wav:
                self._set_status("No audio captured", "warn")
                return
            transcript = self._whisper_transcribe(wav)
            if not transcript:
                self._set_status("Empty transcription", "warn")
                return
            self._last_original = transcript
            info = self._prompts.get(note, {})
            label = info.get("label", "")
            system = info.get("system", "")
            self._last_prompt_label = label
            log.info("Transcript [%s]: %s", label, transcript[:120])

            if label == "Raw" or not system:
                self._paste(transcript)
                self._last_result = transcript
                self._set_status(f"Done [{label or 'raw'}]", "ok")
                return

            self._set_status(f"Translating [{label}]...", "busy")
            translated = self._gpt_translate(transcript, system)
            if translated:
                self._paste(translated)
                self._last_result = translated
                self._set_status(f"Done [{label}]", "ok")
            else:
                self._set_status("Translation returned empty", "warn")
        except Exception as exc:
            log.error("Processing error: %s", exc)
            self._set_status(f"Error: {exc}", "error")
            if self._log_fn:
                self._log_fn("VOICE", str(exc)[:80], color=(255, 80, 80))
        finally:
            self._processing = False

    def _process_clipboard(self) -> None:
        self._processing = True
        try:
            self._set_status("Translating clipboard...", "busy")
            text = _clipboard_read()
            if not text or not text.strip():
                self._set_status("Clipboard empty", "warn")
                return
            self._last_original = text
            pro = self._prompts.get(1 + PAD_NOTE_OFFSET, {})
            system = pro.get("system", CLIPBOARD_DEFAULT_PROMPT)
            translated = self._gpt_translate(text, system)
            if translated:
                self._paste(translated)
                self._last_result = translated
                self._set_status("Done [Clipboard]", "ok")
            else:
                self._set_status("Translation returned empty", "warn")
        except Exception as exc:
            log.error("Clipboard error: %s", exc)
            self._set_status(f"Error: {exc}", "error")
            if self._log_fn:
                self._log_fn("VOICE", str(exc)[:80], color=(255, 80, 80))
        finally:
            self._processing = False

    # -- API calls ------------------------------------------------------------

    def _whisper_transcribe(self, wav_data: bytes) -> str:
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key)
        audio_file = io.BytesIO(wav_data)
        audio_file.name = "recording.wav"
        kwargs: dict = dict(
            model=self.transcription_model,
            file=audio_file,
            language=self.input_language,
        )
        if self._whisper_prompt:
            kwargs["prompt"] = self._whisper_prompt
        resp = client.audio.transcriptions.create(**kwargs)
        return resp.text.strip()

    def _gpt_translate(self, text: str, system_prompt: str) -> str:
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key)
        resp = client.chat.completions.create(
            model=self.chat_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()

    # -- paste at cursor ------------------------------------------------------

    @staticmethod
    def _paste(text: str) -> None:
        from pynput.keyboard import Key, Controller
        _clipboard_write(text)
        time.sleep(0.1)
        kb = Controller()
        kb.press(Key.ctrl_l)
        kb.press("v")
        kb.release("v")
        kb.release(Key.ctrl_l)
        time.sleep(0.05)

    # -- UI -------------------------------------------------------------------

    _STATUS_COLORS = {
        "idle": (150, 150, 160),
        "recording": (255, 80, 80),
        "busy": (255, 200, 80),
        "ok": (80, 255, 120),
        "warn": (255, 180, 80),
        "error": (255, 80, 80),
    }

    def _set_status(self, text: str, kind: str = "idle") -> None:
        self._status = text
        color = self._STATUS_COLORS.get(kind, (150, 150, 160))
        try:
            import dearpygui.dearpygui as dpg
            if dpg.does_item_exist("vs_status"):
                dpg.set_value("vs_status", f"Status: {text}")
                dpg.configure_item("vs_status", color=color)
            if dpg.does_item_exist("vs_active_prompt") and self._last_prompt_label:
                dpg.set_value("vs_active_prompt",
                              f"Prompt: {self._last_prompt_label}")
            if self._last_original and dpg.does_item_exist("vs_orig"):
                short = self._last_original[:200].replace("\n", " ")
                dpg.set_value("vs_orig", f"RU: {short}")
            if self._last_result and dpg.does_item_exist("vs_result"):
                short = self._last_result[:200].replace("\n", " ")
                dpg.set_value("vs_result", f"EN: {short}")
        except Exception:
            pass

    # -- Sidebar status (build_ui) -------------------------------------------

    def build_ui(self, parent_tag: str) -> None:
        """Minimal status for the plugin list in the left sidebar."""
        import dearpygui.dearpygui as dpg
        info = f"Chat: {self.chat_model}  |  Lang: {self.input_language} -> {self.output_language}"
        dpg.add_text(info, parent=parent_tag, color=(120, 120, 140))

    # -- Dockable window: Prompt Editor --------------------------------------

    def register_windows(self) -> list[dict]:
        return [
            {"id": "vs_prompt_editor", "title": "Prompt Editor",
             "default_open": True},
        ]

    def build_window(self, window_id: str, parent_tag: str) -> None:
        if window_id != "vs_prompt_editor":
            return
        import dearpygui.dearpygui as dpg
        self._build_prompt_editor(parent_tag)

    def _build_prompt_editor(self, parent: str) -> None:
        import dearpygui.dearpygui as dpg

        dpg.add_text("Status: Idle", tag="vs_status", parent=parent,
                     color=(150, 150, 160))
        dpg.add_text("Prompt: --", tag="vs_active_prompt",
                     parent=parent, color=(120, 120, 140), wrap=400)
        dpg.add_text("RU: --", tag="vs_orig", parent=parent,
                     color=(150, 150, 160), wrap=400)
        dpg.add_text("EN: --", tag="vs_result", parent=parent,
                     color=(150, 150, 160), wrap=400)
        dpg.add_spacer(height=6, parent=parent)
        dpg.add_separator(parent=parent)
        dpg.add_spacer(height=4, parent=parent)

        dpg.add_text("Whisper prompt hint:", parent=parent,
                     color=(150, 150, 160))
        dpg.add_input_text(tag="vs_whisper_prompt",
                           default_value=self._whisper_prompt,
                           hint="e.g.: conversational Russian about programming",
                           width=-1, multiline=True, height=45,
                           parent=parent)
        dpg.add_spacer(height=6, parent=parent)

        sorted_notes = sorted(self._prompts.keys())
        for note in sorted_notes:
            p = self._prompts[note]
            pad_num = p.get("pad", note - PAD_NOTE_OFFSET)
            label = p.get("label", f"Pad {pad_num}")
            system = p.get("system", "")
            with dpg.group(parent=parent):
                with dpg.group(horizontal=True):
                    dpg.add_text(f"Pad {pad_num}:", color=(100, 180, 255))
                    dpg.add_input_text(tag=f"vs_pad_label_{note}",
                                       default_value=label, width=150,
                                       hint="Label")
                if label not in ("Raw", "Clipboard", "Cancel"):
                    dpg.add_input_text(tag=f"vs_pad_system_{note}",
                                       default_value=system, width=-1,
                                       multiline=True, height=55,
                                       hint="System prompt for GPT...")
                else:
                    dpg.add_text(f"  (special: {label})",
                                 color=(150, 150, 160))
                dpg.add_spacer(height=2)

        dpg.add_spacer(height=4, parent=parent)
        dpg.add_button(label="Save All Prompts", parent=parent, width=200,
                       callback=lambda: self._on_save_prompts_clicked())

    # -- Right-panel properties (mic selector, API key, settings) ------------

    def build_properties(self, parent_tag: str) -> None:
        import dearpygui.dearpygui as dpg

        dpg.add_text("Voice Scribe Settings", parent=parent_tag,
                     color=(100, 180, 255))
        dpg.add_separator(parent=parent_tag)
        dpg.add_spacer(height=4, parent=parent_tag)

        dpg.add_text(
            f"Chat: {self.chat_model}  |  Whisper: {self.transcription_model}\n"
            f"Lang: {self.input_language} -> {self.output_language}",
            parent=parent_tag, color=(120, 120, 140),
        )
        dpg.add_spacer(height=6, parent=parent_tag)

        # API key
        key_display = self.api_key[:8] + "..." if self.api_key else ""

        def on_key_submit(sender, app_data):
            self._save_api_key(app_data)
            self._set_status("API key saved", "ok")

        dpg.add_text("API Key:", parent=parent_tag, color=(150, 150, 160))
        with dpg.group(horizontal=True, parent=parent_tag):
            dpg.add_input_text(tag="vs_prop_api_key",
                               default_value=key_display,
                               hint="sk-proj-...", password=True, width=200,
                               on_enter=True, callback=on_key_submit)
            if not self.api_key:
                dpg.add_text("(not set)", color=(255, 100, 80))

        dpg.add_spacer(height=8, parent=parent_tag)

        # Mic selector
        dpg.add_text("Microphone:", parent=parent_tag, color=(150, 150, 160))
        devices = _list_input_devices()
        dev_names = ["(system default)"] + [f"[{i}] {n}" for i, n in devices]
        dev_ids = [None] + [i for i, _ in devices]

        current_name = "(system default)"
        for idx, (dev_id, dev_name) in enumerate(devices):
            if dev_id == self.mic_device:
                current_name = f"[{dev_id}] {dev_name}"
                break

        def on_mic_changed(sender, value):
            sel_idx = dev_names.index(value) if value in dev_names else 0
            self.mic_device = dev_ids[sel_idx] if sel_idx < len(dev_ids) else None
            self._save_mic_device()
            log.info("Mic device set to %s", self.mic_device)

        dpg.add_combo(tag="vs_mic_combo", items=dev_names,
                      default_value=current_name, width=-1,
                      parent=parent_tag, callback=on_mic_changed)

        dpg.add_spacer(height=4, parent=parent_tag)

        # Test mic button
        dpg.add_button(label="Test Mic (2s)", tag="vs_test_mic_btn",
                       parent=parent_tag, width=160,
                       callback=lambda: self._test_mic())
        dpg.add_text("", tag="vs_mic_test_result", parent=parent_tag,
                     color=(150, 150, 160), wrap=260)

    def _test_mic(self):
        """Record 2 seconds and report RMS / peak amplitude."""
        import dearpygui.dearpygui as dpg

        if dpg.does_item_exist("vs_mic_test_result"):
            dpg.set_value("vs_mic_test_result", "Recording 2 seconds...")
            dpg.configure_item("vs_mic_test_result", color=(255, 200, 80))

        def _run():
            try:
                kwargs: dict = dict(samplerate=self.sample_rate, channels=1,
                                    dtype="int16")
                if self.mic_device is not None:
                    kwargs["device"] = self.mic_device
                audio = sd.rec(int(self.sample_rate * 2), **kwargs)
                sd.wait()
                rms = int(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
                peak = int(np.max(np.abs(audio)))
                log.info("Mic test — RMS: %d, peak: %d", rms, peak)

                if dpg.does_item_exist("vs_mic_test_result"):
                    if peak > 500:
                        dpg.set_value("vs_mic_test_result",
                                      f"Mic OK \u2714  RMS={rms}  peak={peak}")
                        dpg.configure_item("vs_mic_test_result",
                                           color=(80, 255, 120))
                    else:
                        dpg.set_value("vs_mic_test_result",
                                      f"No audio detected  RMS={rms}  peak={peak}\n"
                                      "Check mic selection or levels.")
                        dpg.configure_item("vs_mic_test_result",
                                           color=(255, 80, 80))
            except Exception as exc:
                log.error("Mic test failed: %s", exc)
                if dpg.does_item_exist("vs_mic_test_result"):
                    dpg.set_value("vs_mic_test_result", f"Error: {exc}")
                    dpg.configure_item("vs_mic_test_result",
                                       color=(255, 80, 80))

        threading.Thread(target=_run, daemon=True).start()

    # -- prompt save helpers --------------------------------------------------

    def _on_save_prompts_clicked(self) -> None:
        import dearpygui.dearpygui as dpg
        try:
            if dpg.does_item_exist("vs_whisper_prompt"):
                self._whisper_prompt = dpg.get_value("vs_whisper_prompt").strip()
            for note, p in self._prompts.items():
                lt = f"vs_pad_label_{note}"
                st = f"vs_pad_system_{note}"
                if dpg.does_item_exist(lt):
                    p["label"] = dpg.get_value(lt).strip()
                if dpg.does_item_exist(st):
                    p["system"] = dpg.get_value(st).strip()
            self._save_prompts_to_file()
            self._reload_and_refresh_pads()
            self._set_status("Prompts saved!", "ok")
            if self._log_fn:
                self._log_fn("VOICE", "Prompts saved to prompts.toml",
                             color=(80, 255, 120))
        except Exception as exc:
            log.error("Save prompts failed: %s", exc)
            self._set_status(f"Save error: {exc}", "error")

    def _save_api_key(self, key: str) -> None:
        self.api_key = key.strip()
        try:
            _KEY_FILE.write_text(self.api_key, encoding="utf-8")
            log.info("API key saved to %s", _KEY_FILE)
        except OSError as exc:
            log.error("Failed to save API key: %s", exc)

    def _save_prompts_to_file(self) -> None:
        path = self._plugin_dir / "prompts.toml"
        lines = [
            "# Voice Scribe -- style prompts for each pad (1-8).",
            "# Edit system prompts to customize translation style.",
            '# Special labels (no system prompt needed): "Raw", "Clipboard", "Cancel"',
            "",
        ]
        if self._whisper_prompt:
            lines.append(f'whisper_prompt = """{self._whisper_prompt}"""')
            lines.append("")
        for note in sorted(self._prompts.keys()):
            p = self._prompts[note]
            pad = p.get("pad", note - PAD_NOTE_OFFSET)
            label = p.get("label", f"Pad {pad}")
            system = p.get("system", "")
            lines.append("[[prompts]]")
            lines.append(f"pad = {pad}")
            lines.append(f'label = "{label}"')
            if system:
                lines.append(f'system = """{system}"""')
            lines.append("")
        path.write_text("\n".join(lines), encoding="utf-8")
        log.info("Prompts saved to %s", path)

    def _reload_and_refresh_pads(self) -> None:
        self._load_prompts()
        if self._active:
            try:
                from ui.pad_grid import overlay_plugin_pad_labels
                overlay_plugin_pad_labels(self.get_pad_labels())
            except Exception:
                pass
