"""Visual 2x8 pad grid (16 pads) with edit/swap icons, click-to-trigger, and DAW-style knobs."""
import math
import time

import dearpygui.dearpygui as dpg

PAD_W = 88
PAD_H = 86
PAD_SPACING = 6
BANK_GAP = 16

PAD_NOTES_BANK_A = [16, 17, 18, 19, 20, 21, 22, 23]
PAD_NOTES_BANK_B = [24, 25, 26, 27, 28, 29, 30, 31]
PAD_NOTES = PAD_NOTES_BANK_A + PAD_NOTES_BANK_B
# UI rows: top = pads 1-4 + 9-12, bottom = 5-8 + 13-16 (see project spec).
TOP_ROW_NOTES = [16, 17, 18, 19, 24, 25, 26, 27]
BOT_ROW_NOTES = [20, 21, 22, 23, 28, 29, 30, 31]

TOP_ROW_H = 18
BOT_ROW_H = 18

KNOB_RADIUS = 28
KNOB_ARC_SEGMENTS = 40
# Arc from 225° (CCW from +x, math convention) to -45° = 270° sweep (DAW-style).
KNOB_ANGLE_MIN_DEG = 225.0
KNOB_ANGLE_MAX_DEG = -45.0

_pad_tags: dict[int, dict] = {}
_pad_press_times: dict[int, float] = {}
_knob_draw_tags: dict[int, dict] = {}

_default_color = (40, 40, 52, 255)
_press_color = (100, 180, 255, 255)
_swap_highlight = (180, 130, 60, 255)
_release_theme = None
_flash_themes: dict[int, int] = {}

_knob_track_color = (50, 50, 65, 255)
_knob_value_color = (130, 150, 255, 255)
_knob_glow_fill = (160, 180, 255, 100)
_knob_pointer_fill = (235, 240, 255, 255)
_knob_pointer_outline = (90, 110, 200, 255)

_on_pad_click_cb = None
_on_pad_edit_cb = None
_on_pad_swap_cb = None

_swap_mode_note: int | None = None


def set_pad_click_callback(cb):
    global _on_pad_click_cb
    _on_pad_click_cb = cb


def set_pad_edit_callback(cb):
    global _on_pad_edit_cb
    _on_pad_edit_cb = cb


def set_pad_swap_callback(cb):
    global _on_pad_swap_cb
    _on_pad_swap_cb = cb


def _note_to_human(note: int) -> int:
    return note - 15


def _get_release_theme():
    global _release_theme
    if _release_theme is None:
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_Button, _default_color)
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, _default_color)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 5)
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 5)
        _release_theme = t
    return _release_theme


def _get_flash_theme(velocity: int):
    if velocity in _flash_themes:
        return _flash_themes[velocity]
    brightness = 80 + int(velocity / 127 * 175)
    r = min(brightness, 255)
    g = min(int(brightness * 0.8), 255)
    b = 255
    with dpg.theme() as t:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_Button, (r, g, b, 255))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (r, g, b, 255))
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 5)
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 5)
    _flash_themes[velocity] = t
    return t


_swap_theme = None


def _get_swap_theme():
    global _swap_theme
    if _swap_theme is None:
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_Border, _swap_highlight)
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (55, 48, 30, 255))
                dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 2)
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 5)
        _swap_theme = t
    return _swap_theme


_swap_target_theme = None


def _get_swap_target_theme():
    global _swap_target_theme
    if _swap_target_theme is None:
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_Border, (180, 130, 60, 120))
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (45, 42, 35, 255))
                dpg.add_theme_style(dpg.mvStyleVar_ChildBorderSize, 2)
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 5)
        _swap_target_theme = t
    return _swap_target_theme


_icon_btn_theme = None


def _get_icon_btn_theme():
    global _icon_btn_theme
    if _icon_btn_theme is None:
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 0, 0, 0))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (255, 255, 255, 30))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (255, 255, 255, 50))
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)
                dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 2, 2)
        _icon_btn_theme = t
    return _icon_btn_theme


_body_btn_theme = None


def _get_body_btn_theme():
    global _body_btn_theme
    if _body_btn_theme is None:
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (0, 0, 0, 0))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (255, 255, 255, 15))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (255, 255, 255, 30))
        _body_btn_theme = t
    return _body_btn_theme


def _arc_point_pairs(cx: float, cy: float, radius: float,
                     start_deg: float, end_deg: float, segments: int) -> list[list[float]]:
    segs = max(2, int(segments))
    pts: list[list[float]] = []
    for i in range(segs + 1):
        t = i / segs
        deg = start_deg + (end_deg - start_deg) * t
        rad = math.radians(deg)
        x = cx + radius * math.cos(rad)
        y = cy - radius * math.sin(rad)
        pts.append([float(x), float(y)])
    return pts


def _knob_value_angle(value: int) -> float:
    v = max(0, min(127, int(value)))
    return KNOB_ANGLE_MIN_DEG - (v / 127.0) * (
        KNOB_ANGLE_MIN_DEG - KNOB_ANGLE_MAX_DEG
    )


def _knob_pointer_xy(cx: float, cy: float, radius: float, angle_deg: float,
                     inset: float = 6.0) -> tuple[float, float]:
    r = max(4.0, radius - inset)
    rad = math.radians(angle_deg)
    return cx + r * math.cos(rad), cy - r * math.sin(rad)


def _value_arc_segments(angle_deg: float) -> int:
    span = abs(KNOB_ANGLE_MIN_DEG - angle_deg)
    return max(3, min(KNOB_ARC_SEGMENTS, int(span / 270.0 * KNOB_ARC_SEGMENTS) + 3))


def create_pad_grid(parent="pad_area", knobs=None):
    """Build 2x8 pads, DAW-style CC knobs, and mixer slot below."""
    _pad_tags.clear()
    _pad_press_times.clear()
    _knob_draw_tags.clear()

    with dpg.group(parent=parent):
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=8)
            with dpg.group():
                with dpg.group(horizontal=True):
                    _add_pad_row(TOP_ROW_NOTES)
                dpg.add_spacer(height=PAD_SPACING)
                with dpg.group(horizontal=True):
                    _add_pad_row(BOT_ROW_NOTES)

            dpg.add_spacer(width=16)
            if knobs:
                with dpg.group():
                    dpg.add_text("KNOBS", color=(75, 78, 95))
                    dpg.add_spacer(height=6)
                    for i, knob in enumerate(knobs):
                        _create_knob_widget(knob)
                        if i < len(knobs) - 1:
                            dpg.add_spacer(height=8)

        dpg.add_spacer(height=8)
        dpg.add_child_window(tag="mixer_content", width=-1, height=-1, border=False)


def update_knob_display(cc: int, value: int):
    info = _knob_draw_tags.get(cc)
    if not info:
        return
    val_tag = info["value_arc"]
    dot_tag = info["dot"]
    glow_tag = info["glow"]
    if not dpg.does_item_exist(val_tag):
        return

    cx, cy, radius = info["cx"], info["cy"], info["radius"]
    angle = _knob_value_angle(value)

    if value <= 0:
        dpg.configure_item(val_tag, points=[])
    else:
        segs = _value_arc_segments(angle)
        pts = _arc_point_pairs(cx, cy, radius, KNOB_ANGLE_MIN_DEG, angle, segs)
        dpg.configure_item(val_tag, points=pts)

    px, py = _knob_pointer_xy(cx, cy, radius, angle)
    dpg.configure_item(dot_tag, center=[float(px), float(py)])
    dpg.configure_item(glow_tag, center=[float(px), float(py)])


def _add_pad_row(row_notes: list[int]):
    for i, note in enumerate(row_notes):
        _create_pad_widget(note, f"Pad {_note_to_human(note)}")
        if i == 3:
            dpg.add_spacer(width=BANK_GAP)
        elif i < len(row_notes) - 1:
            dpg.add_spacer(width=PAD_SPACING)


def _create_knob_widget(knob):
    """Rotary level indicator driven by update_knob_display (no MIDI interaction here)."""
    cc = knob.cc
    r = float(KNOB_RADIUS)
    dl_w = int(r * 2.0 + 20.0)
    dl_h = int(r * 2.0 + 14.0)
    cx = dl_w / 2.0
    cy = r + 8.0

    bg_pts = _arc_point_pairs(
        cx, cy, r, KNOB_ANGLE_MIN_DEG, KNOB_ANGLE_MAX_DEG, KNOB_ARC_SEGMENTS
    )

    val_tag = f"knob_val_arc_{cc}"
    dot_tag = f"knob_dot_{cc}"
    glow_tag = f"knob_glow_{cc}"

    with dpg.group():
        with dpg.drawlist(width=dl_w, height=dl_h, tag=f"knob_drawlist_{cc}"):
            dpg.draw_polyline(
                bg_pts,
                color=_knob_track_color,
                thickness=5,
                tag=f"knob_bg_arc_{cc}",
            )
            dpg.draw_polyline(
                [],
                color=_knob_value_color,
                thickness=6,
                tag=val_tag,
            )
            dpg.draw_circle(
                (cx, cy),
                10.0,
                color=(0, 0, 0, 0),
                fill=_knob_glow_fill,
                tag=glow_tag,
            )
            dpg.draw_circle(
                (cx, cy),
                4.5,
                color=_knob_pointer_outline,
                fill=_knob_pointer_fill,
                thickness=1,
                tag=dot_tag,
            )
        dpg.add_text(knob.label, color=(120, 120, 140))

    _knob_draw_tags[cc] = {
        "cx": cx,
        "cy": cy,
        "radius": r,
        "value_arc": val_tag,
        "dot": dot_tag,
        "glow": glow_tag,
    }
    update_knob_display(cc, 0)


def _create_pad_widget(note: int, default_label: str):
    """Composite pad with ✎ / #N / ⇄ and clickable body."""
    human_num = _note_to_human(note)
    container_tag = f"pad_container_{note}"
    body_tag = f"pad_body_{note}"
    num_tag = f"pad_num_{note}"

    def make_edit_cb(n=note):
        return lambda: _on_edit_click(n)

    def make_body_cb(n=note):
        return lambda: _on_pad_body_click(n)

    def make_swap_cb(n=note):
        return lambda: _on_swap_click(n)

    with dpg.child_window(tag=container_tag, width=PAD_W, height=PAD_H,
                          border=True, no_scrollbar=True):
        with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                       pad_outerX=False, no_pad_innerX=True):
            dpg.add_table_column(init_width_or_weight=0.3)
            dpg.add_table_column(init_width_or_weight=0.4)
            dpg.add_table_column(init_width_or_weight=0.3)
            with dpg.table_row():
                edit_btn = dpg.add_button(
                    label="\u270E", width=22, height=TOP_ROW_H, callback=make_edit_cb()
                )
                dpg.bind_item_theme(edit_btn, _get_icon_btn_theme())
                dpg.add_spacer()
                dpg.add_text(f"#{human_num}", tag=num_tag, color=(85, 88, 100))

        body_h = PAD_H - TOP_ROW_H - BOT_ROW_H - 22
        btn = dpg.add_button(
            label=default_label,
            tag=body_tag,
            width=-1,
            height=max(body_h, 26),
            callback=make_body_cb(),
        )
        dpg.bind_item_theme(btn, _get_body_btn_theme())

        with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                       pad_outerX=False, no_pad_innerX=True):
            dpg.add_table_column(init_width_or_weight=0.3)
            dpg.add_table_column(init_width_or_weight=0.7)
            with dpg.table_row():
                swap_btn = dpg.add_button(
                    label="\u21C4",
                    tag=f"pad_swap_{note}",
                    width=22,
                    height=BOT_ROW_H,
                    callback=make_swap_cb(),
                )
                dpg.bind_item_theme(swap_btn, _get_icon_btn_theme())
                dpg.add_spacer()

    dpg.bind_item_theme(container_tag, _get_release_theme())
    _pad_tags[note] = {
        "container": container_tag,
        "body": body_tag,
    }


def _on_edit_click(note: int):
    if _on_pad_edit_cb:
        _on_pad_edit_cb(note)


def _on_pad_body_click(note: int):
    global _swap_mode_note
    if _swap_mode_note is not None:
        source = _swap_mode_note
        _exit_swap_mode()
        if source != note and _on_pad_swap_cb:
            _on_pad_swap_cb(source, note)
        return
    if _on_pad_click_cb:
        _on_pad_click_cb(note)


def _on_swap_click(note: int):
    global _swap_mode_note
    if _swap_mode_note == note:
        _exit_swap_mode()
        return
    if _swap_mode_note is not None:
        source = _swap_mode_note
        _exit_swap_mode()
        if source != note and _on_pad_swap_cb:
            _on_pad_swap_cb(source, note)
        return
    _enter_swap_mode(note)


def _enter_swap_mode(note: int):
    global _swap_mode_note
    _swap_mode_note = note
    tags = _pad_tags.get(note)
    if tags:
        dpg.bind_item_theme(tags["container"], _get_swap_theme())
    for other_note, other_tags in _pad_tags.items():
        if other_note != note:
            dpg.bind_item_theme(other_tags["container"], _get_swap_target_theme())


def _exit_swap_mode():
    global _swap_mode_note
    _swap_mode_note = None
    for _, tags in _pad_tags.items():
        dpg.bind_item_theme(tags["container"], _get_release_theme())


def clear_pad_labels():
    for i, note in enumerate(PAD_NOTES):
        tags = _pad_tags.get(note)
        if tags and dpg.does_item_exist(tags["body"]):
            dpg.set_item_label(tags["body"], f"Pad {i + 1}")


def update_pad_labels(pads):
    for pad in pads:
        tags = _pad_tags.get(pad.note)
        if tags and dpg.does_item_exist(tags["body"]):
            dpg.set_item_label(tags["body"], pad.label)


def overlay_plugin_pad_labels(labels: dict[int, str]):
    for note, label in labels.items():
        tags = _pad_tags.get(note)
        if tags and dpg.does_item_exist(tags["body"]):
            dpg.set_item_label(tags["body"], label)
    for note in PAD_NOTES:
        swap_tag = f"pad_swap_{note}"
        if dpg.does_item_exist(swap_tag):
            is_plugin = note in labels
            dpg.configure_item(swap_tag, show=not is_plugin)


def flash_pad(note: int, velocity: int = 127):
    tags = _pad_tags.get(note)
    if not tags:
        return
    t = _get_flash_theme(velocity)
    dpg.bind_item_theme(tags["container"], t)
    _pad_press_times[note] = time.time()


def release_pad(note: int):
    if _swap_mode_note is not None:
        return
    tags = _pad_tags.get(note)
    if not tags:
        return
    dpg.bind_item_theme(tags["container"], _get_release_theme())
