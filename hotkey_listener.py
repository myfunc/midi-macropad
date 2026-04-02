"""Global keyboard/mouse hotkey listener for triggering pad actions."""
import queue
import threading
import logging
from pynput import keyboard, mouse
from midi_listener import MidiEvent

_log = logging.getLogger(__name__)

# Mouse button name mapping
_MOUSE_BUTTONS = {
    "mouse4": mouse.Button.x1,       # Back button
    "mouse5": mouse.Button.x2,       # Forward button
    "mouse_back": mouse.Button.x1,
    "mouse_forward": mouse.Button.x2,
}

# Extra keyboard key names (beyond what pynput Key enum provides)
_KEY_MAP = {
    "f1": keyboard.Key.f1, "f2": keyboard.Key.f2, "f3": keyboard.Key.f3,
    "f4": keyboard.Key.f4, "f5": keyboard.Key.f5, "f6": keyboard.Key.f6,
    "f7": keyboard.Key.f7, "f8": keyboard.Key.f8, "f9": keyboard.Key.f9,
    "f10": keyboard.Key.f10, "f11": keyboard.Key.f11, "f12": keyboard.Key.f12,
    "f13": keyboard.Key.f13, "f14": keyboard.Key.f14, "f15": keyboard.Key.f15,
    "f16": keyboard.Key.f16, "f17": keyboard.Key.f17, "f18": keyboard.Key.f18,
    "f19": keyboard.Key.f19, "f20": keyboard.Key.f20,
    "ctrl": keyboard.Key.ctrl_l, "alt": keyboard.Key.alt_l,
    "shift": keyboard.Key.shift_l, "win": keyboard.Key.cmd,
    "esc": keyboard.Key.esc, "escape": keyboard.Key.esc,
    "space": keyboard.Key.space, "enter": keyboard.Key.enter,
    "tab": keyboard.Key.tab, "backspace": keyboard.Key.backspace,
    "delete": keyboard.Key.delete,
    "up": keyboard.Key.up, "down": keyboard.Key.down,
    "left": keyboard.Key.left, "right": keyboard.Key.right,
}


def _parse_hotkey(hotkey_str: str):
    """Parse a hotkey string into its components.

    Returns (kind, spec) where:
      kind="mouse", spec=mouse.Button
      kind="key", spec=frozenset of modifier keys + final key
    """
    s = hotkey_str.strip().lower()
    if not s:
        return None, None

    # Mouse buttons
    if s in _MOUSE_BUTTONS:
        return "mouse", _MOUSE_BUTTONS[s]

    # Keyboard combo (e.g., "ctrl+shift+r" or just "f13")
    parts = [p.strip() for p in s.split("+")]
    keys = set()
    for part in parts:
        if part in _KEY_MAP:
            keys.add(_KEY_MAP[part])
        elif len(part) == 1:
            keys.add(keyboard.KeyCode.from_char(part))
        else:
            _log.warning("Unknown hotkey token: %s", part)
            return None, None
    if keys:
        return "key", frozenset(keys)
    return None, None


class HotkeyListener:
    """Listens for keyboard/mouse hotkeys and injects pad events into the queue."""

    def __init__(self, event_queue: queue.Queue, log_fn=None):
        self._queue = event_queue
        self._log_fn = log_fn or (lambda *a: None)
        self._mouse_bindings: dict[mouse.Button, int] = {}   # button -> note
        self._key_bindings: dict[frozenset, int] = {}         # key combo -> note
        self._pressed_keys: set = set()
        self._kb_listener: keyboard.Listener | None = None
        self._mouse_listener: mouse.Listener | None = None
        self._lock = threading.Lock()

    def reload_bindings(self, mapper):
        """Rebuild hotkey->note mappings from the current preset."""
        new_mouse = {}
        new_keys = {}
        preset = mapper.current_preset
        for pad in preset.pads:
            hk = getattr(pad, "hotkey", "") or ""
            if not hk:
                continue
            kind, spec = _parse_hotkey(hk)
            if kind == "mouse":
                new_mouse[spec] = pad.note
            elif kind == "key":
                new_keys[spec] = pad.note
        with self._lock:
            self._mouse_bindings = new_mouse
            self._key_bindings = new_keys
        count = len(new_mouse) + len(new_keys)
        if count:
            _log.info("Hotkey bindings reloaded: %d binding(s)", count)

    def start(self):
        """Start keyboard and mouse listeners in background threads."""
        self._kb_listener = keyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self._kb_listener.daemon = True
        self._kb_listener.start()

        self._mouse_listener = mouse.Listener(
            on_click=self._on_mouse_click,
        )
        self._mouse_listener.daemon = True
        self._mouse_listener.start()
        _log.info("Hotkey listener started")

    def stop(self):
        """Stop both listeners."""
        if self._kb_listener:
            self._kb_listener.stop()
            self._kb_listener = None
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        _log.info("Hotkey listener stopped")

    def _inject_pad_press(self, note: int):
        """Put a synthetic pad_press event into the MIDI queue."""
        import time as _t
        evt = MidiEvent("pad_press", _t.time(), note=note, velocity=100)
        try:
            self._queue.put_nowait(evt)
        except queue.Full:
            pass
        # Schedule a release after 150ms
        def _release():
            rel = MidiEvent("pad_release", _t.time(), note=note, velocity=0)
            try:
                self._queue.put_nowait(rel)
            except queue.Full:
                pass
        threading.Timer(0.15, _release).start()

    def _on_key_press(self, key):
        self._pressed_keys.add(key)
        with self._lock:
            for combo, note in self._key_bindings.items():
                if combo.issubset(self._pressed_keys):
                    self._inject_pad_press(note)

    def _on_key_release(self, key):
        self._pressed_keys.discard(key)

    def _on_mouse_click(self, x, y, button, pressed):
        if not pressed:
            return
        with self._lock:
            note = self._mouse_bindings.get(button)
        if note is not None:
            self._inject_pad_press(note)
