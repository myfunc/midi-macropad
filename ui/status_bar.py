"""Bottom status bar — shows tooltip for hovered control."""
import dearpygui.dearpygui as dpg

_tooltips: dict[str | int, str] = {}
_last_text: str = ""

_DEFAULT_HINT = "Hover over a control to see what it does"
_DEFAULT_COLOR = (80, 80, 100)
_ACTIVE_COLOR = (185, 190, 210)

_SPECIAL_KEYS = {
    "ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "win": "Win",
    "playpause": "Play/Pause", "prevtrack": "Prev Track",
    "nexttrack": "Next Track", "volumemute": "Vol Mute",
    "backspace": "Backspace", "delete": "Delete", "tab": "Tab",
    "enter": "Enter", "escape": "Esc", "space": "Space",
}


def _format_keys(keys: str) -> str:
    """ctrl+shift+p → Ctrl + Shift + P"""
    parts = []
    for p in keys.split("+"):
        k = p.strip()
        parts.append(_SPECIAL_KEYS.get(k.lower(), k.upper() if len(k) == 1 else k))
    return " + ".join(parts)


def describe_pad(label: str, action) -> str:
    """Generate a human-readable description for a pad's action."""
    if action.type == "keystroke":
        return f"{label} -- sends {_format_keys(action.keys)}"
    elif action.type == "obs":
        t = action.target
        if t == "toggle_recording":
            d = "toggles OBS recording"
        elif t == "toggle_streaming":
            d = "toggles OBS streaming"
        elif t == "next_scene":
            d = "next OBS scene"
        elif t == "prev_scene":
            d = "previous OBS scene"
        elif t.startswith("scene:"):
            d = f"OBS scene -> {t[6:]}"
        elif t.startswith("mute:"):
            d = f"toggles mute: {t[5:]} (OBS)"
        else:
            d = f"OBS: {t}"
        return f"{label} -- {d}"
    elif action.type == "shell":
        return f"{label} -- runs: {action.command}"
    elif action.type == "launch":
        return f"{label} -- opens: {action.command}"
    elif action.type == "volume":
        target = "master volume" if action.target == "master" else "mic volume"
        return f"{label} -- controls {target}"
    elif action.type == "scroll":
        return f"{label} -- mouse scroll"
    return label


def register(item_tag: str | int, text: str):
    """Register a tooltip for an item (replaces previous if any)."""
    _tooltips[item_tag] = text


def unregister(item_tag: str | int):
    _tooltips.pop(item_tag, None)


def poll_hover():
    """Check all registered items for hover and update status bar text.

    Call once per frame from the render loop.
    """
    global _last_text
    for tag, text in _tooltips.items():
        try:
            if dpg.is_item_hovered(tag):
                if text != _last_text:
                    dpg.set_value("status_bar_text", text)
                    dpg.configure_item("status_bar_text", color=_ACTIVE_COLOR)
                    _last_text = text
                return
        except Exception:
            continue
    if _last_text:
        dpg.set_value("status_bar_text", _DEFAULT_HINT)
        dpg.configure_item("status_bar_text", color=_DEFAULT_COLOR)
        _last_text = ""
