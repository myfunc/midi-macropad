"""Global selection state — tracks what element is currently selected for the properties panel."""

_selected_type: str | None = None  # "pad" | "mode" | "plugin" | None
_selected_id = None  # note number, mode index, plugin name
_on_change_cb = None


def set_callback(cb):
    global _on_change_cb
    _on_change_cb = cb


def select(sel_type: str | None, sel_id=None):
    global _selected_type, _selected_id
    if _selected_type == sel_type and _selected_id == sel_id:
        return
    _selected_type = sel_type
    _selected_id = sel_id
    if _on_change_cb:
        _on_change_cb(_selected_type, _selected_id)


def clear():
    select(None, None)


def get():
    return _selected_type, _selected_id
