"""Main DearPyGui dashboard -- native title bar, fixed 3-panel layout."""
import os
import ctypes
from pathlib import Path
import dearpygui.dearpygui as dpg

_ICON_PATH = str(Path(__file__).resolve().parents[1] / "app_icon.ico")

LEFT_W = 184
RIGHT_W = 274
STATUS_H = 26

_user32 = ctypes.windll.user32
_hwnd = 0


def _get_hwnd():
    global _hwnd
    if not _hwnd:
        _hwnd = _user32.FindWindowW(None, "MIDI Macropad")
    return _hwnd


def _load_font():
    windir = os.environ.get("WINDIR", r"C:\Windows")
    for name in ("seguisym.ttf", "segoeui.ttf", "arial.ttf", "tahoma.ttf"):
        path = os.path.join(windir, "Fonts", name)
        if os.path.isfile(path):
            with dpg.font_registry():
                with dpg.font(path, 16) as font:
                    dpg.add_font_range_hint(dpg.mvFontRangeHint_Default)
                    dpg.add_font_range_hint(dpg.mvFontRangeHint_Cyrillic)
                    dpg.add_font_range(0x2000, 0x206F)
                    dpg.add_font_range(0x2190, 0x21FF)
                    dpg.add_font_range(0x25A0, 0x25FF)
                    dpg.add_font_range(0x2700, 0x27BF)
            return font
    return None


def setup_theme():
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (22, 22, 28))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (26, 26, 33))
            dpg.add_theme_color(dpg.mvThemeCol_PopupBg, (30, 30, 38))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (36, 36, 46))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (46, 46, 60))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (56, 56, 72))
            dpg.add_theme_color(dpg.mvThemeCol_Button, (40, 40, 52))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (52, 52, 68))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (62, 62, 80))
            dpg.add_theme_color(dpg.mvThemeCol_Text, (200, 204, 212))
            dpg.add_theme_color(dpg.mvThemeCol_TextDisabled, (85, 88, 100))
            dpg.add_theme_color(dpg.mvThemeCol_Tab, (30, 30, 40))
            dpg.add_theme_color(dpg.mvThemeCol_TabHovered, (48, 50, 66))
            dpg.add_theme_color(dpg.mvThemeCol_TabActive, (42, 46, 66))
            dpg.add_theme_color(dpg.mvThemeCol_Header, (40, 40, 52))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (50, 50, 66))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderActive, (58, 58, 76))
            dpg.add_theme_color(dpg.mvThemeCol_Separator, (36, 36, 46))
            dpg.add_theme_color(dpg.mvThemeCol_Border, (36, 36, 46))
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrab, (75, 85, 165))
            dpg.add_theme_color(dpg.mvThemeCol_SliderGrabActive, (95, 105, 195))
            dpg.add_theme_color(dpg.mvThemeCol_CheckMark, (85, 145, 240))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarBg, (22, 22, 28))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrab, (42, 42, 54))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabHovered, (55, 55, 70))
            dpg.add_theme_color(dpg.mvThemeCol_ScrollbarGrabActive, (65, 65, 82))
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)
            dpg.add_theme_style(dpg.mvStyleVar_WindowRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 0)
            dpg.add_theme_style(dpg.mvStyleVar_TabRounding, 3)
            dpg.add_theme_style(dpg.mvStyleVar_GrabRounding, 2)
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarRounding, 4)
            dpg.add_theme_style(dpg.mvStyleVar_ScrollbarSize, 10)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 0, 0)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 4)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 6, 4)
            dpg.add_theme_style(dpg.mvStyleVar_WindowBorderSize, 0)
            dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 1)
    return t


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def create_dashboard(width=2400, height=1560):
    dpg.create_viewport(
        title="MIDI Macropad",
        width=width, height=height,
        min_width=900, min_height=600,
        decorated=True,
        small_icon=_ICON_PATH,
        large_icon=_ICON_PATH,
    )
    font = _load_font()
    if font:
        dpg.bind_font(font)


def create_layout():
    """Build the fixed 3-panel layout inside a primary window."""
    vp_w = dpg.get_viewport_width()
    center_w = max(300, vp_w - LEFT_W - RIGHT_W - 18)

    with dpg.window(tag="primary_window", no_title_bar=True,
                    no_scrollbar=True, no_move=True, no_resize=True,
                    no_collapse=True):
        with dpg.group(horizontal=True, tag="main_panels"):
            dpg.add_child_window(tag="panel_left", width=LEFT_W, border=False)
            with dpg.child_window(tag="panel_center", width=center_w,
                                  border=False):
                pass
            dpg.add_child_window(tag="panel_right", width=RIGHT_W,
                                 border=False)

        with dpg.child_window(tag="panel_status", height=STATUS_H,
                              border=False, no_scrollbar=True):
            with dpg.group(horizontal=True):
                dpg.add_spacer(width=6)
                dpg.add_text("", tag="device_status", color=(255, 180, 80))
                dpg.add_spacer(width=16)
                dpg.add_text("", tag="plugin_status_text",
                             color=(120, 120, 140))
                dpg.add_spacer(width=16)
                dpg.add_text("", tag="status_bar_text", color=(75, 78, 95))

    dpg.set_primary_window("primary_window", True)


def create_center_content():
    """Create the pad area (pads + knobs + mixer) + full-width tabs."""
    # Tall enough for 2×4 pads + compact mixer beside knobs (~380px in layout spec).
    dpg.add_child_window(tag="pad_area", parent="panel_center",
                         height=400, border=False)
    dpg.add_spacer(height=2, parent="panel_center")

    with dpg.child_window(tag="tabs_side", width=-1, height=-1,
                          parent="panel_center"):
        dpg.add_tab_bar(tag="center_tabs")
        with dpg.tab(label="  Log  ", parent="center_tabs", tag="tab_log"):
            dpg.add_child_window(tag="log_content", height=-1, border=False)


def add_plugin_tab(tab_id: str, label: str) -> str:
    """Add a plugin tab to the center tab bar. Returns the content tag."""
    tag = f"tab_plugin_{tab_id}"
    content = f"tab_plugin_content_{tab_id}"
    with dpg.tab(label=f"  {label}  ", parent="center_tabs", tag=tag):
        dpg.add_child_window(tag=content, height=-1, border=False)
    return content


def _set_window_icon(hwnd: int) -> None:
    """Load app_icon.ico and apply it to the window via WM_SETICON."""
    if not os.path.isfile(_ICON_PATH):
        return
    try:
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        LR_DEFAULTSIZE = 0x0040
        shell32 = ctypes.windll.shell32  # noqa: F841
        hicon_sm = _user32.LoadImageW(
            0, _ICON_PATH, IMAGE_ICON, 16, 16, LR_LOADFROMFILE,
        )
        hicon_lg = _user32.LoadImageW(
            0, _ICON_PATH, IMAGE_ICON, 48, 48, LR_LOADFROMFILE | LR_DEFAULTSIZE,
        )
        if hicon_sm:
            _user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_sm)
        if hicon_lg:
            _user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_lg)
    except Exception:
        pass


def post_setup():
    """Apply DWM dark title bar, square corners, and custom icon."""
    hwnd = _get_hwnd()
    if not hwnd:
        return
    try:
        dwm = ctypes.windll.dwmapi
        val = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), 4)
        val = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(val), 4)
        val = ctypes.c_int(0x001C1616)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(val), 4)
        val = ctypes.c_int(0x002E2424)
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(val), 4)
    except Exception:
        pass
    _set_window_icon(hwnd)


_last_vp_size = (0, 0)
_on_resize_cb = None


def set_resize_callback(cb):
    global _on_resize_cb
    _on_resize_cb = cb


def get_viewport_size() -> tuple[int, int]:
    return dpg.get_viewport_width(), dpg.get_viewport_height()


def poll():
    """Update panel sizes to track viewport dimensions."""
    global _last_vp_size
    vp_w = dpg.get_viewport_client_width()
    vp_h = dpg.get_viewport_client_height()
    center_w = max(300, vp_w - LEFT_W - RIGHT_W - 18)
    panel_h = max(200, vp_h - STATUS_H - 18)

    if dpg.does_item_exist("panel_left"):
        dpg.configure_item("panel_left", height=panel_h)
    if dpg.does_item_exist("panel_center"):
        dpg.configure_item("panel_center", width=center_w, height=panel_h)
    if dpg.does_item_exist("panel_right"):
        dpg.configure_item("panel_right", height=panel_h)

    full_w, full_h = dpg.get_viewport_width(), dpg.get_viewport_height()
    if (full_w, full_h) != _last_vp_size and _last_vp_size != (0, 0):
        if _on_resize_cb:
            _on_resize_cb(full_w, full_h)
    _last_vp_size = (full_w, full_h)
