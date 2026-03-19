"""Mode switcher tabs with manual-override support.

When the user clicks a tab in the UI the mode sticks until the next MIDI
mode-switch event (knob 4).  Context-based auto-switching is suppressed
while the override is active.
"""
import dearpygui.dearpygui as dpg

_mode_callback = None
_tab_tags: list[str] = []
_manual_override: bool = False


def create_mode_tabs(mode_names: list[str], mode_colors: list[str], callback=None):
    """Create mode tabs inside the mode_tabs tab bar. callback(index) called on switch."""
    global _mode_callback
    _mode_callback = callback
    _tab_tags.clear()

    dpg.delete_item("mode_tabs", children_only=True)

    for i, (name, color) in enumerate(zip(mode_names, mode_colors)):
        tag = f"mode_tab_{i}"
        with dpg.tab(label=f"  {name}  ", parent="mode_tabs", tag=tag):
            pass
        _tab_tags.append(tag)

    dpg.set_item_callback("mode_tabs", _on_tab_changed)


def _on_tab_changed(sender, app_data):
    """DPG tab-bar callback.

    app_data may be a string tag *or* an integer UUID depending on the
    DearPyGui version, so we try both comparison styles.
    """
    global _manual_override
    if not _mode_callback or not _tab_tags:
        return

    index = _resolve_tab_index(app_data)
    if index is not None:
        _manual_override = True
        _mode_callback(index)


def _resolve_tab_index(app_data) -> int | None:
    """Map app_data (str tag or int UUID) to a tab index."""
    for i, tag in enumerate(_tab_tags):
        if app_data == tag:
            return i
    # app_data might be an integer UUID — compare via alias lookup
    for i, tag in enumerate(_tab_tags):
        try:
            if dpg.get_alias_id(tag) == app_data:
                return i
        except (SystemError, Exception):
            pass
    return None


def set_active_tab(index: int):
    """Programmatically set the active tab (from MIDI / context switch)."""
    if 0 <= index < len(_tab_tags):
        dpg.set_value("mode_tabs", _tab_tags[index])


def is_manual_override() -> bool:
    return _manual_override


def clear_manual_override():
    global _manual_override
    _manual_override = False
