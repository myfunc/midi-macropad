"""Visual 2x8 pad grid (16 pads) with edit/swap icons, click-to-trigger, and DAW-style knobs."""
import math
import time

import dearpygui.dearpygui as dpg

PAD_W = 110
PAD_H = 108
PAD_SPACING = 8
BANK_GAP = 20

PAD_NOTES_BANK_A = [16, 17, 18, 19, 20, 21, 22, 23]
PAD_NOTES_BANK_B = [24, 25, 26, 27, 28, 29, 30, 31]
PAD_NOTES = PAD_NOTES_BANK_A + PAD_NOTES_BANK_B
# Physical top row = MIDI 20–23 (A) / 28–31 (B); bottom = 16–19 / 24–27.
TOP_ROW_NOTES = [20, 21, 22, 23, 28, 29, 30, 31]
BOT_ROW_NOTES = [16, 17, 18, 19, 24, 25, 26, 27]

TOP_ROW_H = 20
BOT_ROW_H = 20

KNOB_RADIUS = 32
KNOB_ARC_SEGMENTS = 48
KNOB_VALUE_SEGMENTS = 14
KNOB_WIDGET_W = 100
KNOB_DRAWLIST_W = 100
KNOB_DRAWLIST_H = 72
KNOB_TOPBAR_H = TOP_ROW_H
# Match column height when a 2×2 cell has no knob (keeps Bank B grid aligned).
KNOB_BLOCK_H = KNOB_TOPBAR_H + KNOB_DRAWLIST_H + 22
# Pad column top inset when knobs are shown (aligns pad rows with knob 2×2 rows).
KNOB_HEADER_PAD = 28

# Arc from 225° to -45° = 270° sweep (DAW-style).
KNOB_ANGLE_MIN_DEG = 225.0
KNOB_ANGLE_MAX_DEG = -45.0

KNOB_TRACK_COLOR = (55, 55, 72, 255)
KNOB_VALUE_COLOR_A = (100, 140, 255, 255)
KNOB_VALUE_COLOR_B = (160, 120, 255, 255)
KNOB_PCT_TEXT_COLOR = (140, 145, 160, 255)
KNOB_GLOW_FILL = (160, 120, 255, 90)
KNOB_POINTER_FILL = (235, 240, 255, 255)
KNOB_POINTER_OUTLINE = (100, 110, 180, 255)
KNOB_GLOW_RADIUS = 7.0
KNOB_POINTER_RADIUS = 4.0
KNOB_TRACK_THICKNESS = 6.0
KNOB_VALUE_THICKNESS = 7.0

CHILD_ROUNDING = 8

_pad_tags: dict[int, dict] = {}
_pad_press_times: dict[int, float] = {}
_knob_draw_tags: dict[int, dict] = {}

_default_color = (40, 40, 52, 255)
_swap_highlight = (180, 130, 60, 255)
_release_theme = None
_flash_themes: dict[int, int] = {}

_on_pad_click_cb = None
_on_pad_edit_cb = None
_on_pad_swap_cb = None
_on_knob_edit_cb = None

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


def set_knob_edit_callback(cb):
    global _on_knob_edit_cb
    _on_knob_edit_cb = cb


def _note_to_human(note: int) -> int:
    return note - 15


def _lerp_rgb(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int, int]:
    t = max(0.0, min(1.0, t))
    r = int(a[0] + (b[0] - a[0]) * t)
    g = int(a[1] + (b[1] - a[1]) * t)
    bl = int(a[2] + (b[2] - a[2]) * t)
    return (r, g, bl, 255)


def _get_release_theme():
    global _release_theme
    if _release_theme is None:
        with dpg.theme() as t:
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_Button, _default_color)
                dpg.add_theme_color(dpg.mvThemeCol_ChildBg, _default_color)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 5)
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, CHILD_ROUNDING)
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
            dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, CHILD_ROUNDING)
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
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, CHILD_ROUNDING)
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
                dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, CHILD_ROUNDING)
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


def _knob_pointer_xy(cx: float, cy: float, radius: float, angle_deg: float) -> tuple[float, float]:
    rad = math.radians(angle_deg)
    return cx + radius * math.cos(rad), cy - radius * math.sin(rad)


def _value_arc_segments(angle_deg: float) -> int:
    span = abs(KNOB_ANGLE_MIN_DEG - angle_deg)
    return max(3, min(KNOB_ARC_SEGMENTS, int(span / 270.0 * KNOB_ARC_SEGMENTS) + 3))


def _knob_cx_cy() -> tuple[float, float]:
    cx = KNOB_DRAWLIST_W / 2.0
    cy = float(KNOB_RADIUS) + 10.0
    return cx, cy


def create_pad_grid(parent="pad_area", knobs=None):
    """Build 2x8 pads, DAW-style CC knobs (2x2 + bank gap), and mixer slot below."""
    _pad_tags.clear()
    _pad_press_times.clear()
    _knob_draw_tags.clear()

    with dpg.group(parent=parent):
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=8)
            with dpg.group():
                if knobs:
                    dpg.add_spacer(height=KNOB_HEADER_PAD)
                with dpg.group(horizontal=True):
                    _add_pad_row(TOP_ROW_NOTES)
                dpg.add_spacer(height=PAD_SPACING)
                with dpg.group(horizontal=True):
                    _add_pad_row(BOT_ROW_NOTES)

            if knobs:
                dpg.add_spacer(width=BANK_GAP)
                _create_knobs_panel(knobs)

        dpg.add_spacer(height=8)
        dpg.add_child_window(tag="mixer_content", width=-1, height=-1, border=False)


def _partition_knobs_for_banks(knobs) -> tuple[list, list]:
    """Split knob mappings into Bank A vs B when `bank` is set on the mapping; else all → A."""
    a, b = [], []
    for k in knobs:
        bank = getattr(k, "bank", None)
        if bank in ("b", "B", "bank_b", 1):
            b.append(k)
        else:
            a.append(k)
    return a, b


def _create_knob_bank_grid(knobs_four: list) -> None:
    """Lay out up to four knobs in a 2×2. Empty slots keep alignment."""
    slots = list(knobs_four[:4])
    while len(slots) < 4:
        slots.append(None)

    def _cell(k):
        if k is not None:
            _create_knob_widget(k)
        else:
            dpg.add_spacer(width=KNOB_WIDGET_W, height=KNOB_BLOCK_H)

    with dpg.group(horizontal=False):
        with dpg.group(horizontal=True):
            _cell(slots[0])
            dpg.add_spacer(width=PAD_SPACING)
            _cell(slots[1])
        dpg.add_spacer(height=PAD_SPACING)
        with dpg.group(horizontal=True):
            _cell(slots[2])
            dpg.add_spacer(width=PAD_SPACING)
            _cell(slots[3])


def _create_knobs_panel(knobs) -> None:
    bank_a, bank_b = _partition_knobs_for_banks(knobs)
    with dpg.group(horizontal=False):
        dpg.add_text("KNOBS", color=(75, 78, 95))
        dpg.add_spacer(height=6)
        with dpg.group(horizontal=True):
            _create_knob_bank_grid(bank_a)
            if bank_b:
                dpg.add_spacer(width=BANK_GAP)
                _create_knob_bank_grid(bank_b)


def update_knob_display(cc: int, value: int):
    info = _knob_draw_tags.get(cc)
    if not info:
        return
    seg_tags: list = info["seg_tags"]
    glow_tag = info["glow"]
    dot_tag = info["dot"]
    pct_tag = info["pct_text"]
    if not seg_tags or not dpg.does_item_exist(seg_tags[0]):
        return

    cx, cy, radius = info["cx"], info["cy"], info["radius"]
    angle = _knob_value_angle(value)
    pct = round(value / 127.0 * 100) if value > 0 else 0
    pct_str = f"{pct}%"
    if dpg.does_item_exist(pct_tag):
        char_w = 7.0
        text_w = len(pct_str) * char_w
        dpg.configure_item(
            pct_tag,
            text=pct_str,
            pos=[cx - text_w / 2.0, cy - 7],
        )

    col_a = KNOB_VALUE_COLOR_A[:3]
    col_b = KNOB_VALUE_COLOR_B[:3]

    if value <= 0:
        for tag in seg_tags:
            dpg.configure_item(tag, points=[])
        px, py = _knob_pointer_xy(cx, cy, radius, KNOB_ANGLE_MIN_DEG)
    else:
        n = len(seg_tags)
        start_deg = KNOB_ANGLE_MIN_DEG
        span = angle - start_deg
        for i, tag in enumerate(seg_tags):
            t0 = i / n
            t1 = (i + 1) / n
            a0 = start_deg + span * t0
            a1 = start_deg + span * t1
            sub = max(2, _value_arc_segments(a1) // n + 1)
            pts = _arc_point_pairs(cx, cy, radius, a0, a1, sub)
            tm = (t0 + t1) / 2.0
            c = _lerp_rgb(col_a, col_b, tm)
            dpg.configure_item(tag, points=pts, color=c)
        px, py = _knob_pointer_xy(cx, cy, radius, angle)

    dpg.configure_item(glow_tag, center=[float(px), float(py)])
    dpg.configure_item(dot_tag, center=[float(px), float(py)])


def _add_pad_row(row_notes: list[int]):
    for i, note in enumerate(row_notes):
        _create_pad_widget(note, f"Pad {_note_to_human(note)}")
        if i == 3:
            dpg.add_spacer(width=BANK_GAP)
        elif i < len(row_notes) - 1:
            dpg.add_spacer(width=PAD_SPACING)


def _on_knob_edit_click(cc: int):
    if _on_knob_edit_cb:
        _on_knob_edit_cb(cc)


def _create_knob_widget(knob):
    """Rotary control; driven by update_knob_display."""
    cc = knob.cc
    r = float(KNOB_RADIUS)
    cx, cy = _knob_cx_cy()

    bg_pts = _arc_point_pairs(
        cx, cy, r, KNOB_ANGLE_MIN_DEG, KNOB_ANGLE_MAX_DEG, KNOB_ARC_SEGMENTS
    )

    seg_tags: list[str] = []
    for i in range(KNOB_VALUE_SEGMENTS):
        seg_tags.append(f"knob_val_seg_{cc}_{i}")

    glow_tag = f"knob_glow_{cc}"
    dot_tag = f"knob_dot_{cc}"
    pct_tag = f"knob_pct_{cc}"
    dl_tag = f"knob_drawlist_{cc}"

    with dpg.group():
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=max(1, KNOB_WIDGET_W - 26))
            edit_btn = dpg.add_button(
                label="\u270E",
                width=26,
                height=KNOB_TOPBAR_H,
                callback=lambda *_a, c=cc: _on_knob_edit_click(c),
            )
            dpg.bind_item_theme(edit_btn, _get_icon_btn_theme())

        with dpg.drawlist(width=KNOB_DRAWLIST_W, height=KNOB_DRAWLIST_H, tag=dl_tag):
            dpg.draw_polyline(
                bg_pts,
                color=KNOB_TRACK_COLOR,
                thickness=KNOB_TRACK_THICKNESS,
                tag=f"knob_bg_arc_{cc}",
            )
            for i, st in enumerate(seg_tags):
                dpg.draw_polyline([], color=KNOB_VALUE_COLOR_A, thickness=KNOB_VALUE_THICKNESS, tag=st)
            dpg.draw_text(
                (cx - 10, cy - 7),
                "0%",
                color=KNOB_PCT_TEXT_COLOR,
                size=13,
                tag=pct_tag,
            )
            dpg.draw_circle(
                (0.0, 0.0),
                KNOB_GLOW_RADIUS,
                color=(0, 0, 0, 0),
                fill=KNOB_GLOW_FILL,
                tag=glow_tag,
            )
            dpg.draw_circle(
                (0.0, 0.0),
                KNOB_POINTER_RADIUS,
                color=KNOB_POINTER_OUTLINE,
                fill=KNOB_POINTER_FILL,
                thickness=1,
                tag=dot_tag,
            )

        dpg.add_text(knob.label, color=(120, 120, 140), wrap=KNOB_WIDGET_W)

    _knob_draw_tags[cc] = {
        "cx": cx,
        "cy": cy,
        "radius": r,
        "seg_tags": seg_tags,
        "glow": glow_tag,
        "dot": dot_tag,
        "pct_text": pct_tag,
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
                    label="\u270E", width=26, height=TOP_ROW_H, callback=make_edit_cb()
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
                    width=26,
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


def get_release_theme():
    """Theme for pad idle / released state (ChildRounding matches grid)."""
    return _get_release_theme()


def get_flash_theme(velocity: int):
    """Velocity-tinted flash theme for MIDI/visual feedback."""
    return _get_flash_theme(velocity)


def get_swap_theme():
    """Theme when a pad is selected as swap source."""
    return _get_swap_theme()


def get_swap_target_theme():
    """Theme for valid swap targets while in swap mode."""
    return _get_swap_target_theme()


def get_icon_btn_theme():
    """Transparent icon button theme (edit / swap)."""
    return _get_icon_btn_theme()


def get_body_btn_theme():
    """Pad body click area theme."""
    return _get_body_btn_theme()
