"""MIDI Macropad -- entry point."""
import sys
import os

os.environ.setdefault("PYTHONUTF8", "1")

import queue
import time
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
    set_pad_click_callback, set_pad_edit_callback, set_pad_swap_callback,
)
from ui.volume_panel import (
    create_volume_panel, set_master_volume_display, set_mic_volume_display,
    set_master_mute_display, set_mic_mute_display,
    set_master_cap_display, set_mic_cap_display,
    populate_output_devices, populate_input_devices,
)
from ui.midi_log import create_midi_log, add_log_entry
from ui.sidebar_left import create_left_sidebar, populate_plugins, set_active_preset
from ui.sidebar_right import create_right_sidebar, set_rebuild_fn, rebuild, set_plugin_list
from ui.pad_editor import build_pad_properties
from ui.status_bar import poll_hover, register as register_tooltip
from ui import selection
from obs_controller import OBSController
from plugins.manager import PluginManager
from logger import get_logger, LOG_FILE, log_startup_banner, log_session_summary

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.toml")
EVENT_QUEUE: queue.Queue = queue.Queue(maxsize=256)
log = get_logger("app")

_midi_event_count = 0
_plugin_error_count = 0
_session_start = 0.0


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
    obs = OBSController()
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
            mapping = mapper.lookup_pad(event.note)
            if mapping and mapping.action.type == "plugin":
                if plugin_manager.on_pad_press(event.note, event.velocity):
                    labels = plugin_manager.get_all_pad_labels()
                    overlay_plugin_pad_labels(labels)
                    label = labels.get(event.note, f"note {event.note}")
                    add_log_entry("PAD",
                                  f"{label} (note {event.note}, vel {event.velocity}) [plugin]",
                                  color=(200, 150, 255))
                else:
                    add_log_entry("PAD",
                                  f"{mapping.label} (note {event.note}, vel {event.velocity}) [plugin idle]",
                                  color=(150, 150, 160))
            elif mapping:
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


def _handle_knob(knob, value: int):
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


def _on_pad_edit(note: int):
    """Open the Properties panel for this pad."""
    mapping = mapper.lookup_pad(note)
    rebuild(lambda parent: build_pad_properties(
        parent, note, mapping, on_save=_on_pad_save))


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
    saved_idx = mapper.current_preset_index

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
        elif pad_a and not pad_b:
            new_b = {"note": note_b, "label": pad_a["label"],
                     "action": dict(pad_a["action"])}
            pad_a["label"] = f"Pad {note_a - 15}"
            pad_a["action"] = {"type": "keystroke", "keys": ""}
            pads_list.append(new_b)
        elif pad_b and not pad_a:
            new_a = {"note": note_a, "label": pad_b["label"],
                     "action": dict(pad_b["action"])}
            pad_b["label"] = f"Pad {note_b - 15}"
            pad_b["action"] = {"type": "keystroke", "keys": ""}
            pads_list.append(new_a)
        break

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        _toml.dump(raw, f)

    global config
    config = load_config(CONFIG_PATH)
    mapper.__init__(config)
    mapper.set_preset(saved_idx)
    _apply_preset_ui()
    add_log_entry("SYS",
                  f"Swapped pad {note_a - 15} <-> pad {note_b - 15}",
                  color=(100, 255, 150))


# ---- selection -> right sidebar ----------------------------------------------

def _on_selection_changed(sel_type, sel_id):
    if dpg.does_item_exist("sr_plugin_combo"):
        dpg.set_value("sr_plugin_combo", sel_id if sel_type == "plugin" else "(none)")
    if sel_type == "pad":
        mapping = mapper.lookup_pad(sel_id)
        rebuild(lambda parent: build_pad_properties(
            parent, sel_id, mapping, on_save=_on_pad_save))
    elif sel_type == "plugin":
        def _build_plugin(parent):
            plugin_manager.build_plugin_properties(sel_id, parent)
            plugin = plugin_manager.plugins.get(sel_id)
            if plugin:
                try:
                    dpg.add_spacer(height=8, parent=parent)
                    dpg.add_separator(parent=parent)
                    dpg.add_spacer(height=4, parent=parent)
                    plugin.build_ui(parent)
                except Exception:
                    pass
        rebuild(_build_plugin)
    else:
        rebuild(None)


def _on_pad_save(note, data):
    import toml as _toml
    preset = mapper.current_preset
    saved_idx = mapper.current_preset_index
    label = data.get("label", "")
    atype = data.get("action_type", "keystroke")

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

    raw = _toml.load(CONFIG_PATH)
    section = _toml_pad_section_key(raw)
    for preset_data in raw.get(section, []):
        if preset_data.get("name") != preset.name:
            continue
        for pad_data in preset_data.get("pads", []):
            if pad_data.get("note") == note:
                pad_data["label"] = label
                pad_data["action"] = action_dict
                break
        else:
            preset_data.setdefault("pads", []).append(
                {"note": note, "label": label, "action": action_dict})
        break

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        _toml.dump(raw, f)

    global config
    config = load_config(CONFIG_PATH)
    mapper.__init__(config)
    mapper.set_preset(saved_idx)
    _apply_preset_ui()
    add_log_entry("SYS", f"Pad {note} saved to config.toml", color=(100, 255, 150))


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
    create_center_content()

    n_presets = len(config.pad_presets)
    saved_preset = int(settings.get("preset_index", settings.get("mode_index", 0)))
    if n_presets:
        if not (0 <= saved_preset < n_presets):
            saved_preset = max(0, min(saved_preset, n_presets - 1))
            settings.put("preset_index", saved_preset)
        mapper.set_preset(saved_preset)
    else:
        saved_preset = 0

    create_left_sidebar(
        preset_names=[p.name for p in config.pad_presets],
        callback=on_preset_changed,
        plugin_toggle_callback=on_plugin_toggle,
    )
    set_active_preset(saved_preset)

    # Center: pad grid + knobs + mixer, log
    set_pad_click_callback(_on_pad_click)
    set_pad_edit_callback(_on_pad_edit)
    set_pad_swap_callback(_on_pad_swap)
    create_pad_grid(knobs=config.knobs)
    create_volume_panel(
        master_callback=on_master_volume_slider,
        mic_callback=on_mic_volume_slider,
        master_mute_callback=on_master_mute_toggle,
        mic_mute_callback=on_mic_mute_toggle,
        output_device_callback=on_output_device_changed,
        input_device_callback=on_input_device_changed,
        master_cap_callback=on_master_cap_changed,
        mic_cap_callback=on_mic_cap_changed,
    )
    create_midi_log()

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
    populate_plugins(plugin_manager.discover(),
                     set(plugin_manager.plugins.keys()))
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
            if dpg.does_item_exist("sl_midi_device"):
                dpg.set_value("sl_midi_device", midi.port_name or "")
                dpg.configure_item("sl_midi_device", color=(80, 255, 120))
        else:
            if dpg.does_item_exist("device_status"):
                dpg.set_value("device_status", "MIDI: searching...")
                dpg.configure_item("device_status", color=(255, 180, 80))
            if dpg.does_item_exist("sl_midi_device"):
                dpg.set_value("sl_midi_device", "Searching for device…")
                dpg.configure_item("sl_midi_device", color=(255, 180, 80))

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
