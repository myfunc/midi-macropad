"""AppCore — initializes and owns all service objects without any UI dependency."""
from __future__ import annotations

import os
import sys
import queue
import threading
import time
import traceback
from pathlib import Path
from dataclasses import asdict

# Ensure project root is on sys.path so existing modules import correctly.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import settings
from midi_listener import MidiListener, MidiEvent
from mapper import Mapper, load_config
from executor import execute_keystroke, execute_shell, execute_launch, execute_scroll
from audio import AudioController
from feedback import FeedbackService, set_transpose, get_transpose
from obs_controller import OBSController
from hotkey_listener import HotkeyListener
from plugins.manager import PluginManager
from pad_registry import PadEntry, PAD_NOTES_ALL

from backend.event_bus import EventBus

CONFIG_PATH = os.path.join(_PROJECT_ROOT, "config.toml")


class AppCore:
    """Headless application core — all business logic, no UI.

    Reuses existing modules (mapper, midi_listener, audio, plugins, etc.)
    exactly as main.py does, but without any DearPyGui dependency.
    """

    def __init__(self):
        self.event_queue: queue.Queue = queue.Queue(maxsize=256)
        self.event_bus = EventBus()
        self._running = False
        self._poll_thread: threading.Thread | None = None
        self._lock = threading.Lock()

        # Log buffer for WebSocket clients connecting later
        self.log_buffer: list[dict] = []
        self._LOG_BUFFER_MAX = 200

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def bootstrap(self) -> None:
        settings.load()

        self.config = load_config(CONFIG_PATH)
        self.mapper = Mapper(self.config)

        self.plugin_manager = PluginManager(
            Path(_PROJECT_ROOT) / "plugins",
            log_fn=self._runtime_log,
        )
        self.feedback = FeedbackService(
            self.config.device_name, log_fn=self._runtime_log,
        )
        self.plugin_manager.set_runtime_services({"feedback": self.feedback})

        self.audio = AudioController(
            output_device_id=settings.get("output_device_id"),
            input_device_id=settings.get("input_device_id"),
            midi_master_cap=settings.get("midi_master_cap", 1.0),
            midi_mic_cap=settings.get("midi_mic_cap", 1.0),
        )

        self.midi = MidiListener(
            self.config.device_name, self.event_queue,
            log_fn=self._midi_log,
        )
        self.hotkeys = HotkeyListener(self.event_queue, log_fn=self._runtime_log)

        obs_cfg = settings.get("obs_session_plugin", {})
        self.obs = OBSController(
            host=obs_cfg.get("host", "127.0.0.1"),
            port=int(obs_cfg.get("port", 4455)),
            password=obs_cfg.get("password", ""),
        )

        # Restore preset
        idx = settings.get("preset_index", 0)
        if 0 <= idx < len(self.config.pad_presets):
            self.mapper.set_preset(idx)

        # Load enabled plugins
        enabled = settings.get("enabled_plugins", [])
        for info in self.plugin_manager.discover():
            if info["name"] in enabled:
                self.plugin_manager.load_plugin(info)

        # Wire OBS into plugin manager
        self.plugin_manager.set_runtime_services({
            "feedback": self.feedback,
            "obs": self.obs,
        })

        self._runtime_log("SYS", "AppCore bootstrapped", (100, 255, 150))

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    def start_services(self) -> None:
        self._running = True
        self.midi.start()
        self.hotkeys.reload_bindings(self.mapper)
        self.hotkeys.start()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        self._runtime_log("SYS", "Services started", (100, 255, 150))

    def shutdown(self) -> None:
        self._running = False
        try:
            self.hotkeys.stop()
        except Exception:
            pass
        try:
            self.plugin_manager.unload_all()
        except Exception:
            pass
        try:
            self.feedback.close()
        except Exception:
            pass
        try:
            self.midi.stop()
        except Exception:
            pass
        try:
            settings.flush()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Event poll loop (replaces DearPyGui render loop)
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        # Small delay to let uvicorn start cleanly
        import time as _time
        _time.sleep(1.0)

        while self._running:
            # Drain MIDI events
            try:
                event = self.event_queue.get(timeout=0.05)
                self._handle_event(event)
            except queue.Empty:
                pass

            # Poll plugins (~20 fps) — wrapped in try/except
            # because some plugins use native DLLs (Voicemeeter)
            try:
                self.plugin_manager.poll_all()
            except Exception as exc:
                self._runtime_log("ERR", f"Plugin poll error: {exc}", (255, 80, 80))
                _time.sleep(0.5)  # Back off on error

    def _handle_event(self, event: MidiEvent) -> None:
        try:
            if event.type == "pad_press":
                self.event_bus.publish("midi.pad_press", {
                    "note": event.note, "velocity": event.velocity,
                })
                # Plugins first
                if self.plugin_manager.on_pad_press(event.note, event.velocity):
                    labels = self.plugin_manager.get_all_pad_labels()
                    label = labels.get(event.note, f"note {event.note}")
                    self._runtime_log("PAD",
                        f"{label} (note {event.note}, vel {event.velocity}) [plugin]",
                        (200, 150, 255))
                else:
                    mapping = self.mapper.lookup_pad(event.note)
                    if mapping:
                        self._runtime_log("PAD",
                            f"{mapping.label} (note {event.note}, vel {event.velocity})",
                            (100, 200, 255))
                        self._execute_action(mapping.action)
                    else:
                        self._runtime_log("PAD",
                            f"note {event.note} (unmapped)", (150, 150, 160))

            elif event.type == "pad_release":
                self.event_bus.publish("midi.pad_release", {"note": event.note})
                self.plugin_manager.on_pad_release(event.note)

            elif event.type == "knob":
                self.event_bus.publish("midi.knob", {
                    "cc": event.cc, "value": event.value,
                })
                if not self.plugin_manager.on_knob(event.cc, event.value):
                    knob = self.mapper.lookup_knob(event.cc)
                    if knob:
                        self._runtime_log("KNOB",
                            f"{knob.label} CC{event.cc}={event.value}",
                            (255, 200, 80))
                        self._handle_knob(knob, event.value)

            elif event.type == "pitch_bend":
                self.event_bus.publish("midi.pitch_bend", {"pitch": event.pitch})
                self.plugin_manager.on_pitch_bend(event.pitch)

        except Exception as exc:
            self._runtime_log("ERR", f"Event error: {exc}", (255, 80, 80))

    # ------------------------------------------------------------------
    # Action execution (mirrors main.py _execute_action)
    # ------------------------------------------------------------------

    def _execute_action(self, action) -> None:
        try:
            if action.type == "keystroke":
                execute_keystroke(action.keys)
            elif action.type == "app_keystroke":
                execute_keystroke(action.keys, process=action.process)
            elif action.type == "shell":
                execute_shell(action.command)
            elif action.type == "launch":
                execute_launch(action.command)
            elif action.type == "volume":
                pass  # Volume handled by knob handler
            elif action.type == "scroll":
                execute_scroll(action.keys)
        except Exception as exc:
            self._runtime_log("ERR", f"Action failed: {exc}", (255, 80, 80))

    def _handle_knob(self, knob, value: int) -> None:
        action = knob.action
        if action.type == "volume":
            level = value / 127.0
            if action.target == "master":
                self.audio.set_master_volume(level)
            elif action.target == "mic":
                self.audio.set_mic_volume(level)
            elif action.target == "spotify":
                self.audio.set_app_volume("spotify", level)
            elif action.target == "foreground":
                self.audio.set_foreground_volume(level)
            self.event_bus.publish("audio.level", {
                "target": action.target, "level": level,
            })

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def _runtime_log(self, tag: str, message: str, color=(200, 200, 200)) -> None:
        entry = {"tag": tag, "message": message, "color": list(color), "ts": time.time()}
        with self._lock:
            self.log_buffer.append(entry)
            if len(self.log_buffer) > self._LOG_BUFFER_MAX:
                self.log_buffer = self.log_buffer[-self._LOG_BUFFER_MAX:]
        self.event_bus.publish("log.entry", entry)

    def _midi_log(self, level: str, message: str) -> None:
        color = (
            (255, 80, 80) if level == "error"
            else (255, 180, 80) if level == "warning"
            else (100, 255, 150)
        )
        self._runtime_log("MIDI", message, color)

    # ------------------------------------------------------------------
    # State snapshot (for new WebSocket clients)
    # ------------------------------------------------------------------

    def get_state_snapshot(self) -> dict:
        """Return full state for handshake response."""
        preset = self.mapper.current_preset
        presets = [
            {"index": i, "name": p.name}
            for i, p in enumerate(self.config.pad_presets)
        ]

        # Pads from registry
        pads = {}
        for note, entry in self.mapper.registry.get_all().items():
            pads[str(note)] = {
                "note": entry.note,
                "label": entry.label,
                "source": entry.source,
                "action_type": entry.action_type,
                "action_data": entry.action_data,
                "hotkey": entry.hotkey,
                "locked": entry.locked,
                "color": list(entry.color),
            }

        # Plugin pad labels overlay
        plugin_labels = self.plugin_manager.get_all_pad_labels()
        for note_str, label in {str(k): v for k, v in plugin_labels.items()}.items():
            if note_str in pads:
                pads[note_str]["label"] = label

        # Knobs
        knobs = []
        for k in self.config.knobs:
            knobs.append({
                "cc": k.cc, "label": k.label,
                "action": {"type": k.action.type, "target": k.action.target},
                "value": settings.get("knob_values", {}).get(str(k.cc), 64),
            })

        # Plugins
        discovered = []
        for info in self.plugin_manager.discover():
            discovered.append({
                "name": info["name"],
                "version": info.get("version", ""),
                "description": info.get("description", ""),
                "enabled": info["name"] in self.plugin_manager.enabled,
            })

        return {
            "midi": {
                "connected": self.midi.connected,
                "port_name": self.midi.port_name,
                "device_name": self.config.device_name,
            },
            "presets": {
                "current_index": self.mapper.current_preset_index,
                "list": presets,
            },
            "pads": pads,
            "knobs": knobs,
            "plugins": {"discovered": discovered},
            "obs": {
                "connected": self.obs.connected,
                "current_scene": self.obs.current_scene,
                "is_recording": self.obs.is_recording,
                "is_streaming": self.obs.is_streaming,
                "is_replay_buffer_active": self.obs.is_replay_buffer_active,
                "scenes": self.obs.scene_names,
            },
            "logs": self.log_buffer[-50:],
        }
