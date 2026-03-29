"""Visual 2x4 pad grid with edit/swap icons, click-to-trigger, and knob level indicators."""
import time
import dearpygui.dearpygui as dpg

PAD_W = 108
PAD_H = 100
PAD_SPACING = 6
KNOB_BAR_W = 22
KNOB_BAR_H = 76
PAD_NOTES = [16, 17, 18, 19, 20, 21, 22, 23]
TOP_ROW_H = 18
BOT_ROW_H = 18

_pad_tags: dict[int, dict] = {}
_pad_press_times: dict[int, float] = {}
_knob_bar_tags: dict[int, str] = {}
_default_color = (40, 40, 52, 255)
_press_color = (100, 180, 255, 255)
_swap_highlight = (180, 130, 60, 255)
_release_theme = None
_flash_themes: dict[int, int] = {}

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


def create_pad_grid(parent="pad_area", knobs=None):
    """Build the 2x4 pad grid plus knob level indicators and mixer slot."""
    _pad_tags.clear()
    _pad_press_times.clear()
    _knob_bar_tags.clear()

    with dpg.group(parent=parent):
        dpg.add_spacer(height=4)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=8)
            with dpg.group():
                with dpg.group(horizontal=True):
                    for i in range(4, 8):
                        _create_pad_widget(PAD_NOTES[i], f"Pad {i + 1}")
                        if i < 7:
                            dpg.add_spacer(width=PAD_SPACING)
                dpg.add_spacer(height=PAD_SPACING)
                with dpg.group(horizontal=True):
                    for i in range(4):
                        _create_pad_widget(PAD_NOTES[i], f"Pad {i + 1}")
                        if i < 3:
                            dpg.add_spacer(width=PAD_SPACING)

            dpg.add_spacer(width=16)
            if knobs:
                with dpg.group():
                    dpg.add_text("KNOBS", color=(75, 78, 95))
                    dpg.add_spacer(height=4)
                    with dpg.group(horizontal=True):
                        for knob in knobs:
                            with dpg.group():
                                dpg.add_text(knob.label, color=(120, 120, 140))
                                bar_tag = f"knob_bar_{knob.cc}"
                                dpg.add_slider_int(
                                    tag=bar_tag,
                                    default_value=0,
                                    min_value=0,
                                    max_value=127,
                                    width=KNOB_BAR_W,
                                    height=KNOB_BAR_H,
                                    vertical=True,
                                    no_input=True,
                                    format="",
                                )
                                _knob_bar_tags[knob.cc] = bar_tag
                            dpg.add_spacer(width=8)

            dpg.add_spacer(width=16)
            dpg.add_child_window(tag="mixer_content", width=300, height=-1,
                                 border=False)


def update_knob_display(cc: int, value: int):
    tag = _knob_bar_tags.get(cc)
    if tag and dpg.does_item_exist(tag):
        dpg.set_value(tag, value)


def _create_pad_widget(note: int, default_label: str):
    """Build a composite pad with drawlist overlay for edit icon, number, swap icon."""
    human_num = _note_to_human(note)
    container_tag = f"pad_container_{note}"
    body_tag = f"pad_body_{note}"
    num_tag = f"pad_num_{note}"

    # Use note in default args to avoid closure issues
    def make_edit_cb(n=note):
        return lambda: _on_edit_click(n)

    def make_body_cb(n=note):
        return lambda: _on_pad_body_click(n)

    def make_swap_cb(n=note):
        return lambda: _on_swap_click(n)

    inner_w = PAD_W - 10

    with dpg.child_window(tag=container_tag, width=PAD_W, height=PAD_H,
                          border=True, no_scrollbar=True):
        # Top bar: [edit]  ...spacer...  [#N]
        with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                       pad_outerX=False, no_pad_innerX=True):
            dpg.add_table_column(init_width_or_weight=0.3)
            dpg.add_table_column(init_width_or_weight=0.4)
            dpg.add_table_column(init_width_or_weight=0.3)
            with dpg.table_row():
                edit_btn = dpg.add_button(label="\u270E", width=24, height=TOP_ROW_H,
                                          callback=make_edit_cb())
                dpg.bind_item_theme(edit_btn, _get_icon_btn_theme())

                dpg.add_spacer()

                dpg.add_text(f"#{human_num}", tag=num_tag, color=(85, 88, 100))

        # Center: clickable body
        body_h = PAD_H - TOP_ROW_H - BOT_ROW_H - 24
        btn = dpg.add_button(label=default_label, tag=body_tag,
                             width=-1, height=max(body_h, 28),
                             callback=make_body_cb())
        dpg.bind_item_theme(btn, _get_body_btn_theme())

        # Bottom bar: [swap]
        with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                       pad_outerX=False, no_pad_innerX=True):
            dpg.add_table_column(init_width_or_weight=0.3)
            dpg.add_table_column(init_width_or_weight=0.7)
            with dpg.table_row():
                swap_btn = dpg.add_button(label="\u21C4", tag=f"pad_swap_{note}",
                                          width=24, height=BOT_ROW_H,
                                          callback=make_swap_cb())
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
