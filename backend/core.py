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
from mapper import (
    Mapper, load_config, save_config,
    PAD_NOTES_BANK_A, PAD_NOTES_BANK_B, PAD_NOTES_ALL,
    PIANO_MAP_NOTE_MIN, PIANO_MAP_NOTE_MAX,
    PianoPreset, PianoKeyMapping,
)
from executor import execute_keystroke, execute_shell, execute_launch, execute_scroll
from audio import AudioController
from feedback import FeedbackService, set_transpose, get_transpose
from obs_controller import OBSController
from hotkey_listener import HotkeyListener
from plugins.manager import PluginManager
from pad_registry import PadEntry, pad_key

from backend.event_bus import EventBus
from backend.operation_manager import OperationManager
from backend.telemetry import TelemetryAggregator

CONFIG_PATH = os.path.join(_PROJECT_ROOT, "config.toml")

import re

_PRESET_NAME_RE = re.compile(r'^[\w\s\-.,!?()&#+]{1,50}$', re.UNICODE)
# Legacy fixed IDs kept for backward compatibility; dynamic IDs also accepted
_LEGACY_PANEL_IDS = frozenset({"bankA", "bankB", "knobs"})

# Valid bank values per panel type. Kept at module scope so other modules (and
# tests) can introspect without constructing an AppCore instance.
VALID_BANKS: dict[str, tuple[str, ...]] = {
    "pad": ("A", "B"),
    "knob": ("A", "B"),
    "piano": ("play", "map"),
}

# Title templates recognized by ``reconcile_panels(fix_titles=True)`` and by
# PanelHeader's auto-sync logic. Custom titles (anything not matching) are
# preserved as-is when banks change.
_TEMPLATE_TITLE_RE = re.compile(
    r'^(?:Pad Panel [AB]|Knob Panel [AB]|Piano \((?:Play|Map)\))$'
)


def _template_title(panel_type: str, bank: str) -> str:
    """Return the canonical template title for (type, bank)."""
    if panel_type == "pad":
        return f"Pad Panel {bank}"
    if panel_type == "knob":
        return f"Knob Panel {bank}"
    if panel_type == "piano":
        return "Piano (Map)" if bank == "map" else "Piano (Play)"
    return f"{panel_type} {bank}"


def _validate_preset_name(name: str) -> str | None:
    """Validate preset name. Returns error message or None if valid."""
    if not isinstance(name, str) or not name.strip():
        return "Имя пресета не может быть пустым"
    name = name.strip()
    if len(name) > 50:
        return "Имя пресета не может быть длиннее 50 символов"
    if any(ord(ch) < 32 for ch in name):
        return "Имя пресета содержит управляющие символы"
    if not _PRESET_NAME_RE.match(name):
        return "Имя пресета содержит недопустимые символы"
    return None


def _validate_params(params: dict) -> str | None:
    """Validate knob action params. Returns error message or None if valid."""
    if not isinstance(params, dict):
        return "params должен быть словарём"
    if len(params) > 20:
        return "params не может содержать более 20 ключей"
    for key, value in params.items():
        if not isinstance(key, str):
            return f"Ключ params должен быть строкой, получен {type(key).__name__}"
        if isinstance(value, (dict, list, tuple, set)):
            return f"Значение params['{key}'] не может быть вложенным объектом"
        if not isinstance(value, (str, int, float, bool)):
            return f"Значение params['{key}'] должно быть str/int/float, получен {type(value).__name__}"
    return None


class AppCore:
    """Headless application core — all business logic, no UI.

    Reuses existing modules (mapper, midi_listener, audio, plugins, etc.)
    exactly as main.py does, but without any DearPyGui dependency.
    """

    def __init__(self):
        self.event_queue: queue.Queue = queue.Queue(maxsize=256)
        self.event_bus = EventBus()
        self.op_manager = OperationManager(self.event_bus)
        self._running = False
        self._poll_thread: threading.Thread | None = None
        self._lock = threading.RLock()  # RLock to allow nested locking

        # Log buffer for WebSocket clients connecting later
        self.log_buffer: list[dict] = []
        self._LOG_BUFFER_MAX = 200

        # In-memory marker for the last physically-pressed pad bank.
        # Takes priority over settings["panel_presets"]["_active_pad_bank"]
        # when resolving the active knob panel — avoids writing settings on
        # every pad press.
        self._active_pad_bank_mem: str | None = None

        # Session telemetry — observer on event_bus, dumped on shutdown
        self.telemetry = TelemetryAggregator(Path(_PROJECT_ROOT))
        self.event_bus.subscribe_sync(self.telemetry.on_event)

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
            mode=settings.get("feedback_mode", "midi"),
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
        self.hotkeys = HotkeyListener(
            self.event_queue,
            log_fn=self._runtime_log,
            action_callback=self._execute_hotkey_action,
        )

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

        # Migrate: create panel_presets if missing
        self._migrate_panel_presets()

        # Restore MIDI routing from settings
        self._restore_midi_routing()

        # Reconcile active-panel slots against live panels (fix drift caused
        # by earlier bugs / crashed writes). Titles are left alone here — the
        # user can explicitly request a title-fix pass via the REST endpoint.
        try:
            rep = self.reconcile_panels(fix_titles=False)
            for msg in rep.get("fixed", []):
                self._runtime_log("SYS", f"[reconcile] {msg}", (100, 255, 150))
            for msg in rep.get("warnings", []):
                self._runtime_log("SYS", f"[reconcile] {msg}", (255, 200, 80))
        except Exception as exc:
            self._runtime_log("ERR",
                f"reconcile_panels failed: {exc}", (255, 80, 80))

        # Mark headless mode so plugins skip DearPyGui calls
        os.environ["MACROPAD_HEADLESS"] = "1"

        # Load enabled plugins (skip plugins that crash without DearPyGui)
        enabled = settings.get("enabled_plugins", [])
        _SKIP_IN_HEADLESS = set()  # Add plugin names here if they crash
        for info in self.plugin_manager.discover():
            name = info["name"]
            if name in enabled and name not in _SKIP_IN_HEADLESS:
                try:
                    self.plugin_manager.load_plugin(info)
                except Exception as exc:
                    self._runtime_log("ERR",
                        f"Plugin '{name}' failed to load: {exc}", (255, 80, 80))

        # Wire OBS into plugin manager
        self.plugin_manager.set_runtime_services({
            "feedback": self.feedback,
            "obs": self.obs,
        })

        # Notify plugins about current preset so they activate correctly
        try:
            preset = self.mapper.current_preset
            if preset:
                self.plugin_manager.on_mode_changed(preset.name)
                self.plugin_manager.notify_preset_changed(self.mapper)
        except Exception as exc:
            self._runtime_log("ERR", f"Initial mode sync failed: {exc}", (255, 80, 80))

        self.telemetry.set_plugins_enabled(list(self.plugin_manager.enabled))
        self._runtime_log("SYS", "AppCore bootstrapped", (100, 255, 150))

    def _restore_midi_routing(self) -> None:
        """Restore mapper state from saved freeform ``panels`` + ``active_panels``.

        Also keeps the legacy routing tables populated for backward compat
        with any callers that still read them.
        """
        panels = settings.get("panels") or {}
        active = settings.get("active_panels") or {}

        # Freeform registration
        for instance_id, panel in panels.items():
            if not isinstance(panel, dict):
                continue
            preset_name = panel.get("preset")
            if preset_name:
                self.mapper.register_panel_preset(instance_id, preset_name)

        for key, instance_id in active.items():
            if not instance_id:
                continue
            try:
                panel_type, bank = key.split(":", 1)
            except ValueError:
                continue
            self.mapper.set_active_panel(panel_type, bank, instance_id)

        # Legacy mirror so existing fallbacks keep working
        for (panel_type, bank), instance_id in self.mapper._active_panels.items():
            preset_name = self.mapper._panel_presets.get(instance_id)
            if not preset_name:
                continue
            if panel_type == "pad":
                routing_key = "bankA" if bank == "A" else "bankB"
                self.mapper.set_midi_routing(routing_key, preset_name)
            elif panel_type == "knob":
                legacy = "knobBank-A" if bank == "A" else "knobBank-B"
                self.mapper.set_knob_routing(legacy, preset_name)

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
            path = self.telemetry.dump()
            self._runtime_log(
                "SYS", f"Telemetry dumped to {path.name}", (100, 255, 150))
        except Exception as exc:
            self._runtime_log("ERR", f"Telemetry dump failed: {exc}", (255, 80, 80))
        try:
            self.op_manager.shutdown()
        except Exception:
            pass
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
        import time as _time
        _time.sleep(1.0)

        # In headless mode, skip polling entirely — plugins that use
        # DearPyGui or native DLLs crash when polled from background threads.
        # MIDI pad press/release still works via _handle_event.
        _SKIP_ALL_POLLS = True

        while self._running:
            # Drain MIDI events
            try:
                event = self.event_queue.get(timeout=0.05)
                self._handle_event(event)
            except queue.Empty:
                pass

            # Plugin poll — disabled in headless mode (DearPyGui/DLL crashes)
            # MIDI events and pad actions still work via on_pad_press
            if not _SKIP_ALL_POLLS:
                try:
                    self.plugin_manager.poll_all()
                except Exception:
                    pass

    def _handle_event(self, event: MidiEvent) -> None:
        with self._lock:
            self._handle_event_locked(event)

    def _handle_event_locked(self, event: MidiEvent) -> None:
        try:
            if event.type == "pad_press":
                note = event.note
                # Split: pad notes -> pad dispatch, other notes -> piano dispatch
                if note in PAD_NOTES_ALL:
                    self._handle_pad_press(note, event.velocity)
                else:
                    # Piano / non-pad note — route through piano dispatcher
                    self.event_bus.publish("midi.key_press", {
                        "note": note, "velocity": event.velocity,
                    })
                    handled = self.handle_piano_note(
                        note, event.velocity, on=True)
                    if not handled:
                        # Legacy fallback: let plugins (incl. Piano) handle it.
                        self.plugin_manager.on_pad_press(note, event.velocity)

            elif event.type == "pad_release":
                note = event.note
                if note in PAD_NOTES_ALL:
                    self.event_bus.publish("midi.pad_release", {"note": note})
                    self.plugin_manager.on_pad_release(note)
                else:
                    self.event_bus.publish("midi.key_release", {"note": note})
                    handled = self.handle_piano_note(note, 0, on=False)
                    if not handled:
                        self.plugin_manager.on_pad_release(note)

            elif event.type == "knob":
                self.event_bus.publish("midi.knob", {
                    "cc": event.cc, "value": event.value,
                })
                if not self.plugin_manager.on_knob(event.cc, event.value):
                    # Freeform model: active knob-panel per bank.
                    bank = self._active_pad_bank_mem or "A"
                    knob = self.mapper.lookup_knob_for_active_bank(bank, event.cc)
                    if knob:
                        self._runtime_log("KNOB",
                            f"[knob:{bank}] {knob.label} CC{event.cc}={event.value}",
                            (255, 200, 80))
                        self._handle_knob(knob, event.value)

            elif event.type == "pitch_bend":
                self.event_bus.publish("midi.pitch_bend", {"pitch": event.pitch})
                self.plugin_manager.on_pitch_bend(event.pitch)

        except Exception as exc:
            self._runtime_log("ERR", f"Event error: {exc}", (255, 80, 80))

    def _handle_pad_press(self, note: int, velocity: int) -> None:
        """Handle a pad press for notes in PAD_NOTES_ALL."""
        # Update in-memory active pad bank marker so knob routing follows the
        # last physically-pressed bank without writing to settings on every press.
        if note in PAD_NOTES_BANK_A:
            self._active_pad_bank_mem = "A"
        elif note in PAD_NOTES_BANK_B:
            self._active_pad_bank_mem = "B"

        self.event_bus.publish("midi.pad_press", {
            "note": note, "velocity": velocity,
        })
        # Plugins first
        if self.plugin_manager.on_pad_press(note, velocity):
            labels = self.plugin_manager.get_all_pad_labels()
            label = labels.get(note, f"note {note}")
            self._runtime_log("PAD",
                f"{label} (note {note}, vel {velocity}) [plugin]",
                (200, 150, 255))
            # Push updated pad states (toggle may have changed)
            self.event_bus.publish("pads.updated", {
                "pads": self.get_state_snapshot()["pads"],
            })
        else:
            mapping = self.mapper.lookup_pad_for_active(note)
            if mapping:
                self._runtime_log("PAD",
                    f"{mapping.label} (note {note}, vel {velocity})",
                    (100, 200, 255))
                self._execute_action(mapping.action)
            else:
                self._runtime_log("PAD",
                    f"note {note} (unmapped)", (150, 150, 160))

    # ------------------------------------------------------------------
    # Piano dispatch (play vs map bank)
    # ------------------------------------------------------------------

    def handle_piano_note(self, note: int, velocity: int, on: bool) -> bool:
        """Dispatch a non-pad MIDI note through active piano panel(s).

        Precedence: active ``map`` bank wins over active ``play`` bank when
        both are active simultaneously. Returns True if the note was handled
        (either by executor or piano plugin), False if there is no active
        piano panel for this note — in which case the caller may fall back
        to legacy plugin dispatch.
        """
        # Piano map dispatch is limited to keyboard notes 36-72. Notes outside
        # this range cannot be mapped; only ``play`` may accept them.
        map_pid = self.mapper.get_active_panel("piano", "map")
        play_pid = self.mapper.get_active_panel("piano", "play")

        # (1) Map wins: resolve a key mapping and execute its action on press.
        if map_pid and PIANO_MAP_NOTE_MIN <= note <= PIANO_MAP_NOTE_MAX:
            preset_name = self.mapper.get_panel_preset(map_pid)
            if preset_name:
                key = self.mapper.lookup_piano_key(preset_name, note)
                if key is not None:
                    if on and key.action:
                        from mapper import ActionDef
                        action = ActionDef(
                            type=key.action.get("type", ""),
                            keys=key.action.get("keys", ""),
                            target=key.action.get("target", ""),
                            command=key.action.get("command", ""),
                            process=key.action.get("process", ""),
                            params=dict(key.action.get("params", {}) or {}),
                        )
                        label = key.label or f"note {note}"
                        self._runtime_log("PIANO",
                            f"map: {label} -> {action.type}",
                            (150, 200, 255))
                        self._execute_action(action)
                    # On release or no-action key in map bank, swallow event.
                    return True
            # Map panel active but no matching key -> no-op (map has priority)
            return True

        # (2) Play bank: forward to Piano plugin.
        if play_pid:
            piano = self.plugin_manager.plugins.get("Piano") if hasattr(
                self.plugin_manager, "plugins") else None
            if piano is not None:
                try:
                    if on:
                        piano._note_on(note, velocity)
                    else:
                        piano._note_off(note)
                    return True
                except Exception as exc:
                    self._runtime_log("ERR",
                        f"piano note dispatch failed: {exc}", (255, 80, 80))
                    return True

        # (3) Neither bank active — not handled.
        return False

    # ------------------------------------------------------------------
    # Hotkey action dispatch
    # ------------------------------------------------------------------

    def _execute_hotkey_action(self, preset_name: str, note: int) -> None:
        """Execute the action bound to a specific preset + note (from hotkey)."""
        with self._lock:
            mapping = self.mapper.get_pad_from_preset(preset_name, note)
            if mapping:
                self._runtime_log("HOTKEY",
                    f"{mapping.label} ({preset_name}:note {note})",
                    (180, 220, 255))
                self._execute_action(mapping.action)
            else:
                self._runtime_log("HOTKEY",
                    f"note {note} in preset '{preset_name}' (unmapped)",
                    (150, 150, 160))

    # ------------------------------------------------------------------
    # Action execution
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
        try:
            if action.type == "volume":
                self._knob_volume(action, value)
            elif action.type == "plugin":
                self.plugin_manager.dispatch_knob_action(
                    action.target, value, action.params or {})
        except Exception as exc:
            self._runtime_log("ERR", f"Knob action failed: {exc}", (255, 80, 80))

    def _knob_volume(self, action, value: int) -> None:
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

        # Pads from registry — composite keys "Preset:note"
        pads = {}
        for key, entry in self.mapper.registry.get_all().items():
            pads[key] = {
                "note": entry.note,
                "preset": entry.preset,
                "label": entry.label,
                "source": entry.source,
                "action_type": entry.action_type,
                "action_data": entry.action_data,
                "hotkey": entry.hotkey,
                "locked": entry.locked,
                "color": list(entry.color),
            }

        # Plugin pad labels overlay — scoped to each plugin's own preset only.
        # Plugin name matches preset name (e.g. "Voicemeeter" plugin -> "Voicemeeter" preset).
        labels_by_plugin = self.plugin_manager.get_pad_labels_by_plugin()
        for plugin_name, plugin_labels in labels_by_plugin.items():
            for note_int, label in plugin_labels.items():
                pkey = f"{plugin_name}:{note_int}"
                if pkey in pads:
                    pads[pkey]["label"] = label

        # Plugin toggle states overlay — scoped to each plugin's own preset only.
        states_by_plugin = self.plugin_manager.get_pad_states_by_plugin()
        for plugin_name, plugin_states in states_by_plugin.items():
            for note_int, state in plugin_states.items():
                pkey = f"{plugin_name}:{note_int}"
                if pkey in pads:
                    pads[pkey]["toggle_state"] = state

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
                "current_index": self.mapper.get_current_preset_index(),
                "list": presets,
            },
            "panel_presets": settings.get("panel_presets") or {},
            "panels": settings.get("panels") or {},
            "active_panels": settings.get("active_panels") or {},
            "active_midi_presets": self.mapper.get_midi_routing(),
            "active_knob_presets": self.mapper.get_knob_routing(),
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
            "knob_presets": self.get_knob_presets(),
            "voicemeeter": self._get_vm_state(),
            "logs": self.log_buffer[-50:],
        }

    # ------------------------------------------------------------------
    # Panel presets
    # ------------------------------------------------------------------

    KNOB_CCS = [48, 49, 50, 51]

    def _default_knob_preset_name(self) -> str | None:
        """Return first available knob preset name, or None."""
        if self.config.knob_presets:
            return self.config.knob_presets[0].name
        return None

    def _active_knob_panel(self) -> str:
        """Resolve the active knob panel based on the active pad bank.

        bankA -> knobBank-A, bankB -> knobBank-B. Defaults to knobBank-A.
        Prefers the in-memory marker (updated on physical pad press); falls
        back to settings["panel_presets"]["_active_pad_bank"] (updated when
        user switches panel preset in UI).
        """
        active_bank = self._active_pad_bank_mem
        if active_bank is None:
            panel_presets = settings.get("panel_presets") or {}
            active_bank = panel_presets.get("_active_pad_bank")
        if active_bank == "B":
            return "knobBank-B"
        return "knobBank-A"

    def _migrate_panel_presets(self) -> None:
        """Ensure freeform ``panels`` + ``active_panels`` structures exist.

        Migrates legacy ``panel_presets.bankA/bankB/knobBank-A/B`` (and any
        dynamic ``padBank-*`` / ``knobBank-*`` entries) into 4 starter freeform
        panels (pad A active + pad B inactive + knob A active + knob B inactive),
        and resets ``ui_layout`` so the default layout is rebuilt.
        """
        if settings.get("panels") is not None and settings.get("active_panels") is not None:
            return

        preset = self.mapper.current_preset
        default_pad_preset = preset.name if preset else "Default"
        default_knob_preset = self._default_knob_preset_name() or default_pad_preset

        legacy = settings.get("panel_presets") or {}
        def _pick(*keys: str, fallback: str) -> str:
            for k in keys:
                p = legacy.get(k) or {}
                name = p.get("preset")
                if name:
                    return name
            return fallback

        pad_a_preset = _pick("bankA", fallback=default_pad_preset)
        pad_b_preset = _pick("bankB", fallback=default_pad_preset)
        knob_a_preset = _pick("knobBank-A", "knobs", fallback=default_knob_preset)
        knob_b_preset = _pick("knobBank-B", "knobs", fallback=default_knob_preset)

        import time as _time
        base = int(_time.time() * 1000)
        pad_a_id = f"padPanel-{base + 1}"
        pad_b_id = f"padPanel-{base + 2}"
        knob_a_id = f"knobPanel-{base + 3}"
        knob_b_id = f"knobPanel-{base + 4}"

        panels = {
            pad_a_id: {
                "instanceId": pad_a_id, "type": "pad", "bank": "A",
                "preset": pad_a_preset, "title": "Pad Panel A",
            },
            pad_b_id: {
                "instanceId": pad_b_id, "type": "pad", "bank": "B",
                "preset": pad_b_preset, "title": "Pad Panel B",
            },
            knob_a_id: {
                "instanceId": knob_a_id, "type": "knob", "bank": "A",
                "preset": knob_a_preset, "title": "Knob Panel A",
            },
            knob_b_id: {
                "instanceId": knob_b_id, "type": "knob", "bank": "B",
                "preset": knob_b_preset, "title": "Knob Panel B",
            },
        }
        active_panels = {
            "pad:A": pad_a_id,
            "pad:B": pad_b_id,
            "knob:A": knob_a_id,
            "knob:B": knob_b_id,
        }
        settings.put("panels", panels)
        settings.put("active_panels", active_panels)
        # Reset layout — simpler than migrating instance IDs in dockview JSON
        settings.put("ui_layout", None)
        # Drop legacy panel_presets to avoid confusion
        if legacy:
            settings.put("panel_presets", {})
        self._runtime_log("SYS",
            "Migrated to freeform panels (4 starter panels)", (100, 255, 150))

    # -- Freeform panel CRUD -----------------------------------------

    def _legacy_mirror_routing(self, panel_type: str, bank: str, preset_name: str | None) -> None:
        """Mirror active-panel binding into legacy routing tables."""
        if not preset_name:
            return
        if panel_type == "pad":
            routing_key = "bankA" if bank == "A" else "bankB"
            self.mapper.set_midi_routing(routing_key, preset_name)
        elif panel_type == "knob":
            legacy = "knobBank-A" if bank == "A" else "knobBank-B"
            self.mapper.set_knob_routing(legacy, preset_name)

    def list_panels(self) -> dict:
        return dict(settings.get("panels") or {})

    def get_panel(self, instance_id: str) -> dict | None:
        panels = settings.get("panels") or {}
        return panels.get(instance_id)

    def create_panel(
        self,
        panel_type: str,
        bank: str = "A",
        preset: str | None = None,
        title: str | None = None,
        activate: bool = False,
    ) -> dict:
        """Create a new freeform panel. Returns the persisted panel dict."""
        valid_banks = VALID_BANKS.get(panel_type)
        if valid_banks is None:
            raise ValueError(f"invalid panel type: {panel_type}")
        if bank not in valid_banks:
            raise ValueError(
                f"invalid bank '{bank}' for type '{panel_type}' "
                f"(expected one of {valid_banks})"
            )

        if preset is None:
            if panel_type == "pad":
                p = self.mapper.current_preset
                preset = p.name if p else "Default"
            elif panel_type == "knob":
                preset = self._default_knob_preset_name() or "Default"
            else:  # piano
                piano_presets = getattr(self.config, "piano_presets", []) or []
                preset = piano_presets[0].name if piano_presets else ""

        if title is None:
            title = _template_title(panel_type, bank)

        import time as _time, uuid
        instance_id = f"{panel_type}Panel-{int(_time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
        panel = {
            "instanceId": instance_id,
            "type": panel_type,
            "bank": bank,
            "preset": preset,
            "title": title,
        }
        panels = dict(settings.get("panels") or {})
        panels[instance_id] = panel
        settings.put("panels", panels)

        with self._lock:
            self.mapper.register_panel_preset(instance_id, preset)

        self.event_bus.publish("panel.created", {"panel": panel})

        if activate:
            self.activate_panel(instance_id)
        return panel

    def update_panel(
        self, instance_id: str,
        bank: str | None = None,
        preset: str | None = None,
        title: str | None = None,
    ) -> dict | None:
        panels = dict(settings.get("panels") or {})
        panel = panels.get(instance_id)
        if not panel:
            return None

        panel_type = panel.get("type")
        old_bank = panel.get("bank")

        # Detect whether this panel currently occupies the active slot
        # on its *old* bank — we need to clear/reassign that slot if the
        # bank changes.
        active_map = dict(settings.get("active_panels") or {})
        old_slot_key = f"{panel_type}:{old_bank}"
        was_active_on_old = active_map.get(old_slot_key) == instance_id

        valid_banks = VALID_BANKS.get(panel_type, ())
        changed = False
        bank_changed = False
        if bank is not None and bank in valid_banks and old_bank != bank:
            panel["bank"] = bank
            changed = True
            bank_changed = True
        if preset is not None and panel.get("preset") != preset:
            panel["preset"] = preset
            with self._lock:
                self.mapper.register_panel_preset(instance_id, preset)
            changed = True
        if title is not None and panel.get("title") != title:
            panel["title"] = title
            changed = True
        if not changed:
            return panel

        panels[instance_id] = panel
        settings.put("panels", panels)

        auto_activated = False
        if bank_changed and was_active_on_old:
            # Clear the old slot (both settings + mapper routing)
            active_map[old_slot_key] = None
            with self._lock:
                self.mapper.set_active_panel(panel_type, old_bank, None)

            # Auto-activate on the new bank iff that slot is empty.
            new_slot_key = f"{panel_type}:{panel['bank']}"
            if not active_map.get(new_slot_key):
                active_map[new_slot_key] = instance_id
                with self._lock:
                    self.mapper.set_active_panel(
                        panel_type, panel["bank"], instance_id)
                    self._legacy_mirror_routing(
                        panel_type, panel["bank"], panel.get("preset"))
                auto_activated = True

            settings.put("active_panels", active_map)

        # If this panel is currently active (on its current bank), re-mirror
        # legacy routing so preset/title edits take effect.
        current_key = f"{panel_type}:{panel['bank']}"
        currently_active = active_map.get(current_key) == instance_id
        if currently_active and not auto_activated:
            self._legacy_mirror_routing(
                panel_type, panel["bank"], panel.get("preset"))
        if currently_active:
            try:
                self.plugin_manager.on_mode_changed(panel["preset"])
                self.plugin_manager.notify_preset_changed(self.mapper)
            except Exception:
                pass

        payload = {
            "panel": panel,
            "pads": self.get_state_snapshot()["pads"],
            "active_midi_presets": self.mapper.get_midi_routing(),
            "active_knob_presets": self.mapper.get_knob_routing(),
        }
        if bank_changed and was_active_on_old:
            payload["active_panels"] = active_map
        self.event_bus.publish("panel.updated", payload)

        if bank_changed and was_active_on_old:
            # Also emit an active_panels-style event so clients update slots.
            self.event_bus.publish("panel.activated", {
                "instanceId": instance_id if auto_activated else None,
                "previousId": instance_id,
                "type": panel_type,
                "bank": panel["bank"] if auto_activated else old_bank,
                "active_panels": active_map,
                "pads": payload["pads"],
                "active_midi_presets": payload["active_midi_presets"],
                "active_knob_presets": payload["active_knob_presets"],
            })
        return panel

    def delete_panel(self, instance_id: str) -> bool:
        panels = dict(settings.get("panels") or {})
        panel = panels.pop(instance_id, None)
        if not panel:
            return False
        settings.put("panels", panels)

        active = dict(settings.get("active_panels") or {})
        changed_active = False
        for key, pid in list(active.items()):
            if pid == instance_id:
                active[key] = None
                changed_active = True
        if changed_active:
            settings.put("active_panels", active)

        with self._lock:
            self.mapper.unregister_panel(instance_id)

        self.event_bus.publish("panel.deleted", {
            "instanceId": instance_id,
            "active_panels": active,
        })
        return True

    def reconcile_panels(self, fix_titles: bool = False) -> dict:
        """Reconcile active_panels slots against the live panels dict.

        - Clears active slot entries that reference non-existent panels.
        - For an empty slot, if exactly one live panel matches the (type, bank)
          pair, assigns it as active.
        - If ``fix_titles`` is True, rewrites template-matching titles to the
          canonical template for the panel's current (type, bank). Custom
          titles (anything not matching the template regex) are untouched.

        Returns a dict with ``fixed`` (list of human-readable change messages)
        and ``warnings`` (list of diagnostic messages).
        """
        fixed: list[str] = []
        warnings: list[str] = []

        panels = dict(settings.get("panels") or {})
        active = dict(settings.get("active_panels") or {})

        # --- Clean up titles first (before the slot sweep relies on them) ---
        if fix_titles:
            for pid, panel in panels.items():
                if not isinstance(panel, dict):
                    continue
                panel_type = panel.get("type")
                bank = panel.get("bank")
                title = panel.get("title", "")
                if panel_type not in VALID_BANKS:
                    continue
                if bank not in VALID_BANKS[panel_type]:
                    continue
                if not isinstance(title, str) or not _TEMPLATE_TITLE_RE.match(title):
                    continue  # custom title -> leave alone
                new_title = _template_title(panel_type, bank)
                if new_title != title:
                    panel["title"] = new_title
                    fixed.append(
                        f"retitled {pid}: '{title}' -> '{new_title}'"
                    )

        # --- Sweep active_panels slots ---
        # Build index: (type, bank) -> [live panel ids]
        by_slot: dict[tuple[str, str], list[str]] = {}
        for pid, panel in panels.items():
            if not isinstance(panel, dict):
                continue
            t = panel.get("type")
            b = panel.get("bank")
            if t in VALID_BANKS and b in VALID_BANKS.get(t, ()):
                by_slot.setdefault((t, b), []).append(pid)

        # Ensure every valid slot key exists in the active map for uniform
        # handling. Missing keys are treated the same as ``None``.
        expected_slot_keys: list[str] = []
        for t, banks in VALID_BANKS.items():
            for b in banks:
                expected_slot_keys.append(f"{t}:{b}")

        changed_active = False
        for key in expected_slot_keys:
            try:
                panel_type, bank = key.split(":", 1)
            except ValueError:
                continue
            current = active.get(key)

            # (1) Stale reference — clear it.
            if current and current not in panels:
                active[key] = None
                changed_active = True
                with self._lock:
                    self.mapper.set_active_panel(panel_type, bank, None)
                fixed.append(
                    f"cleared stale active slot {key} "
                    f"(missing panel id '{current}')"
                )
                current = None

            # (2) Empty slot with exactly one candidate — auto-assign.
            if not current:
                candidates = by_slot.get((panel_type, bank), [])
                if len(candidates) == 1:
                    pid = candidates[0]
                    active[key] = pid
                    changed_active = True
                    with self._lock:
                        self.mapper.set_active_panel(panel_type, bank, pid)
                        preset_name = (panels[pid] or {}).get("preset")
                        if preset_name:
                            self.mapper.register_panel_preset(pid, preset_name)
                        self._legacy_mirror_routing(
                            panel_type, bank, preset_name)
                    fixed.append(f"auto-activated '{pid}' on slot {key}")
                elif len(candidates) > 1:
                    warnings.append(
                        f"slot {key} has {len(candidates)} candidate panels; "
                        f"leaving empty (user must activate manually)"
                    )

        # Persist if anything changed
        if fix_titles:
            settings.put("panels", panels)
        if changed_active:
            settings.put("active_panels", active)

        return {"fixed": fixed, "warnings": warnings}

    def activate_panel(self, instance_id: str) -> bool:
        panels = settings.get("panels") or {}
        panel = panels.get(instance_id)
        if not panel:
            return False
        panel_type = panel.get("type")
        bank = panel.get("bank")
        valid_banks = VALID_BANKS.get(panel_type, ())
        if not valid_banks or bank not in valid_banks:
            return False

        # All settings + mapper mutation + snapshot happen under the same
        # lock so concurrent activations/reads observe a consistent state.
        with self._lock:
            active = dict(settings.get("active_panels") or {})
            key = f"{panel_type}:{bank}"
            prev = active.get(key)
            active[key] = instance_id
            settings.put("active_panels", active)

            self.mapper.set_active_panel(panel_type, bank, instance_id)
            self._legacy_mirror_routing(panel_type, bank, panel.get("preset"))

            # Silence any in-flight piano voices when switching piano banks.
            # Without this, a note held while the user flips from play to map
            # (or vice-versa) can get stuck because the matching note_off is
            # routed differently than its note_on.
            if panel_type == "piano":
                try:
                    piano = self.plugin_manager.plugins.get("Piano") if hasattr(
                        self.plugin_manager, "plugins") else None
                    if piano is not None and hasattr(piano, "stop_all"):
                        piano.stop_all()
                except Exception:
                    # Best-effort: never block activation because of silence.
                    pass

            snapshot_pads = self.get_state_snapshot()["pads"]
            midi_routing = self.mapper.get_midi_routing()
            knob_routing = self.mapper.get_knob_routing()

        try:
            if panel.get("preset"):
                self.plugin_manager.on_mode_changed(panel["preset"])
                self.plugin_manager.notify_preset_changed(self.mapper)
        except Exception:
            pass

        self.event_bus.publish("panel.activated", {
            "instanceId": instance_id,
            "previousId": prev,
            "type": panel_type,
            "bank": bank,
            "active_panels": active,
            "pads": snapshot_pads,
            "active_midi_presets": midi_routing,
            "active_knob_presets": knob_routing,
        })
        return True

    def get_panel_preset(self, panel_id: str) -> dict | None:
        """Get panel preset info for any panel."""
        panel_presets = settings.get("panel_presets")
        if not panel_presets:
            return None
        return panel_presets.get(panel_id)

    def set_panel_preset(self, panel_id: str, preset_name: str, bank: str | None = None) -> bool:
        """Switch a single panel to a different preset.

        Updates the routing table (no pad_maps mutation), persists to settings,
        and notifies plugins + clients.

        Args:
            panel_id: Panel identifier (e.g. "bankA", "padBank-1", "knobBank-1")
            preset_name: Target preset name
            bank: Optional bank override ("A" or "B"). For legacy IDs inferred automatically.
        """
        # Validate preset exists
        preset = self.mapper.get_preset_by_name(preset_name)
        if not preset:
            return False

        panel_presets = settings.get("panel_presets") or {}

        # Determine bank for MIDI routing
        is_knob = panel_id == "knobs" or panel_id.startswith("knob")
        if is_knob:
            panel_presets.setdefault(panel_id, {})["preset"] = preset_name
            # Attempt knob routing if this maps to a knob preset
            with self._lock:
                self.mapper.set_knob_routing(panel_id, preset_name)
        else:
            # Resolve bank: explicit > saved > legacy ID > default A
            if not bank:
                if panel_id == "bankA":
                    bank = "A"
                elif panel_id == "bankB":
                    bank = "B"
                else:
                    bank = panel_presets.get(panel_id, {}).get("bank", "A")

            routing_key = "bankA" if bank == "A" else "bankB"
            with self._lock:
                self.mapper.set_midi_routing(routing_key, preset_name)
            panel_presets.setdefault(panel_id, {})["preset"] = preset_name
            panel_presets[panel_id]["bank"] = bank
            # Track the most recently activated pad bank for knob routing
            panel_presets["_active_pad_bank"] = bank

        settings.put("panel_presets", panel_presets)

        # Notify plugins
        try:
            self.plugin_manager.on_mode_changed(preset_name)
        except Exception:
            pass
        try:
            self.plugin_manager.notify_preset_changed(self.mapper)
        except Exception:
            pass

        self.event_bus.publish("panel_preset.changed", {
            "panel": panel_id,
            "preset": preset_name,
            "pads": self.get_state_snapshot()["pads"],
            "active_midi_presets": self.mapper.get_midi_routing(),
        })
        return True

    def set_panel_order(self, panel_id: str, order: list[int]) -> bool:
        """Save drag-and-drop order for a panel."""
        panel_presets = settings.get("panel_presets") or {}
        panel_presets.setdefault(panel_id, {})
        panel_presets[panel_id]["order"] = order
        settings.put("panel_presets", panel_presets)
        return True

    def create_preset(self, name: str) -> tuple[bool, str]:
        """Create a new empty preset in config.

        Returns (success, error_message).
        """
        from mapper import PadPreset
        err = _validate_preset_name(name)
        if err:
            return False, err
        name = name.strip()
        if self.mapper.get_preset_by_name(name) is not None:
            return False, f"Пресет '{name}' уже существует"
        preset = PadPreset(name=name)
        with self._lock:
            self.config.pad_presets.append(preset)
            self.mapper.rebuild_maps()
        save_config(self.config, CONFIG_PATH)
        return True, ""

    def delete_preset(self, name: str) -> tuple[bool, str]:
        """Delete a preset by name. Refuse if it's the last one.

        Returns (success, error_message).
        """
        if len(self.config.pad_presets) <= 1:
            return False, "Cannot delete the last preset"
        idx = self.mapper.get_preset_index_by_name(name)
        if idx is None:
            return False, f"Preset '{name}' not found"

        # Check if any panel uses this preset — switch them to first available
        panel_presets = settings.get("panel_presets") or {}
        remaining_name = None
        for i, p in enumerate(self.config.pad_presets):
            if i != idx:
                remaining_name = p.name
                break

        with self._lock:
            self.config.pad_presets.pop(idx)
            self.mapper.rebuild_maps()

            # Adjust current_preset_index if needed
            if self.mapper.get_current_preset_index() >= len(self.config.pad_presets):
                self.mapper.set_current_preset_index(
                    max(0, len(self.config.pad_presets) - 1))

        save_config(self.config, CONFIG_PATH)

        # Update panel_presets references
        for panel_id, panel_data in panel_presets.items():
            if isinstance(panel_data, dict) and panel_data.get("preset", "").lower() == name.lower():
                panel_data["preset"] = remaining_name or "Default"
        settings.put("panel_presets", panel_presets)

        # Update routing table
        self._restore_midi_routing()

        return True, ""

    def rename_preset(self, old_name: str, new_name: str) -> tuple[bool, str]:
        """Rename a preset. Returns (success, error_message)."""
        err = _validate_preset_name(new_name)
        if err:
            return False, err
        new_name = new_name.strip()
        if self.mapper.get_preset_by_name(new_name) is not None:
            return False, f"Preset '{new_name}' already exists"
        preset = self.mapper.get_preset_by_name(old_name)
        if not preset:
            return False, f"Preset '{old_name}' not found"

        with self._lock:
            preset.name = new_name
            # Rebuild so registry picks up the new name
            self.mapper.rebuild_maps()

        save_config(self.config, CONFIG_PATH)

        # Update panel_presets references
        panel_presets = settings.get("panel_presets") or {}
        for panel_id, panel_data in panel_presets.items():
            if isinstance(panel_data, dict) and panel_data.get("preset", "").lower() == old_name.lower():
                panel_data["preset"] = new_name
        settings.put("panel_presets", panel_presets)

        # Update routing table
        self._restore_midi_routing()

        return True, ""

    def get_knob_presets(self) -> list[dict]:
        """Return list of knob presets with name, knob count, and full knob definitions.

        The ``knobs`` field is an additive extension so inactive knob panels
        can render their preset's knob layout without relying on the live
        ``knobs`` store (which tracks only the active preset).
        """
        result = []
        for kp in self.config.knob_presets:
            knobs = []
            for k in kp.knobs:
                knobs.append({
                    "cc": k.cc,
                    "label": k.label,
                    "action": {
                        "type": k.action.type,
                        "target": k.action.target,
                    },
                    "value": 0,
                })
            result.append({
                "name": kp.name,
                "knob_count": len(kp.knobs),
                "knobs": knobs,
            })
        return result

    def switch_knob_preset(self, name: str, panel_id: str = "knobBank-A") -> bool:
        """Apply a knob preset for a specific knob panel.

        Updates per-panel routing (mapper._active_knob_presets) and persists
        the active preset to ``settings["panel_presets"][panel_id].preset``.
        Does NOT write ``config.toml`` — that file is the source of truth
        for preset *definitions*, not active routing state.
        """
        with self._lock:
            ok = self.mapper.set_knob_routing(panel_id, name)
        if not ok:
            # Fallback to legacy global apply for backward compat
            with self._lock:
                ok = self.mapper.apply_knob_preset(name)
            if not ok:
                return False
        # Update panel_presets[panel_id].preset in settings
        panel_presets = settings.get("panel_presets") or {}
        panel_presets.setdefault(panel_id, {})["preset"] = name
        settings.put("panel_presets", panel_presets)
        # Build updated knobs for event
        knobs = []
        for k in self.config.knobs:
            knobs.append({
                "cc": k.cc, "label": k.label,
                "action": {"type": k.action.type, "target": k.action.target},
                "value": settings.get("knob_values", {}).get(str(k.cc), 64),
            })
        self.event_bus.publish("knob_preset.changed", {
            "preset": name,
            "panel": panel_id,
            "knobs": knobs,
            "knob_routing": self.mapper.get_knob_routing(),
        })
        self._runtime_log("KNOB",
            f"Knob preset switched [{panel_id}]: {name}", (100, 255, 150))
        return True

    def swap_knobs(self, cc_a: int, cc_b: int) -> bool:
        """Swap two knob mappings and persist to config.toml."""
        with self._lock:
            ok = self.mapper.swap_knobs(cc_a, cc_b)
        if not ok:
            return False
        save_config(self.config, CONFIG_PATH)
        # Build updated knobs for event
        knobs = []
        for k in self.config.knobs:
            knobs.append({
                "cc": k.cc, "label": k.label,
                "action": {"type": k.action.type, "target": k.action.target},
                "value": settings.get("knob_values", {}).get(str(k.cc), 64),
            })
        self.event_bus.publish("knobs.updated_all", {"knobs": knobs})
        self._runtime_log("KNOB",
            f"Knobs swapped: CC{cc_a} <-> CC{cc_b}", (100, 255, 150))
        return True

    def update_knob_config(
        self,
        cc: int,
        action_type: str,
        target: str,
        label: str,
        params: dict,
    ) -> bool:
        """Update knob action in config.toml via mapper, publish event."""
        if params:
            err = _validate_params(params)
            if err:
                self._runtime_log("ERR", f"Knob params invalid: {err}", (255, 80, 80))
                return False
        with self._lock:
            ok = self.mapper.update_knob_action(
                cc, action_type, target, label, params,
                config_path=CONFIG_PATH,
            )
        if ok:
            self.event_bus.publish("knobs.updated", {
                "cc": cc, "label": label,
                "action": {"type": action_type, "target": target, "params": params},
            })
            self._runtime_log("KNOB",
                f"Knob CC{cc} updated: {label} ({target})", (100, 255, 150))
        return ok

    def _get_vm_state(self) -> dict:
        """Extract Voicemeeter plugin state for API."""
        vm_plugin = self.plugin_manager.plugins.get("Voicemeeter")
        if not vm_plugin:
            return {"connected": False}
        try:
            return {
                "connected": getattr(vm_plugin, '_vm', None) is not None
                             and getattr(vm_plugin._vm, 'connected', False),
                "mic_mute": getattr(vm_plugin, '_mic_mute', False),
                "desk_mute": getattr(vm_plugin, '_desk_mute', False),
                "eq_on": getattr(vm_plugin, '_eq_on', False),
                "send2mic": getattr(vm_plugin, '_s2m_b1', False),
                "gate_on": getattr(vm_plugin, '_gate_on', False),
                "monitor": getattr(vm_plugin, '_monitor', False),
                "comp_on": getattr(vm_plugin, '_comp_on', False),
                "mic_gain": getattr(vm_plugin, '_mic_gain', 0.0),
                "duck_enabled": getattr(vm_plugin, '_duck_enabled', False),
            }
        except Exception:
            return {"connected": False}
