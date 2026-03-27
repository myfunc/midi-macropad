"""Live MIDI event log."""
from collections import deque

import dearpygui.dearpygui as dpg
import time

MAX_LOG_LINES = 100
_widget_tags: deque[int] = deque()
_pending: deque[tuple[str, tuple]] = deque(maxlen=MAX_LOG_LINES)


def create_midi_log(parent="log_content"):
    dpg.add_child_window(tag="midi_log_child", parent=parent,
                         height=-1, border=False)
    while _pending:
        text, color = _pending.popleft()
        _append_widget(text, color)


def _append_widget(text: str, color: tuple):
    tag = dpg.add_text(text, parent="midi_log_child", color=color)
    _widget_tags.append(tag)
    while len(_widget_tags) > MAX_LOG_LINES:
        old = _widget_tags.popleft()
        if dpg.does_item_exist(old):
            dpg.delete_item(old)
    dpg.set_y_scroll("midi_log_child", dpg.get_y_scroll_max("midi_log_child"))


def add_log_entry(event_type: str, detail: str, color=(170, 172, 182)):
    ts = time.strftime("%H:%M:%S")
    text = f"[{ts}] {event_type}: {detail}"
    if not dpg.does_item_exist("midi_log_child"):
        _pending.append((text, color))
        return
    _append_widget(text, color)
