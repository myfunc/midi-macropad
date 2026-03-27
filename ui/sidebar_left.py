"""Left sidebar -- mode switcher, device status, plugin toggles, profiles."""
import dearpygui.dearpygui as dpg
import settings
from ui.dashboard import hex_to_rgb
from ui import selection

_mode_callback = None
_plugin_toggle_callback = None
_mode_btn_tags: list[str] = []
_mode_icon_tags: list[str] = []
_mode_colors: list[tuple[int, int, int]] = []
_mode_icons: list[str] = []
_active_index: int = 0
_manual_override: bool = False
_mode_count: int = 0
_mode_themes: dict[tuple[int, bool, bool], int] = {}


def _get_mode_theme(mode_index: int, active: bool, is_icon: bool) -> int:
    key = (mode_index, active, is_icon)
    if key in _mode_themes:
        return _mode_themes[key]
    r, g, b = (
        _mode_colors[mode_index]
        if mode_index < len(_mode_colors)
        else (100, 100, 120)
    )
    if is_icon:
        if active:
            with dpg.theme() as icon_t:
                with dpg.theme_component(dpg.mvButton):
                    dpg.add_theme_color(dpg.mvThemeCol_Button, (r, g, b, 120))
                    dpg.add_theme_color(dpg.mvThemeCol_Text, (255, 255, 255, 255))
                    dpg.add_theme_color(
                        dpg.mvThemeCol_ButtonHovered, (r, g, b, 150)
                    )
                    dpg.add_theme_color(
                        dpg.mvThemeCol_ButtonActive, (r, g, b, 180)
                    )
                    dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6)
                    dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 4, 4)
        else:
            with dpg.theme() as icon_t:
                with dpg.theme_component(dpg.mvButton):
                    dpg.add_theme_color(dpg.mvThemeCol_Button, (r, g, b, 25))
                    dpg.add_theme_color(dpg.mvThemeCol_Text, (r, g, b, 140))
                    dpg.add_theme_color(
                        dpg.mvThemeCol_ButtonHovered, (r, g, b, 45)
                    )
                    dpg.add_theme_color(
                        dpg.mvThemeCol_ButtonActive, (r, g, b, 65)
                    )
                    dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6)
                    dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 4, 4)
        _mode_themes[key] = icon_t
        return icon_t
    if active:
        with dpg.theme() as btn_t:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (r, g, b, 45))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (255, 255, 255, 255))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (r, g, b, 65))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (r, g, b, 80))
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 4)
    else:
        with dpg.theme() as btn_t:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 0, 0, 0))
                dpg.add_theme_color(dpg.mvThemeCol_Text, (r, g, b, 140))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (r, g, b, 28))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (r, g, b, 45))
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 6)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 4)
    _mode_themes[key] = btn_t
    return btn_t


def create_left_sidebar(parent="panel_left", *, mode_names, mode_colors,
                        mode_icons=None, callback=None,
                        plugin_toggle_callback=None):
    global _mode_callback, _plugin_toggle_callback, _mode_count
    for theme_id in _mode_themes.values():
        if dpg.does_item_exist(theme_id):
            dpg.delete_item(theme_id)
    _mode_themes.clear()
    _mode_callback = callback
    _plugin_toggle_callback = plugin_toggle_callback
    _mode_btn_tags.clear()
    _mode_icon_tags.clear()
    _mode_colors.clear()
    _mode_icons.clear()

    if mode_icons is None:
        mode_icons = [""] * len(mode_names)
    _mode_count = len(mode_names)

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
        dpg.add_spacer(height=6)

        for i, (name, color, icon) in enumerate(zip(mode_names, mode_colors, mode_icons)):
            btn_tag = f"sl_mode_{i}"
            icon_tag = f"sl_mode_icon_{i}"
            r, g, b = hex_to_rgb(color)
            _mode_colors.append((r, g, b))
            _mode_icons.append(icon)

            def _make_cb(idx):
                return lambda: _on_mode_click(idx)

            with dpg.group(horizontal=True):
                dpg.add_spacer(width=4)
                dpg.add_button(
                    tag=icon_tag,
                    label=f" {icon} " if icon else "   ",
                    width=42, height=32,
                    callback=_make_cb(i),
                )
                dpg.add_button(
                    label=f" {name}",
                    tag=btn_tag, width=-1, height=32,
                    callback=_make_cb(i),
                )
            dpg.add_spacer(height=2)

            _mode_btn_tags.append(btn_tag)
            _mode_icon_tags.append(icon_tag)

        dpg.add_spacer(height=8)
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
        dpg.set_value("sl_profile_status", f"Copied -> {new_name}")
        dpg.configure_item("sl_profile_status", color=(100, 255, 150))


def _on_mode_click(index):
    global _manual_override, _active_index
    _manual_override = True
    _active_index = index
    _update_mode_highlight(index)
    if _mode_callback:
        _mode_callback(index)


def _update_mode_highlight(index):
    for i, (btn_tag, icon_tag) in enumerate(zip(_mode_btn_tags, _mode_icon_tags)):
        if not dpg.does_item_exist(btn_tag):
            continue
        active = i == index
        btn_t = _get_mode_theme(i, active, False)
        icon_t = _get_mode_theme(i, active, True)
        dpg.bind_item_theme(btn_tag, btn_t)
        dpg.bind_item_theme(icon_tag, icon_t)


def set_active_mode(index):
    global _active_index
    _active_index = index
    _update_mode_highlight(index)


def get_mode_count() -> int:
    return _mode_count


def is_manual_override():
    return _manual_override
