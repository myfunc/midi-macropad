"""Right sidebar -- tabbed panel (Properties + plugin tabs)."""
import dearpygui.dearpygui as dpg
from ui import selection

_rebuild_fn = None


def create_right_sidebar(parent="panel_right"):
    with dpg.child_window(parent=parent, border=False):
        dpg.add_spacer(height=6)
        dpg.add_tab_bar(tag="right_tabs")
        with dpg.tab(label=" Properties ", parent="right_tabs", tag="right_tab_props"):
            dpg.add_spacer(height=6)
            dpg.add_text("Select a pad or plugin to edit",
                         tag="sr_placeholder", color=(85, 88, 100), wrap=250,
                         indent=8)
            dpg.add_group(tag="sr_content")

    selection.set_callback(_on_selection_changed)


def add_right_tab(tab_id: str, label: str) -> str:
    """Add a tab to the right sidebar. Returns content tag."""
    tag = f"right_tab_{tab_id}"
    content = f"right_tab_content_{tab_id}"
    with dpg.tab(label=f" {label} ", parent="right_tabs", tag=tag):
        dpg.add_child_window(tag=content, height=-1, border=False)
    return content


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
