"""Bottom taskbar — window toggle buttons + status text."""
import dearpygui.dearpygui as dpg
from ui import dashboard

_btn_tags: dict[str, str] = {}
_last_states: dict[str, bool] = {}


def create_taskbar():
    with dpg.window(label="Taskbar", tag="win_taskbar", height=32,
                    width=1200, pos=[0, 730],
                    no_close=True, no_collapse=True):
        with dpg.group(horizontal=True):
            dpg.add_group(tag="tb_buttons", horizontal=True)
            dpg.add_spacer(width=20)
            dpg.add_text("Hover over a control for info",
                         tag="status_bar_text", color=(80, 80, 100))

    dashboard.register_window("Taskbar", "win_taskbar")


def add_toggle_button(name: str):
    """Add a window toggle button for the given window name."""
    btn_tag = f"tb_btn_{name.replace(' ', '_').lower()}"

    def _on_click():
        dashboard.toggle_window_visibility(name)

    dpg.add_button(label=name, tag=btn_tag, parent="tb_buttons",
                   small=True, callback=_on_click)
    _btn_tags[name] = btn_tag


def poll():
    """Update toggle button highlight state (call once per frame)."""
    for name, btn_tag in _btn_tags.items():
        if not dpg.does_item_exist(btn_tag):
            continue
        visible = dashboard.is_window_visible(name)
        if _last_states.get(name) == visible:
            continue
        _last_states[name] = visible
        if visible:
            with dpg.theme() as t:
                with dpg.theme_component(dpg.mvButton):
                    dpg.add_theme_color(dpg.mvThemeCol_Button, (45, 58, 85))
            dpg.bind_item_theme(btn_tag, t)
        else:
            dpg.bind_item_theme(btn_tag, 0)
