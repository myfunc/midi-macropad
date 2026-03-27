"""Action executor — translates ActionDef into system actions."""
import subprocess
from pynput.keyboard import Key, Controller as KbController, KeyCode
from pynput.mouse import Controller as MouseController

_keyboard = KbController()
_mouse = MouseController()

# Map special key names to pynput Key objects
_SPECIAL_KEYS = {
    "ctrl": Key.ctrl_l,
    "alt": Key.alt_l,
    "shift": Key.shift_l,
    "win": Key.cmd,
    "cmd": Key.cmd,
    "enter": Key.enter,
    "return": Key.enter,
    "tab": Key.tab,
    "space": Key.space,
    "escape": Key.esc,
    "esc": Key.esc,
    "backspace": Key.backspace,
    "delete": Key.delete,
    "up": Key.up,
    "down": Key.down,
    "left": Key.left,
    "right": Key.right,
    "home": Key.home,
    "end": Key.end,
    "pageup": Key.page_up,
    "pagedown": Key.page_down,
    "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4,
    "f5": Key.f5, "f6": Key.f6, "f7": Key.f7, "f8": Key.f8,
    "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12,
    "playpause": Key.media_play_pause,
    "nexttrack": Key.media_next,
    "prevtrack": Key.media_previous,
    "volumeup": Key.media_volume_up,
    "volumedown": Key.media_volume_down,
    "volumemute": Key.media_volume_mute,
    "`": KeyCode.from_char("`"),
    "/": KeyCode.from_char("/"),
}


def _single_token_to_key(part: str):
    """Map a single token to a layout-independent key when possible."""
    if len(part) != 1:
        return KeyCode.from_char(part)

    char = part.lower()
    if "a" <= char <= "z":
        return KeyCode.from_vk(ord(char.upper()))
    if "0" <= char <= "9":
        return KeyCode.from_vk(ord(char))
    return KeyCode.from_char(part)


def _parse_keys(keys_str: str):
    """Parse 'ctrl+shift+p' into a list of pynput key objects."""
    parts = [p.strip().lower() for p in keys_str.split("+")]
    result = []
    for part in parts:
        if part in _SPECIAL_KEYS:
            result.append(_SPECIAL_KEYS[part])
        elif len(part) == 1:
            result.append(_single_token_to_key(part))
        else:
            result.append(KeyCode.from_char(part))
    return result


def execute_keystroke(keys_str: str):
    """Execute a keystroke combo like 'ctrl+c'."""
    pressed = []
    try:
        keys = _parse_keys(keys_str)
        if not keys:
            return None
        modifiers = keys[:-1]
        final_key = keys[-1]
        for key in modifiers:
            _keyboard.press(key)
            pressed.append(key)
        _keyboard.press(final_key)
        _keyboard.release(final_key)
        return None
    except Exception as exc:
        return f"Keystroke '{keys_str}' failed: {exc}"
    finally:
        for key in reversed(pressed):
            try:
                _keyboard.release(key)
            except Exception:
                pass


def execute_shell(command: str):
    """Run a shell command."""
    try:
        subprocess.Popen(command, shell=True)
        return None
    except Exception as exc:
        return f"Shell command failed: {exc}"


def execute_launch(app_path: str):
    """Launch an application."""
    try:
        subprocess.Popen(app_path, shell=True)
        return None
    except Exception as exc:
        return f"Launch failed: {exc}"


_last_scroll_value = 64

def execute_scroll(midi_value: int):
    """Scroll based on MIDI knob position. Center (64) = no scroll."""
    global _last_scroll_value
    delta = midi_value - _last_scroll_value
    _last_scroll_value = midi_value
    if delta == 0:
        return None
    try:
        _mouse.scroll(0, -delta)
        return None
    except Exception as exc:
        return f"Scroll failed: {exc}"
