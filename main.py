"""MIDI Macropad -- entry point."""
import sys
import os

os.environ.setdefault("PYTHONUTF8", "1")

import queue
import time
import threading
import traceback
import atexit
import signal
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

_PID_FILE = Path(__file__).parent / ".macropad.pid"


def _kill_previous_instance() -> None:
    if not _PID_FILE.exists():
        return
    try:
        old_pid = int(_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return
    if old_pid == os.getpid():
        return
    try:
        os.kill(old_pid, signal.SIGTERM)
        time.sleep(0.5)
    except OSError:
        pass


def _write_pid() -> None:
    _PID_FILE.write_text(str(os.getpid()))


def _remove_pid() -> None:
    try:
        if _PID_FILE.exists() and _PID_FILE.read_text().strip() == str(os.getpid()):
            _PID_FILE.unlink()
    except OSError:
        pass


import dearpygui.dearpygui as dpg
import settings
from midi_listener import MidiListener, MidiEvent
from mapper import Mapper, load_config
from executor import execute_keystroke, execute_shell, execute_launch, execute_scroll
from audio import AudioController, enumerate_output_devices, enumerate_input_devices
from feedback import FeedbackService, set_transpose, get_transpose
from ui.dashboard import (
    create_dashboard, setup_theme,
    create_layout, create_center_content, add_plugin_tab,
    post_setup as dashboard_post_setup, poll as poll_dashboard,
    set_resize_callback,
)
from ui.pad_grid import (
    create_pad_grid, update_pad_labels, flash_pad, release_pad,
    clear_pad_labels, overlay_plugin_pad_labels, update_knob_display,
    update_single_pad_label,
    set_pad_click_callback, set_pad_edit_callback, set_pad_swap_callback,
    set_pad_select_callback, set_knob_edit_callback,
)
from ui.volume_panel import (
    create_volume_panel, set_master_volume_display, set_mic_volume_display,
    set_master_mute_display, set_mic_mute_display,
    set_master_cap_display, set_mic_cap_display,
    populate_output_devices, populate_input_devices,
)
from ui.midi_log import create_midi_log, add_log_entry
from ui.toolbar import create_toolbar, set_active_preset
from ui.sidebar_right import create_right_sidebar, set_rebuild_fn, rebuild, set_plugin_list
from ui.pad_editor import build_pad_properties
from ui.quick_action_picker import build_quick_picker
from ui.status_bar import poll_hover, register as register_tooltip
from ui import selection
from obs_controller import OBSController
from hotkey_listener import HotkeyListener
from plugins.manager import PluginManager
from logger import get_logger, LOG_FILE, log_startup_banner, log_session_summary

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.toml")
EVENT_QUEUE: queue.Queue = queue.Queue(maxsize=256)
log = get_logger("app")

_midi_event_count = 0
_plugin_error_count = 0
_session_start = 0.0

# Knob value cache with 5-second debounced save
_knob_value_cache: dict[int, int] = {}
_knob_flush_timer: threading.Timer | None = None
_KNOB_DEBOUNCE_S = 5.0


class _NullLedController:
    def connect(self) -> bool:
        return False

    def disconnect(self) -> None:
        pass

    def pad_on(self, note: int, velocity: int = 127) -> None:
        pass

    def pad_off(self, note: int) -> None:
        pass


leds = _NullLedController()


def _append_ui_log(tag: str, message: str, color=(200, 200, 200)):
    try:
        add_log_entry(tag, message, color=color)
    except Exception:
        pass


def _runtime_log(tag: str, message: str, color=(200, 200, 200)):
    global _plugin_error_count
    if color == (255, 80, 80):
        _plugin_error_count += 1
    _append_ui_log(tag, message, color=color)
    logger = log.error if color == (255, 80, 80) else log.info
    logger("%s | %s", tag, message)


def _midi_runtime_log(level: str, message: str):
    color = (
        (255, 80, 80) if level == "error"
        else (255, 180, 80) if level == "warning"
        else (100, 255, 150)
    )
    _append_ui_log("MIDI", message, color=color)
    log_fn = getattr(log, level, log.info)
    log_fn("MIDI | %s", message)


try:
    settings.load()
    config = load_config(CONFIG_PATH)
    mapper = Mapper(config)
    plugin_manager = PluginManager(
        Path(__file__).parent / "plugins", log_fn=_runtime_log,
    )
    feedback = FeedbackService(config.device_name, log_fn=_runtime_log)
    plugin_manager.set_runtime_services({"feedback": feedback})
    audio = AudioController(
        output_device_id=settings.get("output_device_id"),
        input_device_id=settings.get("input_device_id"),
        midi_master_cap=settings.get("midi_master_cap", 1.0),
        midi_mic_cap=settings.get("midi_mic_cap", 1.0),
    )
    midi = MidiListener(config.device_name, EVENT_QUEUE, log_fn=_midi_runtime_log)
    hotkeys = HotkeyListener(EVENT_QUEUE, log_fn=_runtime_log)
    _obs_cfg = settings.get("obs_session_plugin", {})
    obs = OBSController(
        host=_obs_cfg.get("host", "127.0.0.1"),
        port=int(_obs_cfg.get("port", 4455)),
        password=_obs_cfg.get("password", ""),
    )
except Exception:
    log.error("Bootstrap failed:\n%s", traceback.format_exc())
    raise

# ---- pad preset UI ----------------------------------------------------------


def _apply_preset_ui():
    preset = mapper.current_preset
    set_active_preset(mapper.current_preset_index)
    clear_pad_labels()
    update_pad_labels(preset.pads)
    plugin_manager.on_mode_changed(preset.name)
    plugin_manager.notify_preset_changed(mapper)
    overlay_plugin_pad_labels(plugin_manager.get_all_pad_labels())


def on_preset_changed(index: int):
    mapper.set_preset(index)
    _apply_preset_ui()
    settings.put("preset_index", index)
    hotkeys.reload_bindings(mapper)


# ---- audio callbacks --------------------------------------------------------

def on_master_volume_slider(value):
    audio.set_master_volume(value)

def on_mic_volume_slider(value):
    audio.set_mic_volume(value)

def on_master_mute_toggle():
    muted = not audio.get_master_mute()
    audio.set_master_mute(muted)
    set_master_mute_display(muted)

def on_mic_mute_toggle():
    muted = not audio.get_mic_mute()
    audio.set_mic_mute(muted)
    set_mic_mute_display(muted)

def on_master_cap_changed(value):
    audio.midi_master_cap = value
    settings.put("midi_master_cap", value)

def on_mic_cap_changed(value):
    audio.midi_mic_cap = value
    settings.put("midi_mic_cap", value)

def on_output_device_changed(device_id):
    audio.set_output_device(device_id)
    settings.put("output_device_id", device_id)
    set_master_volume_display(audio.get_master_volume())
    set_master_mute_display(audio.get_master_mute())
    add_log_entry("SYS", "Output device changed", color=(100, 255, 150))

def on_input_device_changed(device_id):
    audio.set_input_device(device_id)
    settings.put("input_device_id", device_id)
    set_mic_volume_display(audio.get_mic_volume())
    set_mic_mute_display(audio.get_mic_mute())
    add_log_entry("SYS", "Input device changed", color=(100, 255, 150))


# ---- plugin toggle ----------------------------------------------------------

def on_plugin_toggle(name, info, enabled):
    if enabled:
        plugin_manager.load_plugin(info)
        _create_plugin_tabs()
        selection.select("plugin", name)
    else:
        plugin_manager.unload_plugin(name)
        selection.clear()
    set_plugin_list(list(plugin_manager.plugins.keys()))
    _apply_preset_ui()
    settings.put("enabled_plugins", list(plugin_manager.enabled))


# ---- MIDI event handling ----------------------------------------------------

def handle_midi_event(event: MidiEvent):
    global _midi_event_count
    _midi_event_count += 1
    try:
        if event.type == "pad_press":
            flash_pad(event.note, event.velocity)
            leds.pad_on(event.note, event.velocity)
            # Plugins get first chance at ALL notes (Bank B, piano keys, etc.)
            if plugin_manager.on_pad_press(event.note, event.velocity):
                labels = plugin_manager.get_all_pad_labels()
                overlay_plugin_pad_labels(labels)
                label = labels.get(event.note, f"note {event.note}")
                add_log_entry("PAD",
                              f"{label} (note {event.note}, vel {event.velocity}) [plugin]",
                              color=(200, 150, 255))
            else:
                # No plugin consumed it — fall through to mapper
                mapping = mapper.lookup_pad(event.note)
                if mapping:
                    add_log_entry("PAD",
                                  f"{mapping.label} (note {event.note}, vel {event.velocity})",
                                  color=(100, 200, 255))
                    _execute_action(mapping.action, cue_id=_feedback_cue_for_mapping(mapping))
                else:
                    add_log_entry("PAD",
                                  f"note {event.note} (unmapped)",
                                  color=(150, 150, 160))

        elif event.type == "pad_release":
            leds.pad_off(event.note)
            plugin_manager.on_pad_release(event.note)
            release_pad(event.note)

        elif event.type == "knob":
            update_knob_display(event.cc, event.value)
            if plugin_manager.on_knob(event.cc, event.value):
                return
            knob = mapper.lookup_knob(event.cc)
            if knob:
                add_log_entry("KNOB",
                              f"{knob.label} CC{event.cc}={event.value}",
                              color=(255, 200, 80))
                _handle_knob(knob, event.value)
            else:
                add_log_entry("KNOB",
                              f"CC{event.cc}={event.value} (unmapped)",
                              color=(150, 150, 160))

        elif event.type == "pitch_bend":
            # Plugins get first chance (e.g., REAPER Bridge forwards to DAW)
            if not plugin_manager.on_pitch_bend(event.pitch):
                _handle_pitch_bend_transpose(event.pitch)
    except Exception as exc:
        log.error("Unhandled MIDI event %s: %s\n%s",
                  event, exc, traceback.format_exc())
        _runtime_log("ERR", f"Unhandled MIDI event: {exc}", color=(255, 80, 80))


_pb_last_switch_time = 0.0
_pb_armed = True
_PB_THRESHOLD = 3000
_PB_CENTER_ZONE = 1500
_PB_COOLDOWN = 0.18


def _handle_pitch_bend_transpose(pitch: int):
    """Pitch bend X-axis: right (+) = transpose up, left (-) = transpose down."""
    global _pb_last_switch_time, _pb_armed
    import time as _t

    if abs(pitch) < _PB_CENTER_ZONE:
        _pb_armed = True
        return

    if not _pb_armed:
        return

    now = _t.monotonic()
    if now - _pb_last_switch_time < _PB_COOLDOWN:
        return

    current = get_transpose()
    if pitch > _PB_THRESHOLD:
        new_val = current + 1
    elif pitch < -_PB_THRESHOLD:
        new_val = current - 1
    else:
        return

    new_val = max(-24, min(24, new_val))
    _pb_armed = False
    _pb_last_switch_time = now

    set_transpose(new_val)
    settings.put("melody_transpose", new_val)

    feedback.emit("action.default")
    add_log_entry("KEY",
                  f"Transpose: {new_val:+d} semitones",
                  color=(255, 200, 100))


def _restore_knob_values():
    """Restore knob UI display from saved values or current system state."""
    saved = settings.get("knob_values", {})
    for knob in config.knobs:
        cc = knob.cc
        value = None
        # For volume knobs, read the actual system level
        if knob.action.type == "volume":
            try:
                if knob.action.target == "master":
                    level = audio.get_master_volume()
                    value = int(level * 127)
                elif knob.action.target == "mic":
                    level = audio.get_mic_volume()
                    value = int(level * 127)
            except Exception:
                pass
        # Fall back to saved value
        if value is None:
            value = saved.get(str(cc), saved.get(cc, 0))
        value = max(0, min(127, int(value)))
        _knob_value_cache[cc] = value
        update_knob_display(cc, value)


def _knob_cache_value(cc: int, value: int):
    """Buffer knob value and schedule a debounced flush to settings."""
    global _knob_flush_timer
    _knob_value_cache[cc] = value
    if _knob_flush_timer is not None:
        _knob_flush_timer.cancel()

    def _flush():
        settings.put("knob_values", dict(_knob_value_cache))

    _knob_flush_timer = threading.Timer(_KNOB_DEBOUNCE_S, _flush)
    _knob_flush_timer.daemon = True
    _knob_flush_timer.start()


def _handle_knob(knob, value: int):
    _knob_cache_value(knob.cc, value)
    action = knob.action
    if action.type == "volume":
        if action.target == "master":
            level = audio.midi_to_master_volume(value)
            audio.set_master_volume(level)
            set_master_volume_display(level)
        elif action.target == "mic":
            level = audio.midi_to_mic_volume(value)
            audio.set_mic_volume(level)
            set_mic_volume_display(level)
        elif action.target == "foreground":
            from app_detector import get_foreground_process
            proc = get_foreground_process()
            if proc:
                audio.set_app_volume(proc.lower().replace(".exe", ""), value / 127.0)
            else:
                log.debug("Foreground knob: no active process detected")
        else:
            audio.set_app_volume(action.target, value / 127.0)
    elif action.type == "scroll":
        err = execute_scroll(value)
        if err:
            _runtime_log("ERR", err, color=(255, 80, 80))
    elif action.type == "keystroke":
        err = execute_keystroke(action.keys)
        if err:
            _runtime_log("ERR", err, color=(255, 80, 80))


def _feedback_cue_for_mapping(mapping) -> str:
    label = mapping.label.lower()
    if any(word in label for word in ("delete", "cut", "lock")):
        return "action.danger"
    if any(word in label for word in (
        "undo", "redo", "find", "terminal", "palette", "file",
        "scene", "desktop", "task view", "screenshot",
    )):
        return "action.navigation"
    return "action.default"


def _execute_action(action, cue_id: str = "action.default"):
    resolved_cue = cue_id
    if action.type == "keystroke":
        err = execute_keystroke(action.keys)
    elif action.type == "app_keystroke":
        from app_detector import get_foreground_process
        active_process = get_foreground_process() or ""
        expected_process = action.process or ""
        if expected_process and active_process.lower() != expected_process.lower():
            err = (
                f"Skipped {_format_action_keys(action.keys)}: active app is "
                f"{active_process or 'none'}, expected {expected_process}"
            )
        else:
            err = execute_keystroke(action.keys)
    elif action.type == "shell":
        err = execute_shell(action.command)
    elif action.type == "launch":
        err = execute_launch(action.command)
    elif action.type == "obs":
        err, resolved_cue = _execute_obs_action(action)
    else:
        err = f"Unsupported action type: {action.type}"
    if err:
        _runtime_log("ERR", err, color=(255, 80, 80))
        feedback.emit_error()
    elif resolved_cue:
        feedback.emit_action(resolved_cue)


def _format_action_keys(keys: str) -> str:
    return keys.replace("+", " + ")


def _execute_obs_action(action):
    if not obs.connected:
        if not obs.connect():
            return "Cannot connect to OBS", None
    cmd = action.target
    if cmd == "toggle_recording":
        obs.toggle_recording()
        if obs.is_recording:
            leds.pad_on(16)  # Rec pad stays lit while recording
        else:
            leds.pad_off(16)
        add_log_entry("OBS",
                      f"Recording: {'ON' if obs.is_recording else 'OFF'}",
                      color=(255, 100, 100))
        return None, "action.toggle_on" if obs.is_recording else "action.toggle_off"
    elif cmd == "toggle_streaming":
        obs.toggle_streaming()
        if obs.is_streaming:
            leds.pad_on(17)  # Stream pad stays lit while streaming
        else:
            leds.pad_off(17)
        add_log_entry("OBS",
                      f"Streaming: {'ON' if obs.is_streaming else 'OFF'}",
                      color=(255, 100, 100))
        return None, "action.toggle_on" if obs.is_streaming else "action.toggle_off"
    elif cmd == "next_scene":
        obs.next_scene()
        add_log_entry("OBS", f"Scene: {obs.current_scene}", color=(255, 180, 80))
        return None, "action.navigation"
    elif cmd == "prev_scene":
        obs.prev_scene()
        add_log_entry("OBS", f"Scene: {obs.current_scene}", color=(255, 180, 80))
        return None, "action.navigation"
    elif cmd.startswith("scene:"):
        scene_name = cmd[6:]
        obs.switch_scene(scene_name)
        add_log_entry("OBS", f"Scene: {scene_name}", color=(255, 180, 80))
        return None, "action.navigation"
    elif cmd.startswith("mute:"):
        source_name = cmd[5:]
        obs.toggle_source_mute(source_name)
        add_log_entry("OBS", f"Toggle mute: {source_name}", color=(255, 140, 100))
        return None, "action.default"
    return f"Unsupported OBS action: {cmd}", None


# ---- pad interactions --------------------------------------------------------

def _on_pad_click(note: int):
    """Trigger the pad action from UI click (same as MIDI press)."""
    flash_pad(note, 100)
    mapping = mapper.lookup_pad(note)
    if mapping and mapping.action.type == "plugin":
        if plugin_manager.on_pad_press(note, 100):
            labels = plugin_manager.get_all_pad_labels()
            overlay_plugin_pad_labels(labels)
            label = labels.get(note, f"note {note}")
            add_log_entry("PAD", f"{label} (note {note}) [UI click]",
                          color=(200, 150, 255))
        else:
            add_log_entry("PAD",
                          f"{mapping.label} (note {note}) [UI click, plugin idle]",
                          color=(150, 150, 160))
    elif mapping:
        add_log_entry("PAD", f"{mapping.label} (note {note}) [UI click]",
                      color=(100, 200, 255))
        _execute_action(mapping.action, cue_id=_feedback_cue_for_mapping(mapping))
    else:
        add_log_entry("PAD", f"note {note} [UI click, unmapped]",
                      color=(150, 150, 160))

    def _delayed_release():
        import threading
        def _rel():
            release_pad(note)
            plugin_manager.on_pad_release(note)
        threading.Timer(0.15, _rel).start()
    _delayed_release()


def _on_pad_select(note: int):
    """Single-click pad selection — show Quick Action Picker in Properties."""
    # Check mapper first, then registry for plugin-owned pads
    mapping = mapper.lookup_pad(note)
    if mapping:
        label = mapping.label
        action_type = mapping.action.type if mapping.action else ""
        action_data = {}
        if mapping.action:
            a = mapping.action
            if a.keys: action_data["keys"] = a.keys
            if a.process: action_data["process"] = a.process
            if a.command: action_data["command"] = a.command
            if a.target: action_data["target"] = a.target
        hotkey = getattr(mapping, 'hotkey', '') or ''
    else:
        # Try registry (plugin-owned pads)
        entry = mapper.registry.get_pad(note)
        if entry and entry.action_type:
            label = entry.label
            action_type = entry.action_type
            action_data = dict(entry.action_data)
            hotkey = entry.hotkey
        else:
            # Check plugin labels as last resort
            plugin_labels = plugin_manager.get_all_pad_labels()
            label = plugin_labels.get(note, f"Pad {note - 15}")
            action_type = "plugin" if note in plugin_labels else ""
            action_data = {}
            hotkey = ""
    rebuild(lambda parent: build_quick_picker(
        parent, note, label, action_type, action_data, hotkey,
        on_save=_on_pad_save_quick,
        plugin_manager=plugin_manager))


def _on_pad_edit(note: int):
    """Open the Properties panel for this pad (✎ button — same as select)."""
    _on_pad_select(note)


def _on_knob_edit(cc: int):
    """TODO(developer): open knob mapping editor when CC UI exists."""
    knob = mapper.lookup_knob(cc)
    if knob:
        add_log_entry("KNOB", f"Edit knob CC {cc} ({knob.label}) — editor not wired yet",
                      color=(150, 150, 170))
    else:
        add_log_entry("KNOB", f"Edit knob CC {cc} (unmapped)", color=(150, 150, 160))


def _toml_pad_section_key(raw: dict) -> str:
    if "pad_presets" in raw:
        return "pad_presets"
    if "modes" in raw:
        return "modes"
    raw["pad_presets"] = []
    return "pad_presets"


def _on_pad_swap(note_a: int, note_b: int):
    """Swap label+action between two pads in config.toml for the current preset."""
    plugin_notes = plugin_manager.get_plugin_controlled_notes()
    if note_a in plugin_notes or note_b in plugin_notes:
        add_log_entry("SYS",
                      "Cannot swap plugin-controlled pads — "
                      "use the plugin settings instead",
                      color=(255, 180, 80))
        return

    import toml as _toml
    preset = mapper.current_preset

    raw = _toml.load(CONFIG_PATH)
    section = _toml_pad_section_key(raw)
    for preset_data in raw.get(section, []):
        if preset_data.get("name") != preset.name:
            continue
        pads_list = preset_data.get("pads", [])
        pad_a = next((p for p in pads_list if p.get("note") == note_a), None)
        pad_b = next((p for p in pads_list if p.get("note") == note_b), None)

        if pad_a and pad_b:
            pad_a["label"], pad_b["label"] = pad_b["label"], pad_a["label"]
            pad_a["action"], pad_b["action"] = pad_b["action"], pad_a["action"]
            hk_a = pad_a.get("hotkey", "")
            hk_b = pad_b.get("hotkey", "")
            if hk_a or hk_b:
                pad_a["hotkey"], pad_b["hotkey"] = hk_b, hk_a
        elif pad_a and not pad_b:
            new_b = {"note": note_b, "label": pad_a["label"],
                     "action": dict(pad_a["action"])}
            if pad_a.get("hotkey"):
                new_b["hotkey"] = pad_a["hotkey"]
            pad_a["label"] = f"Pad {note_a - 15}"
            pad_a["action"] = {"type": "keystroke", "keys": ""}
            pad_a.pop("hotkey", None)
            pads_list.append(new_b)
        elif pad_b and not pad_a:
            new_a = {"note": note_a, "label": pad_b["label"],
                     "action": dict(pad_b["action"])}
            if pad_b.get("hotkey"):
                new_a["hotkey"] = pad_b["hotkey"]
            pad_b["label"] = f"Pad {note_b - 15}"
            pad_b["action"] = {"type": "keystroke", "keys": ""}
            pad_b.pop("hotkey", None)
            pads_list.append(new_a)
        break

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        _toml.dump(raw, f)

    # In-place update: swap in mapper, update two labels
    mapper.swap_pads(note_a, note_b)
    map_a = mapper.lookup_pad(note_a)
    map_b = mapper.lookup_pad(note_b)
    update_single_pad_label(note_a, map_a.label if map_a else f"Pad {note_a - 15}")
    update_single_pad_label(note_b, map_b.label if map_b else f"Pad {note_b - 15}")
    overlay_plugin_pad_labels(plugin_manager.get_all_pad_labels())
    add_log_entry("SYS",
                  f"Swapped pad {note_a - 15} <-> pad {note_b - 15}",
                  color=(100, 255, 150))


# ---- selection -> right sidebar ----------------------------------------------

def _on_selection_changed(sel_type, sel_id):
    if sel_type == "pad":
        _on_pad_select(sel_id)
    elif sel_type == "plugin":
        rebuild(lambda parent: plugin_manager.build_plugin_properties(sel_id, parent))
    else:
        rebuild(None)


def _on_pad_save(note, data):
    import toml as _toml
    preset = mapper.current_preset
    label = data.get("label", "")
    atype = data.get("action_type", "keystroke")
    hotkey = data.get("hotkey", "")

    action_dict: dict = {"type": atype}
    if atype == "keystroke":
        action_dict["keys"] = data.get("keys", "")
    elif atype == "app_keystroke":
        action_dict["keys"] = data.get("keys", "")
        action_dict["process"] = data.get("process", "")
    elif atype in ("shell", "launch"):
        action_dict["command"] = data.get("command", "")
    elif atype in ("obs", "volume"):
        action_dict["target"] = data.get("target", "")
    elif atype == "plugin":
        action_dict["target"] = data.get("target", "")

    # Persist to TOML
    raw = _toml.load(CONFIG_PATH)
    section = _toml_pad_section_key(raw)
    for preset_data in raw.get(section, []):
        if preset_data.get("name") != preset.name:
            continue
        for pad_data in preset_data.get("pads", []):
            if pad_data.get("note") == note:
                pad_data["label"] = label
                pad_data["action"] = action_dict
                if hotkey:
                    pad_data["hotkey"] = hotkey
                elif "hotkey" in pad_data:
                    del pad_data["hotkey"]
                break
        else:
            entry = {"note": note, "label": label, "action": action_dict}
            if hotkey:
                entry["hotkey"] = hotkey
            preset_data.setdefault("pads", []).append(entry)
        break

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        _toml.dump(raw, f)

    # In-place update: only touch this one pad
    mapper.update_pad(note, label, action_dict, hotkey)
    update_single_pad_label(note, label)
    # Re-overlay plugin labels in case this pad switched to/from plugin type
    overlay_plugin_pad_labels(plugin_manager.get_all_pad_labels())
    hotkeys.reload_bindings(mapper)
    add_log_entry("SYS", f"Pad {note} saved to config.toml", color=(100, 255, 150))


def _on_pad_save_quick(note, data):
    """Auto-save handler for Quick Action Picker."""
    # Handle partial updates (label/hotkey only changes)
    if "extra_field" in data:
        # Update just one field in the action_data
        mapping = mapper.lookup_pad(note)
        if mapping and mapping.action:
            action_dict = {"type": mapping.action.type}
            if mapping.action.keys: action_dict["keys"] = mapping.action.keys
            if mapping.action.process: action_dict["process"] = mapping.action.process
            if mapping.action.command: action_dict["command"] = mapping.action.command
            if mapping.action.target: action_dict["target"] = mapping.action.target
            action_dict[data["extra_field"]] = data["extra_value"]
            mapper.update_pad(note, mapping.label, action_dict,
                              getattr(mapping, 'hotkey', ''))
            _persist_pad_to_toml(note, mapping.label, action_dict,
                                 getattr(mapping, 'hotkey', ''))
        return

    if "label" in data and "action_type" not in data:
        # Just label or hotkey change
        mapping = mapper.lookup_pad(note)
        if mapping:
            label = data.get("label", mapping.label)
            hotkey = data.get("hotkey", getattr(mapping, 'hotkey', ''))
            action_dict = {"type": mapping.action.type}
            if mapping.action.keys: action_dict["keys"] = mapping.action.keys
            if mapping.action.process: action_dict["process"] = mapping.action.process
            if mapping.action.command: action_dict["command"] = mapping.action.command
            if mapping.action.target: action_dict["target"] = mapping.action.target
            mapper.update_pad(note, label, action_dict, hotkey)
            update_single_pad_label(note, label)
            _persist_pad_to_toml(note, label, action_dict, hotkey)
            hotkeys.reload_bindings(mapper)
        return

    # Full action assignment
    label = data.get("label", "")
    atype = data.get("action_type", "")
    action_data = data.get("action_data", {})
    hotkey = data.get("hotkey", "")

    action_dict: dict = {"type": atype}
    action_dict.update(action_data)

    mapper.update_pad(note, label, action_dict, hotkey)
    update_single_pad_label(note, label)
    overlay_plugin_pad_labels(plugin_manager.get_all_pad_labels())
    hotkeys.reload_bindings(mapper)
    _persist_pad_to_toml(note, label, action_dict, hotkey)
    add_log_entry("SYS", f"Pad {note - 15} assigned: {atype or 'cleared'}",
                  color=(100, 255, 150))


def _persist_pad_to_toml(note: int, label: str, action_dict: dict, hotkey: str):
    """Persist a single pad change to config.toml."""
    import toml as _toml
    preset = mapper.current_preset
    raw = _toml.load(CONFIG_PATH)
    section = _toml_pad_section_key(raw)
    for preset_data in raw.get(section, []):
        if preset_data.get("name") != preset.name:
            continue
        for pad_data in preset_data.get("pads", []):
            if pad_data.get("note") == note:
                pad_data["label"] = label
                pad_data["action"] = action_dict
                if hotkey:
                    pad_data["hotkey"] = hotkey
                elif "hotkey" in pad_data:
                    del pad_data["hotkey"]
                break
        else:
            entry = {"note": note, "label": label, "action": action_dict}
            if hotkey:
                entry["hotkey"] = hotkey
            preset_data.setdefault("pads", []).append(entry)
        break
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        _toml.dump(raw, f)


# ---- mixer tab --------------------------------------------------------------

def _create_mixer_tab():
    """Add a Mixer tab to center_tabs with volume/device controls."""
    with dpg.tab(label="  Mixer  ", parent="center_tabs", tag="tab_mixer"):
        with dpg.child_window(tag="mixer_tab_content", height=-1, border=False):
            pass
    create_volume_panel(
        parent="mixer_tab_content",
        master_callback=on_master_volume_slider,
        mic_callback=on_mic_volume_slider,
        master_mute_callback=on_master_mute_toggle,
        mic_mute_callback=on_mic_mute_toggle,
        output_device_callback=on_output_device_changed,
        input_device_callback=on_input_device_changed,
        master_cap_callback=on_master_cap_changed,
        mic_cap_callback=on_mic_cap_changed,
    )


# ---- settings tab -----------------------------------------------------------


def _create_settings_tab():
    """Add a Settings tab to center_tabs with plugin toggles."""
    with dpg.tab(label="  \u2699 Settings  ", parent="center_tabs",
                 tag="tab_settings"):
        with dpg.child_window(tag="settings_tab_content", height=-1,
                              border=False):
            dpg.add_spacer(height=8)
            dpg.add_text("Plugins", color=(85, 150, 240))
            dpg.add_separator()
            dpg.add_spacer(height=6)
            dpg.add_group(tag="settings_plugin_list")


def _focus_settings_tab():
    if dpg.does_item_exist("tab_settings"):
        dpg.set_value("center_tabs", "tab_settings")
    _populate_settings_plugins()


def _populate_settings_plugins():
    if not dpg.does_item_exist("settings_plugin_list"):
        return
    dpg.delete_item("settings_plugin_list", children_only=True)
    for info in plugin_manager.discover():
        name = info["name"]
        is_loaded = name in plugin_manager.plugins

        def _make_cb(n, iref):
            def cb(sender, app_data):
                on_plugin_toggle(n, iref, app_data)
                _populate_settings_plugins()
            return cb

        with dpg.group(horizontal=True, parent="settings_plugin_list"):
            dpg.add_checkbox(
                label=name,
                default_value=is_loaded,
                callback=_make_cb(name, info),
            )
            if is_loaded:
                dpg.add_text(f"  v{info.get('version', '?')}", color=(75, 78, 95))
        dpg.add_spacer(height=2, parent="settings_plugin_list")


# ---- plugin tabs ------------------------------------------------------------

_plugin_tab_tags: list[str] = []


def _create_plugin_tabs():
    for tag in _plugin_tab_tags:
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag)
    _plugin_tab_tags.clear()

    for winfo in plugin_manager.get_all_windows():
        wid = winfo["id"]
        title = winfo.get("title", wid)
        pname = winfo["_plugin"]
        content_tag = add_plugin_tab(wid, title)
        plugin_manager.build_plugin_window(pname, wid, content_tag)
        _plugin_tab_tags.append(f"tab_plugin_{wid}")


# ---- main -------------------------------------------------------------------

def main():
    log.info("Starting MIDI Macropad (log file: %s)", LOG_FILE)
    dpg.create_context()

    saved_w = int(settings.get("window_width", 2400))
    saved_h = int(settings.get("window_height", 1560))
    create_dashboard(width=saved_w, height=saved_h)
    theme = setup_theme()
    dpg.bind_theme(theme)

    create_layout()

    n_presets = len(config.pad_presets)
    saved_preset = int(settings.get("preset_index", settings.get("mode_index", 0)))
    if n_presets:
        if not (0 <= saved_preset < n_presets):
            saved_preset = max(0, min(saved_preset, n_presets - 1))
            settings.put("preset_index", saved_preset)
        mapper.set_preset(saved_preset)
    else:
        saved_preset = 0

    create_toolbar(
        preset_names=[p.name for p in config.pad_presets],
        preset_callback=on_preset_changed,
        settings_callback=_focus_settings_tab,
    )
    set_active_preset(saved_preset)

    create_center_content()

    # Center: pad grid + knobs, log
    set_pad_click_callback(_on_pad_click)
    set_pad_edit_callback(_on_pad_edit)
    set_pad_select_callback(_on_pad_select)
    set_pad_swap_callback(_on_pad_swap)
    set_knob_edit_callback(_on_knob_edit)
    create_pad_grid(knobs=config.knobs)
    create_midi_log()
    _create_mixer_tab()
    _create_settings_tab()

    # Right sidebar
    create_right_sidebar()

    # Audio devices
    out_devs = enumerate_output_devices()
    in_devs = enumerate_input_devices()
    populate_output_devices(out_devs, settings.get("output_device_id"))
    populate_input_devices(in_devs, settings.get("input_device_id"))
    set_master_cap_display(audio.midi_master_cap)
    set_mic_cap_display(audio.midi_mic_cap)

    set_rebuild_fn(_on_selection_changed)

    # Restore saved transpose
    saved_transpose = int(settings.get("melody_transpose", 0))
    set_transpose(saved_transpose)

    # Sync volume display
    set_master_volume_display(audio.get_master_volume())
    set_mic_volume_display(audio.get_mic_volume())
    set_master_mute_display(audio.get_master_mute())
    set_mic_mute_display(audio.get_mic_mute())

    # Restore knob display values from last session
    _restore_knob_values()

    # Plugins — restore previously enabled set, or load all on first run
    saved_plugins = settings.get("enabled_plugins")
    if saved_plugins is not None:
        target_plugins = set(saved_plugins)
        for info in plugin_manager.discover():
            if info["name"] in target_plugins:
                plugin_manager.load_plugin(info)
        if plugin_manager.enabled or not saved_plugins:
            settings.put("enabled_plugins", list(plugin_manager.enabled))
    else:
        plugin_manager.load_all()
    set_plugin_list(list(plugin_manager.plugins.keys()))
    _create_plugin_tabs()
    _apply_preset_ui()

    global _session_start
    _session_start = time.monotonic()
    log_startup_banner(list(plugin_manager.enabled))

    # Tooltips
    register_tooltip("master_vol_slider",
                     "Master volume -- also controlled by MIDI Knob 1 (CC 48)")
    register_tooltip("mic_vol_slider",
                     "Microphone volume -- also controlled by MIDI Knob 2 (CC 49)")
    register_tooltip("master_mute_btn", "Toggle master audio mute")
    register_tooltip("mic_mute_btn", "Toggle microphone mute")
    register_tooltip("master_cap_slider",
                     "Limits maximum master volume set by MIDI knob")
    register_tooltip("mic_cap_slider",
                     "Limits maximum mic volume set by MIDI knob")
    register_tooltip("output_device_combo", "Select audio output device")
    register_tooltip("input_device_combo", "Select audio input device")

    midi.start()
    add_log_entry("SYS", "Starting MIDI listener...", color=(100, 255, 150))

    # MIDI reconnect button in status bar
    def _on_midi_reconnect():
        add_log_entry("SYS", "MIDI reconnect requested...", color=(255, 200, 80))
        midi.reconnect()

    if dpg.does_item_exist("midi_reconnect_btn"):
        dpg.configure_item("midi_reconnect_btn", callback=lambda: _on_midi_reconnect())
    register_tooltip("midi_reconnect_btn", "Reconnect MIDI device")

    hotkeys.reload_bindings(mapper)
    hotkeys.start()

    if leds.connect():
        add_log_entry("LED", "Hardware LED output connected", color=(100, 255, 150))
    else:
        add_log_entry("LED", "Hardware LED output not available", color=(255, 180, 80))

    def _on_viewport_resize(w, h):
        settings.put("window_width", w)
        settings.put("window_height", h)

    set_resize_callback(_on_viewport_resize)

    dpg.setup_dearpygui()
    dpg.show_viewport()
    dashboard_post_setup()

    while dpg.is_dearpygui_running():
        for _ in range(20):
            try:
                event = EVENT_QUEUE.get_nowait()
                handle_midi_event(event)
            except queue.Empty:
                break

        if midi.connected:
            if dpg.does_item_exist("device_status"):
                dpg.set_value("device_status", f"MIDI: {midi.port_name}")
                dpg.configure_item("device_status", color=(80, 255, 120))
            if dpg.does_item_exist("midi_reconnect_btn"):
                dpg.configure_item("midi_reconnect_btn", show=True)
        else:
            if dpg.does_item_exist("device_status"):
                dpg.set_value("device_status", "MIDI: searching...")
                dpg.configure_item("device_status", color=(255, 180, 80))
            if dpg.does_item_exist("midi_reconnect_btn"):
                dpg.configure_item("midi_reconnect_btn", show=True)

        poll_dashboard()
        poll_hover()
        plugin_manager.poll_all()

        status = plugin_manager.get_active_status()
        if status:
            if dpg.does_item_exist("plugin_status_text"):
                dpg.set_value("plugin_status_text", status[0])
                dpg.configure_item("plugin_status_text", color=status[1])
        else:
            if dpg.does_item_exist("plugin_status_text"):
                dpg.set_value("plugin_status_text", "")

        dpg.render_dearpygui_frame()

    duration_s = time.monotonic() - _session_start
    log_session_summary(_midi_event_count, _plugin_error_count, duration_s)

    # Flush knob values before shutdown
    if _knob_flush_timer is not None:
        _knob_flush_timer.cancel()
    if _knob_value_cache:
        settings.put("knob_values", dict(_knob_value_cache))

    hotkeys.stop()
    plugin_manager.unload_all()
    feedback.close()
    leds.disconnect()
    midi.stop()
    settings.flush()
    settings.save_profile()
    dpg.destroy_context()
    log.info("MIDI Macropad closed")


if __name__ == "__main__":
    _kill_previous_instance()
    _write_pid()
    atexit.register(_remove_pid)
    try:
        main()
    except Exception:
        log.error("Fatal startup error:\n%s", traceback.format_exc())
        raise
