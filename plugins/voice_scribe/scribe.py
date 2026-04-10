"""Voice Scribe plugin — speak Russian, type English with configurable style prompts."""

import os
import io
import json
import threading
import time
import wave
import ctypes
import ctypes.wintypes
from datetime import datetime

from base import Plugin

import numpy as np
import sounddevice as sd
import toml
from pathlib import Path
from logger import get_logger

log = get_logger("voice_scribe")

MODE_NAME = "Voice Scribe"
PAD_NOTE_OFFSET = 15
CHAT_SYSTEM = (
    "You are a voice-driven writing assistant. The user builds context by providing "
    "text snippets from conversations, posts, or documents (marked as [Context N:]), "
    "then gives a voice instruction in Russian (marked as [Instruction:]).\n\n"
    "Rules:\n"
    "- Output ONLY the final text, ready to paste and send. No explanations, no commentary.\n"
    "- The user may specify target language, style, and tone in their voice instruction.\n"
    "- If no language is specified, infer from context (reply language matches the conversation).\n"
    "- Generate a natural response/message as if the user wrote it themselves.\n"
    "- In follow-up turns, remember all previous context and responses.\n\n"
    "Formatting:\n"
    "- Wrap code, technical terms, file names, and commands in backticks (`like this`).\n"
    "- Use minimal markdown where appropriate (bold for emphasis, lists if needed).\n"
    "- Use emojis sparingly — only when the tone is genuinely enthusiastic, celebratory, "
    "or to soften an inability to help (e.g. 🎉, 🙏, 🤔). Never force them."
)
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"
_KEY_FILE = Path(__file__).parent / ".api_key"  # legacy; vault is primary
_VAULT_NS = "voice_scribe"
_VAULT_KEY = "openai_api_key"

# Imported lazily at use time to avoid import cycles during module load
_vault = None


def _get_vault():
    global _vault
    if _vault is None:
        from secrets_store import vault as _v
        _vault = _v
    return _vault


class OperationCancelled(Exception):
    """Raised when a voice job was hard-cancelled or superseded."""


def _load_api_key_from_files() -> str:
    """Resolve the OpenAI API key from the vault, with legacy fallbacks.

    Order: vault → legacy .api_key file → .env (OPENAI_API_KEY) → os.environ.
    If the key is found in a legacy location, it is written to the vault so
    subsequent runs use the unified store.
    """
    vault = _get_vault()
    key = vault.get(_VAULT_NS, _VAULT_KEY, default="") or ""
    if key:
        return key

    # Legacy: per-plugin file
    if _KEY_FILE.exists():
        legacy = _KEY_FILE.read_text(encoding="utf-8").strip()
        if legacy:
            vault.set(_VAULT_NS, _VAULT_KEY, legacy)
            log.info("Migrated OpenAI API key from %s to secrets vault", _KEY_FILE)
            return legacy

    # Legacy: .env file at project root
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OPENAI_API_KEY="):
                env_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                if env_key:
                    # Keep .env intact — do not migrate automatically so other
                    # tools relying on it keep working. Vault reads it as a
                    # fallback on its own.
                    return env_key

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
        self._prompt_list: list[dict] = []
        self._owned_note_list: list[int] | None = None
        self._plugin_dir: Path = Path(__file__).parent

        self._active: bool = False
        self._recording: bool = False
        self._active_note: int | None = None
        self._audio_chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._processing_token: int | None = None
        self._token_counter: int = 0

        self._status: str = "Idle"
        self._last_original: str = ""
        self._last_result: str = ""
        self._last_prompt_label: str = ""
        self._whisper_prompt: str = ""
        self._pending_context: list[str] = []
        self._chat_history: list[dict] = []
        self._chat_file: Path | None = None

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
        self._hard_cancel(set_status=False)

    def on_mode_changed(self, mode_name: str) -> None:
        self._active = mode_name == MODE_NAME
        if not self._active:
            self._hard_cancel(set_status=False)

    def set_owned_notes(self, notes: set[int]) -> None:
        self._active = bool(notes)
        self._owned_note_list = sorted(notes)
        if self._prompt_list:
            self._remap_prompt_notes()

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
            self._prompt_list = []
            self._prompts.clear()
            return
        data = toml.load(str(path))
        self._whisper_prompt = data.get("whisper_prompt", "")
        self._prompt_list = list(data.get("prompts", []))
        self._prompt_list.sort(key=lambda x: int(x["pad"]))
        self._remap_prompt_notes()
        log.info("Loaded %d prompt pads", len(self._prompts))

    def _remap_prompt_notes(self) -> None:
        self._prompts.clear()
        for i, p in enumerate(self._prompt_list):
            if self._owned_note_list is not None and i < len(self._owned_note_list):
                note = self._owned_note_list[i]
            else:
                note = int(p["pad"]) + PAD_NOTE_OFFSET
            p["_note"] = note
            self._prompts[note] = p

    # -- MIDI hooks -----------------------------------------------------------

    def on_pad_press(self, note: int, velocity: int) -> bool:
        if not self._active or note not in self._prompts:
            return False
        info = self._prompts[note]
        label = info.get("label", "")

        if label == "Cancel":
            self._hard_cancel()
            return True

        if label == "New Chat":
            self._stop_recording()
            self._chat_history = [{"role": "system", "content": CHAT_SYSTEM}]
            self._chat_file = self._new_chat_file()
            self._pending_context.clear()
            self._set_status("New chat started", "ok")
            log.info("New chat started → %s", self._chat_file.name)
            return True

        if label == "Context":
            if not self._is_processing():
                text = self._capture_selection()
                if text.strip():
                    self._pending_context.append(text)
                    self.emit_feedback("voice.context_added")
                    self._set_status(
                        f"Context added ({len(self._pending_context)} items)", "ok")
                    log.info("Context +1 (%d total), %d chars",
                             len(self._pending_context), len(text))
                else:
                    self.emit_feedback("voice.warn")
                    self._set_status("No text selected", "warn")
            return True

        if label == "Speak":
            if self._recording and self._active_note == note:
                self._stop_recording()
                self.emit_feedback("voice.record_stop")
                token = self._begin_processing()
                if token is not None:
                    threading.Thread(target=self._process_speak, args=(note, token),
                                     daemon=True).start()
            elif not self._recording and not self._is_processing():
                selection = self._capture_selection()
                if selection.strip():
                    self._pending_context.append(selection)
                    self.emit_feedback("voice.context_added")
                self._active_note = note
                self._start_recording()
                if self._recording:
                    self.emit_feedback("voice.record_start")
                ctx_count = len(self._pending_context)
                self._set_status(
                    f"Recording [Speak] ({ctx_count} ctx)...", "recording")
            return True

        if self._recording and self._active_note == note:
            self._stop_recording()
            self.emit_feedback("voice.record_stop")
            token = self._begin_processing()
            if token is not None:
                threading.Thread(target=self._process_audio, args=(note, token),
                                 daemon=True).start()
        elif not self._recording and not self._is_processing():
            self._active_note = note
            self._start_recording()
            if self._recording:
                self.emit_feedback("voice.record_start")
            self._set_status(f"Recording [{label}]", "recording")
        return True

    def on_pad_release(self, note: int) -> bool:
        if not self._active or note not in self._prompts:
            return False
        return self._recording and self._active_note == note

    def get_action_catalog(self) -> list[dict]:
        catalog = []
        for p in self._prompt_list:
            label = p.get("label", f"Pad {p.get('pad', '?')}")
            system = p.get("system", "")
            desc = system[:80] + "..." if len(system) > 80 else system
            catalog.append({
                "id": label,
                "label": label,
                "description": desc or f"Voice prompt: {label}",
            })
        return catalog

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
            "busy" if self._is_processing() else "idle",
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
            self._log("VOICE", f"Mic start failed: {exc}", color=(255, 80, 80), level="error")

    def _stop_recording(self) -> None:
        self._recording = False
        self._active_note = None
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

    def _is_processing(self) -> bool:
        with self._state_lock:
            return self._processing_token is not None

    def _begin_processing(self) -> int | None:
        with self._state_lock:
            if self._processing_token is not None:
                return None
            self._token_counter += 1
            self._processing_token = self._token_counter
            return self._processing_token

    def _finish_processing(self, token: int) -> None:
        with self._state_lock:
            if self._processing_token == token:
                self._processing_token = None

    def _assert_token_active(self, token: int) -> None:
        with self._state_lock:
            if self._processing_token != token:
                raise OperationCancelled()

    def _is_token_active(self, token: int) -> bool:
        with self._state_lock:
            return self._processing_token == token

    def _hard_cancel(self, set_status: bool = True) -> None:
        had_activity = self._recording or self._is_processing()
        self._stop_recording()
        self._pending_context.clear()
        with self._state_lock:
            self._token_counter += 1
            self._processing_token = None
        if set_status:
            if had_activity:
                self.emit_feedback("voice.cancel_requested")
                self.emit_feedback("voice.cancelled")
                self._set_status("Hard cancelled", "warn")
            else:
                self.emit_feedback("voice.warn")
                self._set_status("Nothing to cancel", "warn")

    def _set_status_if_active(self, token: int, text: str, kind: str) -> None:
        self._assert_token_active(token)
        self._set_status(text, kind)

    def _paste_if_active(self, token: int, text: str) -> None:
        self._assert_token_active(token)
        self._paste(text)

    def _process_audio(self, note: int, token: int) -> None:
        try:
            self._assert_token_active(token)
            self.emit_feedback("voice.processing_start")
            self._set_status_if_active(token, "Transcribing...", "busy")
            wav = self._collect_wav()
            self._assert_token_active(token)
            if not wav:
                self.emit_feedback("voice.warn")
                self._set_status_if_active(token, "No audio captured", "warn")
                return
            transcript = self._whisper_transcribe(wav)
            self._assert_token_active(token)
            if not transcript:
                self.emit_feedback("voice.warn")
                self._set_status_if_active(token, "Empty transcription", "warn")
                return
            self._last_original = transcript
            info = self._prompts.get(note, {})
            label = info.get("label", "")
            system = info.get("system", "")
            self._last_prompt_label = label
            log.info("Transcript [%s]: %s", label, transcript[:120])

            if not system:
                self._paste_if_active(token, transcript)
                self._last_result = transcript
                self._assert_token_active(token)
                self.emit_feedback("voice.done")
                self._set_status_if_active(token, f"Done [{label or 'raw'}]", "ok")
                return

            self._set_status_if_active(token, f"Translating [{label}]...", "busy")
            translated = self._gpt_translate(transcript, system)
            self._assert_token_active(token)
            if translated:
                self._paste_if_active(token, translated)
                self._last_result = translated
                self._assert_token_active(token)
                self.emit_feedback("voice.done")
                self._set_status_if_active(token, f"Done [{label}]", "ok")
            else:
                self.emit_feedback("voice.warn")
                self._set_status_if_active(token, "Translation returned empty", "warn")
        except OperationCancelled:
            log.info("Voice job cancelled before completion")
        except Exception as exc:
            self._log("VOICE", f"Processing error: {exc}", color=(255, 80, 80), level="error")
            if self._is_token_active(token):
                self.emit_feedback("voice.error")
                self._set_status(f"Error: {exc}", "error")
        finally:
            self._finish_processing(token)

    # -- speak pipeline --------------------------------------------------------

    @staticmethod
    def _capture_selection() -> str:
        """Send Ctrl+C to copy the current selection, then read clipboard."""
        from pynput.keyboard import Key, Controller
        kb = Controller()
        kb.press(Key.ctrl_l)
        kb.press("c")
        kb.release("c")
        kb.release(Key.ctrl_l)
        time.sleep(0.15)
        return _clipboard_read()

    def _process_speak(self, note: int, token: int) -> None:
        try:
            self._assert_token_active(token)
            self.emit_feedback("voice.processing_start")
            self._set_status_if_active(token, "Transcribing...", "busy")
            wav = self._collect_wav()
            self._assert_token_active(token)
            if not wav:
                self.emit_feedback("voice.warn")
                self._set_status_if_active(token, "No audio captured", "warn")
                return
            instruction = self._whisper_transcribe(wav)
            self._assert_token_active(token)
            if not instruction:
                self.emit_feedback("voice.warn")
                self._set_status_if_active(token, "Empty transcription", "warn")
                return

            if not self._chat_history:
                self._chat_history = [{"role": "system", "content": CHAT_SYSTEM}]
                self._chat_file = self._new_chat_file()

            parts = []
            for i, ctx in enumerate(self._pending_context, 1):
                parts.append(f"[Context {i}:]\n{ctx}")
            parts.append(f"[Instruction:]\n{instruction}")
            user_msg = "\n\n".join(parts)

            self._last_original = instruction
            self._last_prompt_label = "Speak"
            log.info("Speak — instruction: %s (%d ctx, %d history msgs)",
                     instruction[:80], len(parts) - 1, len(self._chat_history))

            self._set_status_if_active(token, "Generating...", "busy")
            request_messages = list(self._chat_history)
            request_messages.append({"role": "user", "content": user_msg})
            self._pending_context.clear()
            result = self._gpt_chat(request_messages)
            self._assert_token_active(token)
            if result:
                self._chat_history = request_messages + [{"role": "assistant", "content": result}]
                self._save_chat_log()
                self._paste_if_active(token, result)
                self._last_result = result
                self._assert_token_active(token)
                self.emit_feedback("voice.done")
                self._set_status_if_active(token, "Done [Speak]", "ok")
            else:
                self._chat_history.pop()
                self.emit_feedback("voice.warn")
                self._set_status_if_active(token, "Generation returned empty", "warn")
        except OperationCancelled:
            log.info("Voice speak job cancelled before completion")
        except Exception as exc:
            self._log("VOICE", f"Speak error: {exc}", color=(255, 80, 80), level="error")
            if self._is_token_active(token):
                self.emit_feedback("voice.error")
                self._set_status(f"Error: {exc}", "error")
        finally:
            self._finish_processing(token)

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

    def _gpt_chat(self, messages: list[dict]) -> str:
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key)
        resp = client.chat.completions.create(
            model=self.chat_model,
            messages=messages,
            temperature=0.3,
        )
        return resp.choices[0].message.content.strip()

    # -- chat log persistence -------------------------------------------------

    def _new_chat_file(self) -> Path:
        chats_dir = self._plugin_dir / "chats"
        chats_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        return chats_dir / f"{stamp}.json"

    def _save_chat_log(self) -> None:
        if not self._chat_file or not self._chat_history:
            return
        try:
            self._chat_file.write_text(
                json.dumps(self._chat_history, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            log.error("Failed to save chat log: %s", exc)

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
        if os.environ.get("MACROPAD_HEADLESS"):
            return
        try:
            import dearpygui.dearpygui as dpg
            if dpg.does_item_exist("vs_status"):
                dpg.set_value("vs_status", f"Status: {text}")
                dpg.configure_item("vs_status", color=color)
            if dpg.does_item_exist("vs_active_prompt") and self._last_prompt_label:
                dpg.set_value("vs_active_prompt",
                              f"Prompt: {self._last_prompt_label}")
            if self._last_original and dpg.does_item_exist("vs_orig"):
                dpg.set_value("vs_orig", self._last_original)
            if self._last_result and dpg.does_item_exist("vs_result"):
                dpg.set_value("vs_result", self._last_result)
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

        editable_prompt_targets = []
        for note in sorted(self._prompts.keys()):
            prompt = self._prompts[note]
            label = prompt.get("label", "")
            if label in ("New Chat", "Context", "Speak", "Cancel"):
                continue
            pad_num = prompt.get("pad", note - PAD_NOTE_OFFSET)
            editable_prompt_targets.append((f"Pad {pad_num} - {label}", note))

        dpg.add_text("Status: Idle", tag="vs_status", parent=parent,
                     color=(150, 150, 160))
        dpg.add_text("Prompt: --", tag="vs_active_prompt",
                     parent=parent, color=(120, 120, 140), wrap=400)
        dpg.add_text("Last transcript:", parent=parent,
                     color=(150, 150, 160))
        dpg.add_input_text(tag="vs_orig",
                           default_value=self._last_original,
                           readonly=True,
                           width=-1,
                           multiline=True,
                           height=65,
                           parent=parent)
        dpg.add_text("Last result:", parent=parent,
                     color=(150, 150, 160))
        dpg.add_input_text(tag="vs_result",
                           default_value=self._last_result,
                           readonly=True,
                           width=-1,
                           multiline=True,
                           height=90,
                           parent=parent)
        with dpg.group(horizontal=True, parent=parent):
            dpg.add_button(label="Copy Result", width=120,
                           callback=lambda: self._copy_last_result_to_clipboard())
            dpg.add_button(label="Use as Whisper Hint", width=150,
                           callback=lambda: self._load_last_result_into_whisper())
        if editable_prompt_targets:
            combo_items = [title for title, _ in editable_prompt_targets]
            with dpg.group(horizontal=True, parent=parent):
                dpg.add_combo(tag="vs_result_target_prompt",
                              items=combo_items,
                              default_value=combo_items[0],
                              width=220)
                dpg.add_button(label="Use in Selected Prompt", width=170,
                               callback=lambda: self._load_last_result_into_prompt())
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
                if label not in ("New Chat", "Context", "Speak", "Cancel"):
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
                self._log("VOICE", f"Mic test failed: {exc}", color=(255, 80, 80), level="error")
                if dpg.does_item_exist("vs_mic_test_result"):
                    dpg.set_value("vs_mic_test_result", f"Error: {exc}")
                    dpg.configure_item("vs_mic_test_result",
                                       color=(255, 80, 80))

        threading.Thread(target=_run, daemon=True).start()

    # -- prompt save helpers --------------------------------------------------

    def _copy_last_result_to_clipboard(self) -> None:
        if not self._last_result.strip():
            self._set_status("No result to copy yet", "warn")
            return
        try:
            _clipboard_write(self._last_result)
            self._set_status("Result copied to clipboard", "ok")
        except OSError as exc:
            self._set_status(f"Clipboard error: {exc}", "error")

    def _load_last_result_into_whisper(self) -> None:
        import dearpygui.dearpygui as dpg
        if not self._last_result.strip():
            self._set_status("No result to insert yet", "warn")
            return
        if dpg.does_item_exist("vs_whisper_prompt"):
            dpg.set_value("vs_whisper_prompt", self._last_result)
            self._set_status("Result loaded into Whisper hint", "ok")

    def _load_last_result_into_prompt(self) -> None:
        import dearpygui.dearpygui as dpg
        if not self._last_result.strip():
            self._set_status("No result to insert yet", "warn")
            return
        if not dpg.does_item_exist("vs_result_target_prompt"):
            self._set_status("No editable prompt target", "warn")
            return
        selected = dpg.get_value("vs_result_target_prompt")
        for note in sorted(self._prompts.keys()):
            prompt = self._prompts[note]
            label = prompt.get("label", "")
            if label in ("New Chat", "Context", "Speak", "Cancel"):
                continue
            pad_num = prompt.get("pad", note - PAD_NOTE_OFFSET)
            target_name = f"Pad {pad_num} - {label}"
            if target_name == selected:
                field_tag = f"vs_pad_system_{note}"
                if dpg.does_item_exist(field_tag):
                    dpg.set_value(field_tag, self._last_result)
                    self._set_status(f"Result loaded into Pad {pad_num}", "ok")
                    return
        self._set_status("Prompt target not found", "warn")

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
            _get_vault().set(_VAULT_NS, _VAULT_KEY, self.api_key)
            log.info("API key saved to secrets vault")
        except OSError as exc:
            log.error("Failed to save API key: %s", exc)

    def _save_prompts_to_file(self) -> None:
        path = self._plugin_dir / "prompts.toml"
        lines = [
            "# Voice Scribe -- style prompts for each pad (1-8).",
            "# Edit system prompts to customize translation style.",
            '# Special labels (no system prompt needed): "New Chat", "Context", "Speak", "Cancel"',
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
        # Web UI refreshes pad labels via WebSocket events; no direct UI call needed.
