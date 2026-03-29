"""Right sidebar -- pad Properties panel."""
import dearpygui.dearpygui as dpg
from ui import selection

_rebuild_fn = None


def create_right_sidebar(parent="panel_right"):
    with dpg.child_window(parent=parent, border=False):
        dpg.add_spacer(height=6)
        with dpg.group(horizontal=True):
            dpg.add_spacer(width=8)
            dpg.add_text("Properties", color=(85, 88, 100))
        dpg.add_spacer(height=4)
        dpg.add_separator()
        dpg.add_spacer(height=6)
        dpg.add_text("Select a pad to edit",
                     tag="sr_placeholder", color=(85, 88, 100), wrap=250,
                     indent=8)
        dpg.add_group(tag="sr_content")

    selection.set_callback(_on_selection_changed)


def set_plugin_list(plugin_names: list[str]):
    pass


def set_rebuild_fn(fn):
    global _rebuild_fn
    _rebuild_fn = fn


def _on_selection_changed(sel_type, sel_id):
    if _rebuild_fn:
        _rebuild_fn(sel_type, sel_id)


def rebuild(content_builder=None):
    if not dpg.does_item_exist("sr_content"):
        return
    dpg.delete_item("sr_content", children_only=True)
    if content_builder:
        if dpg.does_item_exist("sr_placeholder"):
            dpg.configure_item("sr_placeholder", show=False)
        content_builder("sr_content")
    else:
        if dpg.does_item_exist("sr_placeholder"):
            dpg.configure_item("sr_placeholder", show=True)
