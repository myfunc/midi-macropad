"""Quick Action Picker — flat grouped list for fast pad assignment."""
from __future__ import annotations

from typing import Any, Callable

_on_save_cb: Callable | None = None
_current_note: int | None = None
_cached_catalogs: dict[str, list[dict]] | None = None

# System action definitions
SYSTEM_ACTIONS = [
    {"group": "Hotkeys", "id": "keystroke", "label": "Keystroke", "fields": ["keys"]},
    {"group": "Hotkeys", "id": "app_keystroke", "label": "App Keystroke", "fields": ["keys", "process"]},
    {"group": "System", "id": "shell", "label": "Shell Command", "fields": ["command"]},
    {"group": "System", "id": "launch", "label": "Launch App", "fields": ["command"]},
    {"group": "System", "id": "volume", "label": "Volume Control", "fields": ["target"]},
    {"group": "System", "id": "scroll", "label": "Mouse Scroll", "fields": []},
]


def set_catalogs(catalogs: dict[str, list[dict]] | None) -> None:
    """Cache plugin action catalogs for building the picker."""
    global _cached_catalogs
    _cached_catalogs = catalogs


def build_quick_picker(
    parent: str,
    note: int,
    current_label: str,
    current_action_type: str,
    current_action_data: dict,
    current_hotkey: str,
    on_save: Callable | None = None,
    plugin_manager=None,
) -> None:
    """Build the quick action picker in the properties panel."""
    import dearpygui.dearpygui as dpg

    global _on_save_cb, _current_note
    _on_save_cb = on_save
    _current_note = note

    if plugin_manager:
        set_catalogs(plugin_manager.get_all_action_catalogs())

    # Header
    dpg.add_text(f"Pad {note - 15}  (note {note})", parent=parent, color=(110, 190, 255))
    dpg.add_separator(parent=parent)
    dpg.add_spacer(height=4, parent=parent)

    # Current assignment display
    current_desc = _describe_action(current_action_type, current_action_data)
    dpg.add_text(f"Current: {current_desc}", parent=parent, color=(180, 180, 200), wrap=260)
    dpg.add_spacer(height=6, parent=parent)

    # Label field
    dpg.add_text("Label", parent=parent, color=(150, 150, 165))
    dpg.add_input_text(
        tag=f"qap_label_{note}",
        parent=parent,
        default_value=current_label,
        width=-1,
        on_enter=True,
        callback=lambda s, v: _on_field_changed(note, "label", v),
    )

    # Hotkey field
    dpg.add_text("Hotkey", parent=parent, color=(150, 150, 165))
    dpg.add_input_text(
        tag=f"qap_hotkey_{note}",
        parent=parent,
        default_value=current_hotkey,
        hint="e.g. mouse5, f13, ctrl+shift+r",
        width=-1,
        on_enter=True,
        callback=lambda s, v: _on_field_changed(note, "hotkey", v),
    )

    # Extra fields container (shown when action needs parameters)
    dpg.add_spacer(height=4, parent=parent)
    dpg.add_group(tag=f"qap_extra_{note}", parent=parent)
    if current_action_type and current_action_type not in ("plugin", "scroll"):
        _build_extra_fields(dpg, note, current_action_type, current_action_data)

    dpg.add_spacer(height=8, parent=parent)
    dpg.add_separator(parent=parent)
    dpg.add_spacer(height=4, parent=parent)

    # === Action Groups ===
    dpg.add_text("Assign Action", parent=parent, color=(150, 150, 165))
    dpg.add_spacer(height=4, parent=parent)

    # System actions grouped
    last_group = ""
    for action_def in SYSTEM_ACTIONS:
        group = action_def["group"]
        if group != last_group:
            dpg.add_text(f"-- {group} --", parent=parent, color=(100, 100, 120))
            last_group = group
        is_current = current_action_type == action_def["id"]
        label = f"> {action_def['label']}" if is_current else f"  {action_def['label']}"
        color = (110, 190, 255) if is_current else (200, 200, 220)
        dpg.add_button(
            label=label,
            parent=parent,
            width=-1,
            small=True,
            callback=lambda s, a, u=action_def: _on_action_selected(note, u["id"], u["fields"]),
        )

    # Plugin actions
    if _cached_catalogs:
        for plugin_name, actions in sorted(_cached_catalogs.items()):
            if not actions:
                continue
            dpg.add_spacer(height=4, parent=parent)
            dpg.add_text(f"-- {plugin_name} --", parent=parent, color=(100, 100, 120))
            for action in actions:
                action_id = action.get("id", "")
                action_label = action.get("label", action_id)
                target = f"{plugin_name}:{action_id}"
                is_current = (
                    current_action_type == "plugin"
                    and current_action_data.get("target", "") == target
                )
                label = f"> {action_label}" if is_current else f"  {action_label}"
                desc = action.get("description", "")
                dpg.add_button(
                    label=label,
                    parent=parent,
                    width=-1,
                    small=True,
                    callback=lambda s, a, t=target, l=action_label: _on_plugin_action_selected(note, t, l),
                )
                if desc:
                    dpg.add_text(f"    {desc}", parent=parent, color=(100, 100, 120), wrap=240)

    # Clear button
    dpg.add_spacer(height=8, parent=parent)
    dpg.add_separator(parent=parent)
    dpg.add_button(
        label="Clear Assignment",
        parent=parent,
        width=-1,
        callback=lambda: _on_clear(note),
    )


def _describe_action(action_type: str, action_data: dict) -> str:
    """Human-readable description of current action."""
    if not action_type:
        return "(not assigned)"
    if action_type == "keystroke":
        return f"Keystroke: {action_data.get('keys', '?')}"
    if action_type == "app_keystroke":
        return f"App Keystroke: {action_data.get('keys', '?')} -> {action_data.get('process', '?')}"
    if action_type in ("shell", "launch"):
        return f"{action_type.title()}: {action_data.get('command', '?')}"
    if action_type == "volume":
        return f"Volume: {action_data.get('target', '?')}"
    if action_type == "scroll":
        return "Mouse Scroll"
    if action_type == "plugin":
        return f"Plugin: {action_data.get('target', '?')}"
    return action_type


def _build_extra_fields(dpg, note: int, action_type: str, action_data: dict) -> None:
    """Build inline input fields for actions that need parameters."""
    parent = f"qap_extra_{note}"
    if not dpg.does_item_exist(parent):
        return

    field_map = {
        "keystroke": [("keys", "Keys (e.g. ctrl+c)", "keys")],
        "app_keystroke": [
            ("keys", "Keys (e.g. ctrl+c)", "keys"),
            ("process", "Process (e.g. Spotify.exe)", "process"),
        ],
        "shell": [("command", "Shell command", "command")],
        "launch": [("command", "Application path", "command")],
        "volume": [("target", "Target (master, mic, app:Name)", "target")],
    }

    fields = field_map.get(action_type, [])
    for field_key, hint, data_key in fields:
        tag = f"qap_field_{note}_{field_key}"
        dpg.add_input_text(
            tag=tag,
            parent=parent,
            default_value=action_data.get(data_key, ""),
            hint=hint,
            width=-1,
            on_enter=True,
            callback=lambda s, v, dk=data_key: _on_extra_field_changed(note, dk, v),
        )


def _on_action_selected(note: int, action_type: str, fields: list[str]) -> None:
    """User clicked a system action."""
    import dearpygui.dearpygui as dpg

    # Clear extra fields and rebuild
    extra_tag = f"qap_extra_{note}"
    if dpg.does_item_exist(extra_tag):
        dpg.delete_item(extra_tag, children_only=True)

    action_data: dict[str, str] = {}
    if fields:
        _build_extra_fields(dpg, note, action_type, action_data)

    _do_save(note, action_type, action_data)


def _on_plugin_action_selected(note: int, target: str, label: str) -> None:
    """User clicked a plugin action."""
    import dearpygui.dearpygui as dpg

    extra_tag = f"qap_extra_{note}"
    if dpg.does_item_exist(extra_tag):
        dpg.delete_item(extra_tag, children_only=True)

    # Auto-set label to plugin action label
    label_tag = f"qap_label_{note}"
    if dpg.does_item_exist(label_tag):
        current_label = dpg.get_value(label_tag)
        if not current_label or current_label.startswith("Pad "):
            dpg.set_value(label_tag, label)

    _do_save(note, "plugin", {"target": target})


def _on_clear(note: int) -> None:
    """Clear the pad assignment."""
    import dearpygui.dearpygui as dpg

    extra_tag = f"qap_extra_{note}"
    if dpg.does_item_exist(extra_tag):
        dpg.delete_item(extra_tag, children_only=True)

    label_tag = f"qap_label_{note}"
    if dpg.does_item_exist(label_tag):
        dpg.set_value(label_tag, "")

    _do_save(note, "", {})


def _on_field_changed(note: int, field: str, value: str) -> None:
    """Label or hotkey changed — re-save with current action."""
    if _on_save_cb:
        _on_save_cb(note, {field: value.strip()})


def _on_extra_field_changed(note: int, data_key: str, value: str) -> None:
    """Extra field (keys, process, command) changed — re-save."""
    import dearpygui.dearpygui as dpg

    # Collect current state and save
    label_tag = f"qap_label_{note}"
    hotkey_tag = f"qap_hotkey_{note}"
    label = dpg.get_value(label_tag) if dpg.does_item_exist(label_tag) else ""
    hotkey = dpg.get_value(hotkey_tag) if dpg.does_item_exist(hotkey_tag) else ""

    if _on_save_cb:
        _on_save_cb(note, {"extra_field": data_key, "extra_value": value.strip()})


def _do_save(note: int, action_type: str, action_data: dict) -> None:
    """Execute save callback with collected data."""
    import dearpygui.dearpygui as dpg

    label_tag = f"qap_label_{note}"
    hotkey_tag = f"qap_hotkey_{note}"
    label = dpg.get_value(label_tag) if dpg.does_item_exist(label_tag) else ""
    hotkey = dpg.get_value(hotkey_tag) if dpg.does_item_exist(hotkey_tag) else ""

    if _on_save_cb:
        _on_save_cb(note, {
            "label": label.strip(),
            "action_type": action_type,
            "action_data": action_data,
            "hotkey": hotkey.strip(),
        })
