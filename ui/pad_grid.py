"""Visual 2x4 pad grid with click-to-select."""
import dearpygui.dearpygui as dpg
import time
from ui import selection

PAD_SIZE = 88
PAD_SPACING = 5
PAD_NOTES = [16, 17, 18, 19, 20, 21, 22, 23]

_pad_tags: dict[int, str] = {}
_pad_press_times: dict[int, float] = {}
_default_color = (40, 40, 52, 255)
_press_color = (100, 180, 255, 255)


def create_pad_grid(parent="pad_area"):
    _pad_tags.clear()
    _pad_press_times.clear()

    with dpg.group(parent=parent):
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=8)
            with dpg.group():
                with dpg.group(horizontal=True):
                    for i in range(4, 8):
                        _create_pad_button(PAD_NOTES[i], f"Pad {i + 1}")
                dpg.add_spacer(height=PAD_SPACING)
                with dpg.group(horizontal=True):
                    for i in range(4):
                        _create_pad_button(PAD_NOTES[i], f"Pad {i + 1}")


def _create_pad_button(note: int, default_label: str):
    tag = f"pad_btn_{note}"

    with dpg.theme() as pad_theme:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, _default_color)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 5)

    def _on_click():
        selection.select("pad", note)

    dpg.add_button(label=default_label, tag=tag,
                   width=PAD_SIZE, height=PAD_SIZE, callback=_on_click)
    dpg.bind_item_theme(tag, pad_theme)
    _pad_tags[note] = tag


def clear_pad_labels():
    for i, note in enumerate(PAD_NOTES):
        if note in _pad_tags:
            dpg.set_item_label(_pad_tags[note], f"Pad {i + 1}")


def update_pad_labels(pads):
    for pad in pads:
        if pad.note in _pad_tags:
            dpg.set_item_label(_pad_tags[pad.note], pad.label)


def overlay_plugin_pad_labels(labels: dict[int, str]):
    for note, label in labels.items():
        if note in _pad_tags:
            dpg.set_item_label(_pad_tags[note], label)


def flash_pad(note: int, velocity: int = 127):
    if note not in _pad_tags:
        return
    tag = _pad_tags[note]
    brightness = 80 + int(velocity / 127 * 175)
    r = min(brightness, 255)
    g = min(int(brightness * 0.8), 255)
    b = 255
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (r, g, b, 255))
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 5)
    dpg.bind_item_theme(tag, t)
    _pad_press_times[note] = time.time()


def release_pad(note: int):
    if note not in _pad_tags:
        return
    tag = _pad_tags[note]
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvButton):
            dpg.add_theme_color(dpg.mvThemeCol_Button, _default_color)
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 5)
    dpg.bind_item_theme(tag, t)
