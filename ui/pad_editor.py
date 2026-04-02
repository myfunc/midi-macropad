"""Pad editor — builds property widgets for editing pad actions."""
import dearpygui.dearpygui as dpg
from ui.dashboard import get_text_font

ACTION_TYPES = [
    "keystroke", "app_keystroke", "shell", "launch", "obs", "volume", "scroll", "plugin",
]

OBS_TARGETS = [
    "toggle_recording", "toggle_streaming",
    "next_scene", "prev_scene",
]

_plugin_manager = None
_cached_catalogs: dict[str, list[dict]] = {}


def _bind_text(item):
    tf = get_text_font()
    if tf:
        dpg.bind_item_font(item, tf)


def _add_input(parent, **kwargs):
    """add_input_text + bind Cyrillic font."""
    item = dpg.add_input_text(parent=parent, **kwargs)
    _bind_text(item)
    return item


def _refresh_catalogs():
    global _cached_catalogs
    if _plugin_manager is not None:
        _cached_catalogs = _plugin_manager.get_all_action_catalogs()
    else:
        _cached_catalogs = {}


def build_pad_properties(parent: str, note: int, pad_mapping, on_save=None,
                         plugin_manager=None):
    """Build pad property editor inside *parent*."""
    global _plugin_manager
    if plugin_manager is not None:
        _plugin_manager = plugin_manager
    _refresh_catalogs()

    dpg.add_text(f"Pad Properties  (note {note})", parent=parent,
                 color=(100, 180, 255))
    dpg.add_separator(parent=parent)
    dpg.add_spacer(height=4, parent=parent)

    label = pad_mapping.label if pad_mapping else f"Pad {note - 15}"
    dpg.add_text("Label:", parent=parent, color=(150, 150, 160))
    _add_input(parent, tag=f"pe_label_{note}", default_value=label, width=-1)

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
    dpg.add_separator(parent=parent)
    dpg.add_spacer(height=4, parent=parent)

    hotkey_val = pad_mapping.hotkey if pad_mapping and hasattr(pad_mapping, "hotkey") else ""
    dpg.add_text("Hotkey:", parent=parent, color=(150, 150, 160))
    _add_input(parent, tag=f"pe_hotkey_{note}", default_value=hotkey_val or "",
               hint="e.g. mouse5, f13, ctrl+shift+r", width=-1)

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
        _add_input(parent, tag=f"pe_keys_{note}", default_value=keys,
                   hint="e.g. ctrl+shift+p", width=-1)
        if action_type == "app_keystroke":
            process = mapping.action.process if mapping else ""
            dpg.add_text("Process:", parent=parent, color=(150, 150, 160))
            _add_input(parent, tag=f"pe_process_{note}", default_value=process,
                       hint="e.g. Spotify.exe", width=-1)

    elif action_type in ("shell", "launch"):
        cmd = mapping.action.command if mapping else ""
        lbl = "Command:" if action_type == "shell" else "File / command:"
        dpg.add_text(lbl, parent=parent, color=(150, 150, 160))
        _add_input(parent, tag=f"pe_cmd_{note}", default_value=cmd, width=-1)

    elif action_type == "obs":
        target = mapping.action.target if mapping else "toggle_recording"
        dpg.add_text("OBS Target:", parent=parent, color=(150, 150, 160))
        _add_input(parent, tag=f"pe_obs_{note}", default_value=target,
                   hint="toggle_recording, scene:Name, mute:Source", width=-1)

    elif action_type == "volume":
        target = mapping.action.target if mapping else "master"
        dpg.add_text("Target:", parent=parent, color=(150, 150, 160))
        _add_input(parent, tag=f"pe_vol_{note}", default_value=target,
                   hint="master, mic, spotify, spotify.exe", width=-1)

    elif action_type == "scroll":
        dpg.add_text("(no extra parameters)", parent=parent,
                     color=(120, 120, 140))

    elif action_type == "plugin":
        _build_plugin_fields(note, mapping, parent)


def _build_plugin_fields(note, mapping, parent):
    """Plugin selector: plugin combo + action combo from catalog."""
    current_target = mapping.action.target if mapping else ""
    current_plugin = ""
    current_action_id = ""
    if ":" in current_target:
        current_plugin, current_action_id = current_target.split(":", 1)
    else:
        current_plugin = current_target

    plugin_names = sorted(_cached_catalogs.keys()) if _cached_catalogs else []
    if _plugin_manager is not None:
        all_loaded = list(_plugin_manager.plugins.keys())
        for pn in all_loaded:
            if pn not in plugin_names:
                plugin_names.append(pn)
        plugin_names.sort()

    if current_plugin and current_plugin not in plugin_names:
        plugin_names.insert(0, current_plugin)

    dpg.add_text("Plugin:", parent=parent, color=(150, 150, 160))
    dpg.add_combo(
        tag=f"pe_plugin_name_{note}",
        items=plugin_names,
        default_value=current_plugin,
        width=-1,
        parent=parent,
        callback=lambda s, v: _on_plugin_combo_changed(note, v),
    )

    dpg.add_spacer(height=4, parent=parent)

    actions = _cached_catalogs.get(current_plugin, [])
    action_labels = [f"{a['id']}  —  {a['label']}" for a in actions]
    action_ids = [a["id"] for a in actions]

    selected_label = ""
    if current_action_id in action_ids:
        idx = action_ids.index(current_action_id)
        selected_label = action_labels[idx]
    elif actions:
        selected_label = action_labels[0]

    if actions:
        dpg.add_text("Action:", parent=parent, color=(150, 150, 160))
        dpg.add_combo(
            tag=f"pe_plugin_action_{note}",
            items=action_labels,
            default_value=selected_label,
            width=-1,
            parent=parent,
        )

        desc_text = ""
        if current_action_id:
            for a in actions:
                if a["id"] == current_action_id:
                    desc_text = a.get("description", "")
                    break
        if desc_text:
            dpg.add_spacer(height=2, parent=parent)
            dpg.add_text(desc_text, parent=parent, color=(120, 120, 140), wrap=240)
    else:
        dpg.add_text("(no actions available for this plugin)", parent=parent,
                     color=(120, 120, 140))
        _add_input(parent, tag=f"pe_plugin_target_{note}",
                   default_value=current_target,
                   hint="e.g. Spotify or OBS:scene_screen", width=-1)


def _on_plugin_combo_changed(note, new_plugin):
    """Rebuild the action combo when plugin selection changes."""
    tag = f"pe_fields_{note}"
    if not dpg.does_item_exist(tag):
        return
    dpg.delete_item(tag, children_only=True)

    class _FakeMapping:
        class action:
            type = "plugin"
            target = new_plugin
            keys = ""
            command = ""
            process = ""
        label = ""

    _build_fields(note, "plugin", _FakeMapping())


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
    elif atype == "plugin":
        data["target"] = _collect_plugin_target(note)
    if dpg.does_item_exist(f"pe_hotkey_{note}"):
        data["hotkey"] = dpg.get_value(f"pe_hotkey_{note}") or ""
    return data


def _collect_plugin_target(note) -> str:
    """Build 'PluginName:action_id' from the combo widgets."""
    plugin_name = ""
    if dpg.does_item_exist(f"pe_plugin_name_{note}"):
        plugin_name = dpg.get_value(f"pe_plugin_name_{note}") or ""

    if dpg.does_item_exist(f"pe_plugin_action_{note}"):
        raw = dpg.get_value(f"pe_plugin_action_{note}") or ""
        action_id = raw.split("  —  ")[0].strip() if "  —  " in raw else raw.strip()
        if plugin_name and action_id:
            return f"{plugin_name}:{action_id}"
        return plugin_name or action_id

    if dpg.does_item_exist(f"pe_plugin_target_{note}"):
        return dpg.get_value(f"pe_plugin_target_{note}") or ""

    return plugin_name
