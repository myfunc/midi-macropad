"""Top toolbar — Preset selector, Profile selector, Settings button."""
import dearpygui.dearpygui as dpg
import settings
from ui.dashboard import get_text_font

_preset_callback = None
_settings_callback = None
_preset_count: int = 0
_preset_changing: bool = False

TOOLBAR_H = 34


def create_toolbar(
    parent="panel_center",
    *,
    preset_names,
    preset_callback=None,
    settings_callback=None,
):
    global _preset_callback, _settings_callback, _preset_count
    _preset_callback = preset_callback
    _settings_callback = settings_callback
    names = list(preset_names) if preset_names else []
    _preset_count = len(names)

    with dpg.child_window(parent=parent, height=TOOLBAR_H,
                          border=False, no_scrollbar=True, tag="toolbar"):
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=8)

            dpg.add_text("Preset", color=(85, 88, 100))
            dpg.add_combo(
                tag="tb_preset_combo",
                items=names,
                default_value=names[0] if names else "",
                width=160,
                callback=_on_preset_combo,
            )

            dpg.add_spacer(width=16)
            dpg.add_text("Profile", color=(85, 88, 100))
            dpg.add_combo(
                tag="tb_profile_combo",
                items=settings.list_profiles(),
                default_value=settings.active_profile(),
                width=140,
                callback=_on_profile_change,
            )
            profile_input = dpg.add_input_text(
                tag="tb_profile_name",
                hint="new name",
                width=100,
                on_enter=True,
                callback=lambda s, a: _on_profile_copy(s, a),
            )
            tf = get_text_font()
            if tf:
                dpg.bind_item_font(profile_input, tf)
            dpg.add_button(label="Copy", callback=_on_profile_copy, width=42)

            dpg.add_spacer(width=-1)
            dpg.add_button(
                label="\u2699 Settings",
                callback=_on_settings_click,
                width=90,
            )
            dpg.add_spacer(width=8)


def set_active_preset(index: int):
    global _preset_changing
    if not dpg.does_item_exist("tb_preset_combo"):
        return
    cfg = dpg.get_item_configuration("tb_preset_combo") or {}
    items = list(cfg.get("items") or [])
    if not items or index < 0 or index >= len(items):
        return
    _preset_changing = True
    try:
        dpg.set_value("tb_preset_combo", items[index])
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


def _on_profile_copy(sender, app_data):
    new_name = dpg.get_value("tb_profile_name")
    if not new_name or not new_name.strip():
        return
    new_name = new_name.strip()
    settings.copy_profile(settings.active_profile(), new_name)
    dpg.configure_item("tb_profile_combo", items=settings.list_profiles())
    dpg.set_value("tb_profile_name", "")


def _on_settings_click(sender=None, app_data=None):
    if _settings_callback:
        _settings_callback()
