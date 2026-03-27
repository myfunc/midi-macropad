"""Pad editor — builds property widgets for editing pad actions."""
import dearpygui.dearpygui as dpg

ACTION_TYPES = [
    "keystroke", "app_keystroke", "shell", "launch", "obs", "volume", "scroll", "plugin",
]

OBS_TARGETS = [
    "toggle_recording", "toggle_streaming",
    "next_scene", "prev_scene",
]


def build_pad_properties(parent: str, note: int, pad_mapping, on_save=None):
    """Build pad property editor inside *parent*."""
    dpg.add_text(f"Pad Properties  (note {note})", parent=parent,
                 color=(100, 180, 255))
    dpg.add_separator(parent=parent)
    dpg.add_spacer(height=4, parent=parent)

    label = pad_mapping.label if pad_mapping else f"Pad {note - 15}"
    dpg.add_text("Label:", parent=parent, color=(150, 150, 160))
    dpg.add_input_text(tag=f"pe_label_{note}", default_value=label,
                       width=-1, parent=parent)

    dpg.add_spacer(height=4, parent=parent)

    current_type = pad_mapping.action.type if pad_mapping else "keystroke"
    dpg.add_text("Action:", parent=parent, color=(150, 150, 160))
    dpg.add_combo(tag=f"pe_action_type_{note}", items=ACTION_TYPES,
                  default_value=current_type, width=-1, parent=parent,
                  callback=lambda s, v: _on_type_changed(note, v))

    dpg.add_spacer(height=4, parent=parent)
    with dpg.group(tag=f"pe_fields_{note}", parent=parent):
        _build_fields(note, current_type, pad_mapping)

    dpg.add_spacer(height=8, parent=parent)
    with dpg.group(horizontal=True, parent=parent):
        dpg.add_button(label="Save", width=100,
                       callback=lambda: _on_save(note, on_save))
        dpg.add_button(label="Reset", width=100,
                       callback=lambda: _on_reset(note, pad_mapping))


def _build_fields(note, action_type, mapping):
    parent = f"pe_fields_{note}"

    if action_type in ("keystroke", "app_keystroke"):
        keys = mapping.action.keys if mapping else ""
        dpg.add_text("Keys:", parent=parent, color=(150, 150, 160))
        dpg.add_input_text(tag=f"pe_keys_{note}", default_value=keys,
                           hint="e.g. ctrl+shift+p", width=-1, parent=parent)
        if action_type == "app_keystroke":
            process = mapping.action.process if mapping else ""
            dpg.add_text("Process:", parent=parent, color=(150, 150, 160))
            dpg.add_input_text(tag=f"pe_process_{note}", default_value=process,
                               hint="e.g. Spotify.exe", width=-1, parent=parent)

    elif action_type in ("shell", "launch"):
        cmd = mapping.action.command if mapping else ""
        lbl = "Command:" if action_type == "shell" else "File / command:"
        dpg.add_text(lbl, parent=parent, color=(150, 150, 160))
        dpg.add_input_text(tag=f"pe_cmd_{note}", default_value=cmd,
                           width=-1, parent=parent)

    elif action_type == "obs":
        target = mapping.action.target if mapping else "toggle_recording"
        dpg.add_text("OBS Target:", parent=parent, color=(150, 150, 160))
        dpg.add_input_text(tag=f"pe_obs_{note}", default_value=target,
                           hint="toggle_recording, scene:Name, mute:Source",
                           width=-1, parent=parent)

    elif action_type == "volume":
        target = mapping.action.target if mapping else "master"
        dpg.add_text("Target:", parent=parent, color=(150, 150, 160))
        dpg.add_input_text(tag=f"pe_vol_{note}", default_value=target,
                           hint="master, mic, spotify, spotify.exe",
                           width=-1, parent=parent)

    elif action_type == "scroll":
        dpg.add_text("(no extra parameters)", parent=parent,
                     color=(120, 120, 140))

    elif action_type == "plugin":
        dpg.add_text("Handled by plugin", parent=parent,
                     color=(120, 120, 140))


def _on_type_changed(note, new_type):
    tag = f"pe_fields_{note}"
    if dpg.does_item_exist(tag):
        dpg.delete_item(tag, children_only=True)
    _build_fields(note, new_type, None)


def _on_save(note, callback):
    if not callback:
        return
    data = _collect(note)
    callback(note, data)


def _on_reset(note, mapping):
    if not mapping:
        return
    tag = f"pe_fields_{note}"
    if dpg.does_item_exist(tag):
        dpg.delete_item(tag, children_only=True)
    if dpg.does_item_exist(f"pe_label_{note}"):
        dpg.set_value(f"pe_label_{note}", mapping.label)
    if dpg.does_item_exist(f"pe_action_type_{note}"):
        dpg.set_value(f"pe_action_type_{note}", mapping.action.type)
    _build_fields(note, mapping.action.type, mapping)


def _collect(note) -> dict:
    data: dict = {}
    if dpg.does_item_exist(f"pe_label_{note}"):
        data["label"] = dpg.get_value(f"pe_label_{note}")
    if dpg.does_item_exist(f"pe_action_type_{note}"):
        data["action_type"] = dpg.get_value(f"pe_action_type_{note}")

    atype = data.get("action_type", "keystroke")
    if atype in ("keystroke", "app_keystroke") and dpg.does_item_exist(f"pe_keys_{note}"):
        data["keys"] = dpg.get_value(f"pe_keys_{note}")
        if atype == "app_keystroke" and dpg.does_item_exist(f"pe_process_{note}"):
            data["process"] = dpg.get_value(f"pe_process_{note}")
    elif atype in ("shell", "launch") and dpg.does_item_exist(f"pe_cmd_{note}"):
        data["command"] = dpg.get_value(f"pe_cmd_{note}")
    elif atype == "obs" and dpg.does_item_exist(f"pe_obs_{note}"):
        data["target"] = dpg.get_value(f"pe_obs_{note}")
    elif atype == "volume" and dpg.does_item_exist(f"pe_vol_{note}"):
        data["target"] = dpg.get_value(f"pe_vol_{note}")
    return data
