"""Voicemeeter Banana remote-control plugin for MIDI Macropad.

Pad-based toggles and knob-based continuous control via VoicemeeterRemote64.dll.
Includes a real-time routing visualizer with level meters.
"""

import ctypes
import math
import time

from base import Plugin
from logger import get_logger

log = get_logger("voicemeeter")

_VM_ACTION_IDS: tuple[str, ...] = (
    "mic_mute",
    "desk_mute",
    "eq_toggle",
    "send2mic",
    "gate",
    "monitor",
    "comp",
    "reconnect",
)
_DEFAULT_VM_NOTES: tuple[int, ...] = tuple(range(16, 24))

_VM_CATALOG = [
    {"id": "mic_mute", "label": "Mic Mute", "description": "Toggle microphone mute (Strip 0)"},
    {"id": "desk_mute", "label": "Desktop Mute", "description": "Toggle desktop audio mute (Strip 3)"},
    {"id": "eq_toggle", "label": "EQ Toggle", "description": "Toggle EQ on Bus 3"},
    {"id": "send2mic", "label": "Send to Mic", "description": "Toggle Strip 4 -> B1 routing"},
    {"id": "gate", "label": "Gate", "description": "Toggle noise gate on mic"},
    {"id": "monitor", "label": "Monitor", "description": "Toggle mic monitoring to headphones (A1)"},
    {"id": "comp", "label": "Compressor", "description": "Toggle compressor on mic"},
    {"id": "reconnect", "label": "Reconnect", "description": "Reconnect to Voicemeeter"},
]

# Knob CCs (consumed only when Voicemeeter mode is active)
KNOB_MIC_GAIN = 48
KNOB_GATE = 49
KNOB_COMP = 50
KNOB_JOY_X = 16

_LEVEL_POLL_INTERVAL = 0.06  # ~16 fps for meters
_DEFAULT_DLL = r"C:\Program Files (x86)\VB\Voicemeeter\VoicemeeterRemote64.dll"

# Voicemeeter Banana channel map for VBVMR_GetLevel
# type 1 = post-fader input, type 2 = post-mute input, type 3 = bus output
# Strip[0]=ch0, Strip[3]=ch6, Strip[4]=ch8
# Bus[0](A1)=ch0, Bus[3](B1)=ch24
_CH_MIC = 0
_CH_DESK = 6
_CH_A1 = 0
_CH_B1 = 24


class VoicemeeterRemote:
    """Thin ctypes wrapper around VoicemeeterRemote64.dll."""

    def __init__(self, dll_path: str = _DEFAULT_DLL):
        self._path = dll_path
        self._dll = None
        self._connected = False

    def load(self) -> bool:
        try:
            self._dll = ctypes.cdll.LoadLibrary(self._path)
            return True
        except OSError as e:
            log.error("Cannot load DLL %s: %s", self._path, e)
            return False

    def login(self) -> bool:
        if not self._dll:
            return False
        r = self._dll.VBVMR_Login()
        self._connected = r in (0, 1)  # 0=ok, 1=ok+launched VM
        if self._connected:
            time.sleep(0.3)
        return self._connected

    def logout(self):
        if self._dll and self._connected:
            try:
                self._dll.VBVMR_Logout()
            except Exception:
                pass
            self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def get(self, name: str) -> float | None:
        if not self._connected:
            return None
        v = ctypes.c_float()
        if self._dll.VBVMR_GetParameterFloat(name.encode("ascii"), ctypes.byref(v)) == 0:
            return v.value
        return None

    def set(self, name: str, value: float) -> bool:
        if not self._connected:
            return False
        return self._dll.VBVMR_SetParameterFloat(
            name.encode("ascii"), ctypes.c_float(value)) == 0

    def level(self, kind: int, channel: int) -> float:
        if not self._connected:
            return 0.0
        v = ctypes.c_float()
        if self._dll.VBVMR_GetLevel(
                ctypes.c_long(kind), ctypes.c_long(channel), ctypes.byref(v)) == 0:
            return v.value
        return 0.0

    def dirty(self) -> bool:
        if not self._connected or not self._dll:
            return False
        return self._dll.VBVMR_IsParametersDirty() == 1


class VoicemeeterPlugin(Plugin):
    name = "Voicemeeter"
    version = "1.0.0"
    description = "Control Voicemeeter Banana — routing, EQ, gate, compression"
    mode_name = "Voicemeeter"

    def __init__(self):
        self._vm = VoicemeeterRemote()
        self._active = False

        # toggle states
        self._mic_mute = False
        self._desk_mute = False
        self._eq_on = True
        self._s2m_b1 = False
        self._gate_on = True
        self._monitor = False
        self._comp_on = True

        # continuous values
        self._mic_gain = 0.0
        self._gate_val = 3.0
        self._comp_val = 3.0
        self._audibility = 0.0
        self._b1_baseline = 0.0
        self._joy_offset = 0.0

        # ducking (smooth)
        self._duck_enabled = False
        self._duck_active = False
        self._duck_threshold = 0.015
        self._duck_amount = -18.0
        self._duck_hold = 0.6
        self._duck_attack_time = 0.25  # seconds of continuous voice before ducking starts
        self._duck_voice_since = 0.0   # timestamp when voice was first detected above threshold
        self._duck_release_at = 0.0
        self._duck_current = 0.0
        self._desk_gain_saved = 0.0
        self._s2m_gain_saved = 0.0

        # smoothed levels for meters
        self._lvl_mic = 0.0
        self._lvl_desk = 0.0
        self._lvl_a1 = 0.0
        self._lvl_b1 = 0.0

        # knob assignment: "vm" = voicemeeter control, "default" = pass through
        self._knob_mode = {
            48: "default",   # K1: default=Master Volume, vm=Mic Gain
            49: "default",   # K2: default=Mic Volume, vm=Gate
            50: "default",   # K3: default=Spotify Volume, vm=Compressor
        }

        # UI
        self._t: dict[str, int | str] = {}
        self._win_ok = False
        self._last_poll = 0.0
        self._ui_dirty = True
        self._owned_notes: list[int] = []
        self._note_to_action: dict[int, str] = {}

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def on_load(self, config: dict) -> None:
        dll = config.get("dll_path", _DEFAULT_DLL)
        self._vm = VoicemeeterRemote(dll)
        self._load_settings()
        if not self._vm.load():
            return
        if not self._vm.login():
            return
        self._sync_state()
        log.info("Voicemeeter plugin connected")

    def on_unload(self) -> None:
        self._save_settings()
        self._vm.logout()
        self._t.clear()
        self._win_ok = False

    # ── settings persistence ───────────────────────────────────────────────────

    def _save_settings(self):
        try:
            import settings
            settings.put("vm_plugin", {
                "knob_mode": self._knob_mode,
                "duck_enabled": self._duck_enabled,
                "duck_threshold": self._duck_threshold,
                "duck_amount": self._duck_amount,
                "duck_hold": self._duck_hold,
                "duck_attack_time": self._duck_attack_time,
            })
        except Exception:
            pass

    def _load_settings(self):
        try:
            import settings
            s = settings.get("vm_plugin")
            if not s or not isinstance(s, dict):
                return
            km = s.get("knob_mode")
            if isinstance(km, dict):
                self._knob_mode = {int(k): v for k, v in km.items()}
            if "duck_enabled" in s:
                self._duck_enabled = bool(s["duck_enabled"])
            if "duck_threshold" in s:
                self._duck_threshold = float(s["duck_threshold"])
            if "duck_amount" in s:
                self._duck_amount = float(s["duck_amount"])
            if "duck_hold" in s:
                self._duck_hold = float(s["duck_hold"])
            if "duck_attack_time" in s:
                self._duck_attack_time = float(s["duck_attack_time"])
        except Exception:
            pass

    def on_mode_changed(self, mode_name: str) -> None:
        self._active = mode_name == self.mode_name

    def set_owned_notes(self, notes: set[int]) -> None:
        self._active = bool(notes)
        ordered = sorted(notes)
        self._owned_notes = ordered
        self._note_to_action.clear()
        for i, n in enumerate(ordered):
            if i < len(_VM_ACTION_IDS):
                self._note_to_action[n] = _VM_ACTION_IDS[i]

    def _ensure_action_mapping(self) -> None:
        if self._note_to_action:
            return
        for i, n in enumerate(_DEFAULT_VM_NOTES):
            if i < len(_VM_ACTION_IDS):
                self._note_to_action[n] = _VM_ACTION_IDS[i]
        self._owned_notes = list(_DEFAULT_VM_NOTES)

    # ── state sync ─────────────────────────────────────────────────────────────

    def _sync_state(self):
        g = self._vm.get
        if not self._vm.connected:
            return
        self._mic_mute = bool(g("Strip[0].Mute") or 0)
        self._desk_mute = bool(g("Strip[3].Mute") or 0)
        self._eq_on = bool(g("Bus[3].EQ.on") or 0)
        self._s2m_b1 = bool(g("Strip[4].B1") or 0)
        self._monitor = bool(g("Strip[0].A1") or 0)
        self._mic_gain = g("Strip[0].Gain") or 0.0
        self._audibility = g("Strip[0].Audibility") or 0.0
        self._b1_baseline = g("Bus[3].Gain") or 0.0

        gv = g("Strip[0].Gate") or 0.0
        self._gate_on = gv > 0.1
        if gv > 0.1:
            self._gate_val = gv
        cv = g("Strip[0].Comp") or 0.0
        self._comp_on = cv > 0.1
        if cv > 0.1:
            self._comp_val = cv
        self._ui_dirty = True

    @staticmethod
    def _decay(old: float, new: float) -> float:
        return new if new > old else old * 0.82 + new * 0.18

    @staticmethod
    def _to_meter(level: float) -> float:
        if level < 0.00001:
            return 0.0
        db = 20.0 * math.log10(level)
        return max(0.0, min(1.0, (db + 60.0) / 60.0))

    def _sync_levels(self):
        vm = self._vm
        # type 2 = post-mute: respects Strip Mute so ducking won't fire on muted mic
        self._lvl_mic = self._decay(self._lvl_mic, vm.level(2, _CH_MIC))
        self._lvl_desk = self._decay(self._lvl_desk, vm.level(1, _CH_DESK))
        self._lvl_a1 = self._decay(self._lvl_a1, vm.level(3, _CH_A1))
        self._lvl_b1 = self._decay(self._lvl_b1, vm.level(3, _CH_B1))

    # ── poll (every render frame) ──────────────────────────────────────────────

    def poll(self) -> None:
        if not self._vm.connected:
            return
        if self._vm.dirty():
            self._sync_state()
        if not self._win_ok:
            return
        now = time.time()
        if now - self._last_poll >= _LEVEL_POLL_INTERVAL:
            self._sync_levels()
            self._last_poll = now
            self._refresh_levels()
            if self._duck_enabled:
                self._process_ducking()
        if self._ui_dirty:
            self._ui_dirty = False
            self._refresh_routing()

    # ── MIDI pads ──────────────────────────────────────────────────────────────

    def on_pad_press(self, note: int, velocity: int) -> bool:
        if not self._active or not self._vm.connected:
            return False
        self._ensure_action_mapping()
        aid = self._note_to_action.get(note)
        if aid is None:
            return False

        if aid == "mic_mute":
            self._mic_mute = not self._mic_mute
            self._vm.set("Strip[0].Mute", float(self._mic_mute))
            self._notify("Mic", "MUTED" if self._mic_mute else "LIVE")
            return True

        if aid == "desk_mute":
            self._desk_mute = not self._desk_mute
            self._vm.set("Strip[3].Mute", float(self._desk_mute))
            self._notify("Desktop", "MUTED" if self._desk_mute else "LIVE")
            return True

        if aid == "eq_toggle":
            self._eq_on = not self._eq_on
            self._vm.set("Bus[3].EQ.on", float(self._eq_on))
            self._notify("EQ B1", "ON" if self._eq_on else "OFF")
            return True

        if aid == "send2mic":
            self._s2m_b1 = not self._s2m_b1
            self._vm.set("Strip[4].B1", float(self._s2m_b1))
            self._notify("Send2Mic->B1", "ON" if self._s2m_b1 else "OFF")
            return True

        if aid == "gate":
            if self._gate_on:
                self._gate_on = False
                self._vm.set("Strip[0].Gate", 0.0)
            else:
                self._gate_on = True
                self._vm.set("Strip[0].Gate", self._gate_val)
            self._notify("Gate", "ON" if self._gate_on else "OFF")
            return True

        if aid == "monitor":
            self._monitor = not self._monitor
            self._vm.set("Strip[0].A1", float(self._monitor))
            self._notify("Monitor", "ON" if self._monitor else "OFF")
            return True

        if aid == "comp":
            if self._comp_on:
                self._comp_on = False
                self._vm.set("Strip[0].Comp", 0.0)
            else:
                self._comp_on = True
                self._vm.set("Strip[0].Comp", self._comp_val)
            self._notify("Compressor", "ON" if self._comp_on else "OFF")
            return True

        if aid == "reconnect":
            self._vm.logout()
            if self._vm.login():
                self._sync_state()
                self._notify("Voicemeeter", "RECONNECTED")
            else:
                self._notify("Voicemeeter", "CONNECT FAILED", err=True)
            return True

        return False

    def on_pad_release(self, note: int) -> bool:
        self._ensure_action_mapping()
        return self._active and note in self._note_to_action

    # ── MIDI knobs ─────────────────────────────────────────────────────────────

    def on_knob(self, cc: int, value: int) -> bool:
        if not self._active or not self._vm.connected:
            return False

        if cc in self._knob_mode and self._knob_mode[cc] == "default":
            return False

        if cc == KNOB_MIC_GAIN:
            self._mic_gain = -60.0 + (value / 127.0) * 72.0
            self._vm.set("Strip[0].Gain", self._mic_gain)
            self._ui_dirty = True
            return True

        if cc == KNOB_GATE:
            v = (value / 127.0) * 10.0
            self._vm.set("Strip[0].Gate", v)
            self._gate_on = v > 0.1
            if v > 0.1:
                self._gate_val = v
            self._ui_dirty = True
            return True

        if cc == KNOB_COMP:
            v = (value / 127.0) * 10.0
            self._vm.set("Strip[0].Comp", v)
            self._comp_on = v > 0.1
            if v > 0.1:
                self._comp_val = v
            self._ui_dirty = True
            return True

        if cc == KNOB_JOY_X:
            self._audibility = (value / 127.0) * 10.0
            self._vm.set("Strip[0].Audibility", self._audibility)
            self._ui_dirty = True
            return True

        return False

    # Joystick is now used globally for mode switching (see main.py)

    # ── helpers ─────────────────────────────────────────────────────────────────

    def _process_ducking(self):
        now = time.time()
        mic_above = self._lvl_mic > self._duck_threshold

        if mic_above:
            if self._duck_voice_since == 0.0:
                self._duck_voice_since = now
            voice_duration = now - self._duck_voice_since
        else:
            self._duck_voice_since = 0.0
            voice_duration = 0.0

        voice_confirmed = voice_duration >= self._duck_attack_time

        if voice_confirmed:
            self._duck_release_at = now + self._duck_hold
            target = self._duck_amount
            if not self._duck_active:
                self._desk_gain_saved = self._vm.get("Strip[3].Gain") or 0.0
                self._s2m_gain_saved = self._vm.get("Strip[4].Gain") or 0.0
                self._duck_active = True
        elif self._duck_active and now >= self._duck_release_at:
            target = 0.0
        elif self._duck_active:
            target = self._duck_amount
        else:
            return

        speed = 0.35 if target < self._duck_current else 0.12
        self._duck_current += (target - self._duck_current) * speed
        self._vm.set("Strip[3].Gain", self._desk_gain_saved + self._duck_current)
        self._vm.set("Strip[4].Gain", self._s2m_gain_saved + self._duck_current)
        if target == 0.0 and abs(self._duck_current) < 0.3:
            self._vm.set("Strip[3].Gain", self._desk_gain_saved)
            self._vm.set("Strip[4].Gain", self._s2m_gain_saved)
            self._duck_active = False
            self._duck_current = 0.0

    def _notify(self, target: str, state: str, err: bool = False):
        self._ui_dirty = True
        if self._log_fn:
            if err:
                c = (255, 80, 80)
            elif state in ("MUTED", "OFF", "CONNECT FAILED"):
                c = (255, 120, 80)
            else:
                c = (100, 255, 150)
            self._log_fn("VM", f"{target}: {state}", color=c)
        self.emit_feedback("action.default")

    # ── pad labels & status ────────────────────────────────────────────────────

    def get_action_catalog(self) -> list[dict]:
        return list(_VM_CATALOG)

    def get_pad_labels(self) -> dict[int, str]:
        if not self._active:
            return {}
        self._ensure_action_mapping()
        out: dict[int, str] = {}
        for note, aid in self._note_to_action.items():
            if aid == "mic_mute":
                out[note] = "MIC OFF" if self._mic_mute else "MIC"
            elif aid == "desk_mute":
                out[note] = "DESK OFF" if self._desk_mute else "DESK"
            elif aid == "eq_toggle":
                out[note] = "EQ ON" if self._eq_on else "EQ OFF"
            elif aid == "send2mic":
                out[note] = "S2M > B1" if self._s2m_b1 else "S2M ---"
            elif aid == "gate":
                out[note] = "GATE" if self._gate_on else "GATE OFF"
            elif aid == "monitor":
                out[note] = "MON" if self._monitor else "MON OFF"
            elif aid == "comp":
                out[note] = "COMP" if self._comp_on else "COMP OFF"
            elif aid == "reconnect":
                out[note] = "RESYNC"
        return out

    def get_status(self) -> tuple[str, tuple[int, int, int]] | None:
        if not self._active:
            return None
        if not self._vm.connected:
            return ("VM: disconnected", (255, 80, 80))
        flags = []
        if self._mic_mute:
            flags.append("MIC MUTED")
        if self._s2m_b1:
            flags.append("S2M>B1")
        if not self._eq_on:
            flags.append("EQ OFF")
        text = " | ".join(flags) if flags else "VM: ready"
        color = (255, 180, 80) if flags else (100, 255, 150)
        return (text, color)

    # ── UI: routing visualizer ─────────────────────────────────────────────────

    def register_windows(self) -> list[dict]:
        return [{"id": "vm_routing", "title": "Voicemeeter", "default_open": True}]

    def build_window(self, window_id: str, parent_tag: str) -> None:
        if window_id != "vm_routing":
            return
        import dearpygui.dearpygui as dpg

        self._win_ok = True
        t = self._t
        COL_W = 260

        with dpg.group(horizontal=True, parent=parent_tag):
            left_col = dpg.add_child_window(width=COL_W, border=False)
            right_col = dpg.add_child_window(width=-1, border=False)

        # ── LEFT COLUMN ──────────────────────────────────────────────────────

        # ── connection status ──
        t["conn"] = dpg.add_text(
            "Connected" if self._vm.connected else "Not connected",
            parent=left_col,
            color=(100, 255, 150) if self._vm.connected else (255, 80, 80))
        dpg.add_separator(parent=left_col)
        dpg.add_spacer(height=6, parent=left_col)

        # ── routing table ──
        dpg.add_text("ROUTING", parent=left_col, color=(140, 140, 170))
        dpg.add_spacer(height=2, parent=left_col)
        for label, pfx, a1_default, b1_default in [
            ("MIC       ", "mic", False, True),
            ("DESKTOP   ", "dsk", True, False),
            ("SEND2MIC  ", "s2m", True, False),
        ]:
            with dpg.group(horizontal=True, parent=left_col):
                t[f"{pfx}_dot"] = dpg.add_text("*", color=(100, 255, 150))
                dpg.add_text(f" {label}")
                dpg.add_text("->")
                a1c = (100, 255, 150) if a1_default else (60, 60, 70)
                b1c = (100, 255, 150) if b1_default else (60, 60, 70)
                t[f"{pfx}_a1"] = dpg.add_text(" A1 ", color=a1c)
                t[f"{pfx}_b1"] = dpg.add_text(" B1 ", color=b1c)

        dpg.add_spacer(height=8, parent=left_col)
        dpg.add_separator(parent=left_col)
        dpg.add_spacer(height=6, parent=left_col)

        # ── audience ──
        dpg.add_text("AUDIENCE", parent=left_col, color=(140, 140, 170))
        dpg.add_spacer(height=2, parent=left_col)
        with dpg.group(horizontal=True, parent=left_col):
            dpg.add_text("You hear:     ", color=(120, 120, 150))
            t["you"] = dpg.add_text("---", color=(100, 200, 255))
        with dpg.group(horizontal=True, parent=left_col):
            dpg.add_text("Listener gets:", color=(120, 120, 150))
            t["them"] = dpg.add_text("---", color=(255, 180, 80))

        dpg.add_spacer(height=8, parent=left_col)
        dpg.add_separator(parent=left_col)
        dpg.add_spacer(height=6, parent=left_col)

        # ── level meters ──
        dpg.add_text("LEVELS", parent=left_col, color=(140, 140, 170))
        dpg.add_spacer(height=2, parent=left_col)
        for lbl, key in [("MIC       ", "lm"), ("DESKTOP   ", "ld"),
                          ("A1 Headph ", "la"), ("B1 MicOut ", "lb")]:
            with dpg.group(horizontal=True, parent=left_col):
                dpg.add_text(lbl, color=(120, 120, 150))
                t[key] = dpg.add_progress_bar(default_value=0.0, width=180)

        # ── RIGHT COLUMN ─────────────────────────────────────────────────────

        # ── knob assignments ──
        dpg.add_text("KNOBS", parent=right_col, color=(140, 140, 170))
        dpg.add_spacer(height=2, parent=right_col)

        _knob_options = {
            48: ["Master Volume", "VM: Mic Gain"],
            49: ["Mic Volume", "VM: Gate"],
            50: ["Spotify Volume", "VM: Compressor"],
        }

        for cc, options in _knob_options.items():
            label_map = {"default": options[0], "vm": options[1]}
            current = label_map[self._knob_mode.get(cc, "default")]

            def _make_cb(cc_val):
                def _cb(sender, value):
                    self._knob_mode[cc_val] = "vm" if "VM:" in value else "default"
                    self._save_settings()
                return _cb

            with dpg.group(horizontal=True, parent=right_col):
                dpg.add_text(f"K{cc - 47}:", color=(120, 120, 150))
                dpg.add_combo(
                    options,
                    default_value=current,
                    callback=_make_cb(cc),
                    width=160)

        dpg.add_spacer(height=8, parent=right_col)
        dpg.add_separator(parent=right_col)
        dpg.add_spacer(height=6, parent=right_col)

        # ── processing chain ──
        dpg.add_text("PROCESSING", parent=right_col, color=(140, 140, 170))
        dpg.add_spacer(height=2, parent=right_col)
        with dpg.group(horizontal=True, parent=right_col):
            dpg.add_text("Gate:", color=(120, 120, 150))
            t["pg"] = dpg.add_text("ON", color=(100, 255, 150))
            dpg.add_text("  Comp:", color=(120, 120, 150))
            t["pc"] = dpg.add_text("ON", color=(100, 255, 150))
            dpg.add_text("  EQ:", color=(120, 120, 150))
            t["pe"] = dpg.add_text("ON", color=(100, 255, 150))
        with dpg.group(horizontal=True, parent=right_col):
            dpg.add_text("Gain: ", color=(120, 120, 150))
            t["gn"] = dpg.add_text("+0.0 dB")

        dpg.add_spacer(height=8, parent=right_col)
        dpg.add_separator(parent=right_col)
        dpg.add_spacer(height=6, parent=right_col)

        # ── ducking ──
        dpg.add_text("DUCKING", parent=right_col, color=(140, 140, 170))
        dpg.add_spacer(height=2, parent=right_col)

        def _on_duck_toggle(sender, value):
            self._duck_enabled = value
            if not value and self._duck_active:
                self._vm.set("Strip[3].Gain", self._desk_gain_saved)
                self._vm.set("Strip[4].Gain", self._s2m_gain_saved)
                self._duck_active = False
            self._save_settings()

        t["duck_cb"] = dpg.add_checkbox(
            label="Auto-duck music when speaking",
            default_value=self._duck_enabled,
            callback=_on_duck_toggle,
            parent=right_col)

        dpg.add_spacer(height=4, parent=right_col)
        dpg.add_text("Threshold:", color=(120, 120, 150), parent=right_col)

        def _on_thresh(sender, value):
            self._duck_threshold = value
            self._save_settings()

        dpg.add_slider_float(
            default_value=self._duck_threshold,
            min_value=0.002, max_value=0.10,
            format="%.3f",
            callback=_on_thresh,
            parent=right_col, width=180,
            tag="duck_thresh_slider")

        dpg.add_text("Voice delay (s):", color=(120, 120, 150), parent=right_col)

        def _on_attack_time(sender, value):
            self._duck_attack_time = value
            self._save_settings()

        dpg.add_slider_float(
            default_value=self._duck_attack_time,
            min_value=0.05, max_value=1.0,
            format="%.2f s",
            callback=_on_attack_time,
            parent=right_col, width=180,
            tag="duck_attack_slider")

        dpg.add_text("Hold time (s):", color=(120, 120, 150), parent=right_col)

        def _on_hold(sender, value):
            self._duck_hold = value
            self._save_settings()

        dpg.add_slider_float(
            default_value=self._duck_hold,
            min_value=0.1, max_value=3.0,
            format="%.1f s",
            callback=_on_hold,
            parent=right_col, width=180,
            tag="duck_hold_slider")

        dpg.add_text("Duck amount (dB):", color=(120, 120, 150), parent=right_col)

        def _on_amount(sender, value):
            self._duck_amount = value
            self._save_settings()

        dpg.add_slider_float(
            default_value=self._duck_amount,
            min_value=-30.0, max_value=0.0,
            format="%.0f dB",
            callback=_on_amount,
            parent=right_col, width=180,
            tag="duck_amount_slider")

        with dpg.group(horizontal=True, parent=right_col):
            dpg.add_text("Status: ", color=(120, 120, 150))
            t["duck_st"] = dpg.add_text("OFF", color=(60, 60, 70))

    def _refresh_routing(self):
        """Update routing indicators, audience, and processing status (on state change only)."""
        if not self._win_ok:
            return
        try:
            import dearpygui.dearpygui as dpg
        except Exception:
            return

        t = self._t
        ON = (100, 255, 150)
        OFF = (60, 60, 70)
        RED = (255, 80, 80)

        def _s(k, value=None, color=None):
            uid = t.get(k)
            if uid is None or not dpg.does_item_exist(uid):
                return
            if value is not None:
                dpg.set_value(uid, value)
            if color is not None:
                dpg.configure_item(uid, color=color)

        # routing indicators
        _s("mic_dot", color=RED if self._mic_mute else ON)
        _s("mic_a1", color=ON if self._monitor else OFF)
        _s("mic_b1", color=RED if self._mic_mute else ON)
        _s("dsk_dot", color=RED if self._desk_mute else ON)
        _s("dsk_a1", color=ON)
        _s("dsk_b1", color=OFF)
        _s("s2m_dot", color=ON)
        _s("s2m_a1", color=ON)
        _s("s2m_b1", color=ON if self._s2m_b1 else OFF)

        # audience summary
        you = []
        if not self._desk_mute:
            you.append("Desktop")
        you.append("Send2Mic")
        if self._monitor:
            you.append("Mic")
        _s("you", ", ".join(you) or "nothing")

        them = []
        if not self._mic_mute:
            them.append("Mic")
        if self._s2m_b1:
            them.append("Send2Mic")
        _s("them", ", ".join(them) or "nothing",
           color=(255, 180, 80) if them else RED)

        # processing
        _s("pg", "ON" if self._gate_on else "OFF",
           color=ON if self._gate_on else OFF)
        _s("pc", "ON" if self._comp_on else "OFF",
           color=ON if self._comp_on else OFF)
        _s("pe", "ON" if self._eq_on else "OFF",
           color=ON if self._eq_on else OFF)
        _s("gn", f"{self._mic_gain:+.1f} dB")

    def _refresh_levels(self):
        """Update level meter bars (called on timer interval)."""
        if not self._win_ok:
            return
        try:
            import dearpygui.dearpygui as dpg
        except Exception:
            return

        t = self._t

        def _bar(k, v):
            uid = t.get(k)
            if uid is not None and dpg.does_item_exist(uid):
                dpg.set_value(uid, min(1.0, max(0.0, v)))

        _bar("lm", self._to_meter(self._lvl_mic))
        _bar("ld", self._to_meter(self._lvl_desk))
        _bar("la", self._to_meter(self._lvl_a1))
        _bar("lb", self._to_meter(self._lvl_b1))

        # ducking indicator
        dk = self._t.get("duck_st")
        if dk is not None and dpg.does_item_exist(dk):
            if not self._duck_enabled:
                dpg.set_value(dk, "OFF")
                dpg.configure_item(dk, color=(60, 60, 70))
            elif self._duck_active:
                dpg.set_value(dk, "DUCKING")
                dpg.configure_item(dk, color=(255, 180, 80))
            else:
                dpg.set_value(dk, "Listening")
                dpg.configure_item(dk, color=(100, 255, 150))

    def build_properties(self, parent_tag: str) -> None:
        import dearpygui.dearpygui as dpg
        dpg.add_text("Voicemeeter Control", parent=parent_tag,
                      color=(100, 200, 255))
        dpg.add_spacer(height=4, parent=parent_tag)
        st = "Connected" if self._vm.connected else "Not connected"
        sc = (100, 255, 150) if self._vm.connected else (255, 80, 80)
        dpg.add_text(f"Status: {st}", parent=parent_tag, color=sc)
        if not self._vm.connected:
            return

        # ── pad map ──
        dpg.add_spacer(height=8, parent=parent_tag)
        dpg.add_text("Pad Map:", parent=parent_tag, color=(140, 140, 170))
        labels = self.get_pad_labels()
        self._ensure_action_mapping()
        for i, n in enumerate(self._owned_notes, start=1):
            dpg.add_text(f"  {i}. {labels.get(n, '---')}", parent=parent_tag)

        # ── knob assignments ──
        dpg.add_spacer(height=8, parent=parent_tag)
        dpg.add_text("Knob Assignments:", parent=parent_tag, color=(140, 140, 170))
        k1 = "VM: Mic Gain" if self._knob_mode.get(48) == "vm" else "Master Volume"
        k2 = "VM: Gate" if self._knob_mode.get(49) == "vm" else "Mic Volume"
        k3 = "VM: Compressor" if self._knob_mode.get(50) == "vm" else "Spotify Volume"
        dpg.add_text(f"  K1 (CC48): {k1}", parent=parent_tag)
        dpg.add_text(f"  K2 (CC49): {k2}", parent=parent_tag)
        dpg.add_text(f"  K3 (CC50): {k3}", parent=parent_tag)
        dpg.add_text("  Joy X: Audibility", parent=parent_tag)
        dpg.add_text("  Joy Y: B1 Output Gain", parent=parent_tag)

        # ── mic parameters ──
        dpg.add_spacer(height=8, parent=parent_tag)
        dpg.add_separator(parent=parent_tag)
        dpg.add_spacer(height=4, parent=parent_tag)
        dpg.add_text("Mic Strip:", parent=parent_tag, color=(140, 140, 170))

        dpg.add_text(f"  Gain: {self._mic_gain:+.1f} dB", parent=parent_tag)
        dpg.add_text(f"  Gate: {'ON' if self._gate_on else 'OFF'}"
                     f" ({self._gate_val:.1f})", parent=parent_tag)
        dpg.add_text(f"  Comp: {'ON' if self._comp_on else 'OFF'}"
                     f" ({self._comp_val:.1f})", parent=parent_tag)
        dpg.add_text(f"  Audibility: {self._audibility:.1f}", parent=parent_tag)
        dpg.add_text(f"  Mute: {'YES' if self._mic_mute else 'no'}",
                     parent=parent_tag,
                     color=(255, 80, 80) if self._mic_mute else (100, 255, 150))

        # ── bus parameters ──
        dpg.add_spacer(height=8, parent=parent_tag)
        dpg.add_text("Bus B1 (Mic Out):", parent=parent_tag, color=(140, 140, 170))
        dpg.add_text(f"  EQ: {'ON' if self._eq_on else 'OFF'}", parent=parent_tag)
        dpg.add_text(f"  Gain: {self._b1_baseline:+.1f} dB", parent=parent_tag)
        dpg.add_text("  Joystick: mode switcher (global)", parent=parent_tag)

        # ── routing summary ──
        dpg.add_spacer(height=8, parent=parent_tag)
        dpg.add_text("Routing:", parent=parent_tag, color=(140, 140, 170))
        dpg.add_text(f"  Mic -> A1: {'ON' if self._monitor else 'OFF'}"
                     f"  B1: ON", parent=parent_tag)
        dpg.add_text(f"  Desktop mute: {'YES' if self._desk_mute else 'no'}",
                     parent=parent_tag)
        dpg.add_text(f"  S2M -> B1: {'ON' if self._s2m_b1 else 'OFF'}",
                     parent=parent_tag)

        # ── ducking state ──
        dpg.add_spacer(height=8, parent=parent_tag)
        dpg.add_text("Ducking:", parent=parent_tag, color=(140, 140, 170))
        dpg.add_text(f"  Enabled: {'yes' if self._duck_enabled else 'no'}",
                     parent=parent_tag)
        dpg.add_text(f"  Threshold: {self._duck_threshold:.3f}", parent=parent_tag)
        dpg.add_text(f"  Voice delay: {self._duck_attack_time:.2f}s",
                     parent=parent_tag)
        dpg.add_text(f"  Hold time: {self._duck_hold:.1f}s", parent=parent_tag)
        dpg.add_text(f"  Amount: {self._duck_amount:.0f} dB", parent=parent_tag)
