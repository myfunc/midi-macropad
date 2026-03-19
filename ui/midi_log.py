"""Live MIDI event log."""
import dearpygui.dearpygui as dpg
import time

MAX_LOG_LINES = 100
_log_lines: list[tuple[str, tuple]] = []


def create_midi_log(parent="log_content"):
    dpg.add_child_window(tag="midi_log_child", parent=parent,
                         height=-1, border=False)


def add_log_entry(event_type: str, detail: str, color=(170, 172, 182)):
    ts = time.strftime("%H:%M:%S")
    text = f"[{ts}] {event_type}: {detail}"
    _log_lines.append((text, color))
    if len(_log_lines) > MAX_LOG_LINES:
        _log_lines.pop(0)
    _rebuild_log()


def _rebuild_log():
    if not dpg.does_item_exist("midi_log_child"):
        return
    dpg.delete_item("midi_log_child", children_only=True)
    for text, color in _log_lines:
        dpg.add_text(text, parent="midi_log_child", color=color)
    dpg.set_y_scroll("midi_log_child",
                     dpg.get_y_scroll_max("midi_log_child"))
