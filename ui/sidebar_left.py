"""Left sidebar -- mode switcher, device status, plugin toggles, profiles."""
import dearpygui.dearpygui as dpg
import settings
from ui.dashboard import hex_to_rgb
from ui import selection

_mode_callback = None
_plugin_toggle_callback = None
_mode_btn_tags: list[str] = []
_mode_colors: list[tuple[int, int, int]] = []
_active_index: int = 0
_manual_override: bool = False


def create_left_sidebar(parent="panel_left", *, mode_names, mode_colors,
                        callback=None, plugin_toggle_callback=None):
    global _mode_callback, _plugin_toggle_callback
    _mode_callback = callback
    _plugin_toggle_callback = plugin_toggle_callback
    _mode_btn_tags.clear()
    _mode_colors.clear()

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
        dpg.add_text("  MODES", color=(75, 78, 95))
        dpg.add_spacer(height=4)

        for i, (name, color) in enumerate(zip(mode_names, mode_colors)):
            tag = f"sl_mode_{i}"
            r, g, b = hex_to_rgb(color)
            _mode_colors.append((r, g, b))

            def _make_cb(idx):
                return lambda: _on_mode_click(idx)

            dpg.add_button(label=f"   {name}", tag=tag, width=-1, height=28,
                           callback=_make_cb(i))
            _mode_btn_tags.append(tag)

        dpg.add_spacer(height=10)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=6)
            dpg.add_text("Flip:", color=(75, 78, 95))
            dpg.add_button(label=" Normal ", tag="inverted_btn", small=True)

        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=6)
            dpg.add_text("", tag="current_mode_label", color=(100, 180, 255))

        dpg.add_spacer(height=14)
        dpg.add_text("  DEVICE", color=(75, 78, 95))
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=6)
            dpg.add_text("", tag="active_app_label", color=(100, 102, 118),
                         wrap=165)

        dpg.add_spacer(height=14)
        dpg.add_text("  PLUGINS", color=(75, 78, 95))
        dpg.add_spacer(height=4)
        dpg.add_group(tag="sl_plugin_list")

    _update_mode_highlight(0)


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
            dpg.add_checkbox(label=name, tag=f"sl_plugin_{name}",
                             default_value=is_loaded,
                             callback=_make_cb(name, info))


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
        dpg.set_value("sl_profile_status", f"Copied → {new_name}")
        dpg.configure_item("sl_profile_status", color=(100, 255, 150))


def _on_mode_click(index):
    global _manual_override, _active_index
    _manual_override = True
    _active_index = index
    _update_mode_highlight(index)
    if _mode_callback:
        _mode_callback(index)


def _update_mode_highlight(index):
    for i, tag in enumerate(_mode_btn_tags):
        if not dpg.does_item_exist(tag):
            continue
        r, g, b = _mode_colors[i] if i < len(_mode_colors) else (100, 100, 120)
        if i == index:
            with dpg.theme() as t:
                with dpg.theme_component(dpg.mvButton):
                    dpg.add_theme_color(dpg.mvThemeCol_Button, (r, g, b, 40))
                    dpg.add_theme_color(dpg.mvThemeCol_Text, (r, g, b, 255))
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,
                                        (r, g, b, 60))
            dpg.bind_item_theme(tag, t)
        else:
            with dpg.theme() as t:
                with dpg.theme_component(dpg.mvButton):
                    dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 0, 0, 0))
                    dpg.add_theme_color(dpg.mvThemeCol_Text,
                                        (r, g, b, 140))
                    dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered,
                                        (r, g, b, 28))
            dpg.bind_item_theme(tag, t)


def set_active_mode(index):
    global _active_index
    _active_index = index
    _update_mode_highlight(index)


def is_manual_override():
    return _manual_override


def clear_manual_override():
    global _manual_override
    _manual_override = False
