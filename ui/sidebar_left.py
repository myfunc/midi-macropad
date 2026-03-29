"""Left sidebar -- pad preset selector, device status, plugin toggles, profiles."""
import dearpygui.dearpygui as dpg
import settings

_preset_callback = None
_plugin_toggle_callback = None
_preset_count: int = 0
_preset_changing: bool = False


def create_left_sidebar(
    parent="panel_left",
    *,
    preset_names,
    callback=None,
    plugin_toggle_callback=None,
):
    global _preset_callback, _plugin_toggle_callback, _preset_count
    _preset_callback = callback
    _plugin_toggle_callback = plugin_toggle_callback
    names = list(preset_names) if preset_names is not None else []
    _preset_count = len(names)

    with dpg.child_window(parent=parent, border=False):
        dpg.add_spacer(height=6)
        dpg.add_text("  PROFILE", color=(75, 78, 95))
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=False):
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=6)
                dpg.add_combo(
                    items=settings.list_profiles(),
                    default_value=settings.active_profile(),
                    tag="sl_profile_combo",
                    width=-1,
                    callback=_on_profile_change,
                )
            dpg.add_spacer(height=2)
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=6)
                dpg.add_input_text(
                    tag="sl_profile_name_input",
                    hint="new profile name",
                    width=110,
                    on_enter=True,
                    callback=lambda s, a: _on_profile_copy(s, a),
                )
                dpg.add_button(label="Copy", callback=_on_profile_copy, width=42)
            dpg.add_spacer(height=2)
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=6)
                dpg.add_text("", tag="sl_profile_status", color=(100, 255, 150))

        dpg.add_spacer(height=10)
        dpg.add_text("  PAD PRESET", color=(75, 78, 95))
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=6)
            default_preset = names[0] if names else ""
            dpg.add_combo(
                tag="sl_preset_combo",
                items=names,
                default_value=default_preset,
                width=-1,
                callback=_on_preset_combo,
            )

        dpg.add_spacer(height=14)
        dpg.add_text("  DEVICE", color=(75, 78, 95))
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=6)
            dpg.add_text(
                "",
                tag="sl_midi_device",
                color=(100, 102, 118),
                wrap=165,
            )

        dpg.add_spacer(height=14)
        dpg.add_text("  PLUGINS", color=(75, 78, 95))
        dpg.add_spacer(height=4)
        dpg.add_group(tag="sl_plugin_list")


def populate_plugins(discovered, loaded_names):
    if not dpg.does_item_exist("sl_plugin_list"):
        return
    dpg.delete_item("sl_plugin_list", children_only=True)
    for info in discovered:
        name = info["name"]
        is_loaded = name in loaded_names

        def _make_cb(n, iref):
            def cb(sender, app_data):
                if _plugin_toggle_callback:
                    _plugin_toggle_callback(n, iref, app_data)
            return cb

        with dpg.group(horizontal=True, parent="sl_plugin_list"):
            dpg.add_spacer(width=6)
            dpg.add_checkbox(
                label=name,
                tag=f"sl_plugin_{name}",
                default_value=is_loaded,
                callback=_make_cb(name, info),
            )


def set_active_preset(index: int):
    global _preset_changing
    if not dpg.does_item_exist("sl_preset_combo"):
        return
    cfg = dpg.get_item_configuration("sl_preset_combo") or {}
    items = list(cfg.get("items") or [])
    if not items or index < 0 or index >= len(items):
        return
    _preset_changing = True
    try:
        dpg.set_value("sl_preset_combo", items[index])
    finally:
        _preset_changing = False


def get_preset_count() -> int:
    return _preset_count


def _on_preset_combo(sender, app_data):
    if _preset_changing or not _preset_callback:
        return
    cfg = dpg.get_item_configuration(sender) or {}
    items = list(cfg.get("items") or [])
    if not items:
        return
    try:
        idx = items.index(app_data)
    except (ValueError, TypeError):
        return
    _preset_callback(idx)


def _on_profile_change(sender, app_data):
    settings.save_profile()
    settings.load_profile(app_data)
    if dpg.does_item_exist("sl_profile_status"):
        dpg.set_value("sl_profile_status", "Restart to apply")
        dpg.configure_item("sl_profile_status", color=(255, 180, 80))


def _on_profile_copy(sender, app_data):
    new_name = dpg.get_value("sl_profile_name_input")
    if not new_name or not new_name.strip():
        return
    new_name = new_name.strip()
    settings.copy_profile(settings.active_profile(), new_name)
    dpg.configure_item("sl_profile_combo", items=settings.list_profiles())
    dpg.set_value("sl_profile_name_input", "")
    if dpg.does_item_exist("sl_profile_status"):
        dpg.set_value("sl_profile_status", f"Copied -> {new_name}")
        dpg.configure_item("sl_profile_status", color=(100, 255, 150))
