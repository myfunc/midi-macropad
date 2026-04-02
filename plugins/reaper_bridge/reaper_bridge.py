"""REAPER Bridge plugin for midi-macropad.

Forwards MIDI events directly to REAPER via reapy StuffMIDIMessage API.
No virtual MIDI port (loopMIDI) required — events are injected straight
into REAPER's Virtual MIDI Keyboard input.

Layout:
  Pads 1-8   (Bank A, notes 16-23) -> macropad plugins (VoiceMeeter, Scribe)
  Pads 9-16  (Bank B, notes 24-31) -> REAPER (drum kit / instruments)
  Knobs 1-4  (CC 48-51)            -> macropad (volume, VM control)
  Knobs 5-8  (CC 52-55)            -> REAPER (mixer control)
  Piano keys (notes outside pads)  -> REAPER (instruments)
  Pitch bend                       -> REAPER (instrument expression)
"""
import json
import logging
import threading
import time
import traceback

from plugins.base import Plugin

_flog = logging.getLogger("reaper_bridge")
if not _flog.handlers:
    _flog.setLevel(logging.DEBUG)
    _fh = logging.FileHandler(
        "logs/reaper_bridge.log", encoding="utf-8", mode="a"
    )
    _fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(message)s", datefmt="%H:%M:%S"
    ))
    _flog.addHandler(_fh)


class ReaperBridgePlugin(Plugin):
    name = "REAPER Bridge"
    version = "0.2.0"
    description = "Bridge to REAPER DAW via reapy (direct MIDI injection)"

    def on_load(self, config: dict) -> None:
        # --- config ---
        pad_start = config.get("reaper_pad_notes_start", 24)
        pad_end = config.get("reaper_pad_notes_end", 32)
        self._reaper_pad_notes = set(range(pad_start, pad_end))

        mac_start = config.get("macropad_pad_notes_start", 16)
        mac_end = config.get("macropad_pad_notes_end", 24)
        self._macropad_pad_notes = set(range(mac_start, mac_end))

        ccs = config.get("reaper_knob_ccs", [52, 53, 54, 55])
        if isinstance(ccs, str):
            ccs = json.loads(ccs)
        self._reaper_knob_ccs = set(int(c) for c in ccs)

        self._forward_pitch_bend = config.get("forward_pitch_bend", True)
        self._forward_piano_keys = config.get("forward_piano_keys", True)
        self._status_interval = float(config.get("status_push_interval", 0.5))

        # MIDI channel for REAPER injection (0-15)
        self._midi_channel = int(config.get("midi_channel", 0))

        # --- state ---
        self._reapy = None
        self._reapy_api = None
        self._reapy_connected = False
        self._reapy_lock = threading.Lock()
        self._last_status_push = 0.0
        self._reaper_transport = "stopped"
        self._reaper_track_name = ""
        self._reaper_bpm = 120.0
        self._reaper_position = "0:00"
        self._forwarded_count = 0
        self._error_msg = ""

        # --- pad labels for Bank B ---
        self._bank_b_labels: dict[int, str] = {}
        for i, note in enumerate(sorted(self._reaper_pad_notes)):
            self._bank_b_labels[note] = f"R-Pad {i + 1}"

        # --- connect reapy (background) ---
        self._running = True
        self._reapy_thread = threading.Thread(
            target=self._reapy_connect_loop, daemon=True
        )
        self._reapy_thread.start()

        self._log("REAPER", "REAPER Bridge v0.2 loaded (direct injection mode)",
                  color=(100, 200, 255))

    def on_unload(self) -> None:
        self._running = False
        self._reapy_connected = False
        self._log("REAPER", "REAPER Bridge unloaded", color=(255, 200, 80))

    # ── reapy connection ──────────────────────────────────────────────

    def _reapy_connect_loop(self):
        """Background thread: keep reapy connected to REAPER."""
        while self._running:
            if not self._reapy_connected:
                try:
                    import reapy_boost as reapy
                    # Connect to local REAPER (activates reapy server)
                    reapy.connect(None)
                    # Test connection
                    project = reapy.Project()
                    _ = project.name
                    api = reapy.reascript_api

                    with self._reapy_lock:
                        self._reapy = reapy
                        self._reapy_api = api
                    self._reapy_connected = True
                    self._error_msg = ""
                    _flog.info("Connected to REAPER via reapy")
                    _flog.info("Reaper pads: %s", sorted(self._reaper_pad_notes))
                    _flog.info("Macropad pads: %s", sorted(self._macropad_pad_notes))
                    _flog.info("Reaper knobs: %s", sorted(self._reaper_knob_ccs))
                    self._log("REAPER", "Connected to REAPER via reapy",
                              color=(100, 255, 150))
                except ImportError:
                    self._error_msg = "reapy-boost not installed"
                    self._log("REAPER", self._error_msg, color=(255, 180, 80))
                    return
                except Exception:
                    self._reapy_connected = False
                    self._error_msg = "REAPER not running or reapy not configured"
                    time.sleep(3)
                    continue
            time.sleep(5)

    def _stuff_midi(self, status: int, data1: int, data2: int):
        """Inject a MIDI message into REAPER's Virtual MIDI Keyboard.

        Uses StuffMIDIMessage with mode=0 (Virtual MIDI Keyboard).
        The message plays on whichever track is record-armed.
        """
        if not self._reapy_connected or not self._reapy_api:
            _flog.warning("_stuff_midi SKIPPED: connected=%s api=%s",
                          self._reapy_connected, self._reapy_api is not None)
            return
        try:
            # mode 0 = Virtual MIDI Keyboard (record-armed track)
            self._reapy_api.StuffMIDIMessage(0, status, data1, data2)
            self._forwarded_count += 1
            _flog.debug("StuffMIDI OK: status=0x%02X d1=%d d2=%d (total=%d)",
                        status, data1, data2, self._forwarded_count)
        except Exception as exc:
            _flog.error("StuffMIDI FAILED: %s", exc)
            self._reapy_connected = False
            self._error_msg = "Lost connection to REAPER"

    # ── MIDI event hooks ──────────────────────────────────────────────

    def on_pad_press(self, note: int, velocity: int) -> bool:
        _flog.debug("on_pad_press note=%d vel=%d connected=%s", note, velocity, self._reapy_connected)
        # Bank B pads -> forward to REAPER
        if note in self._reaper_pad_notes:
            _flog.info("FORWARD Bank B pad note=%d vel=%d", note, velocity)
            self._stuff_midi(0x90 | self._midi_channel, note, velocity)
            self._log("REAPER", f"Pad note={note} vel={velocity} -> REAPER",
                      color=(100, 255, 180))
            return True

        # Bank A pads -> NOT ours
        if note in self._macropad_pad_notes:
            _flog.debug("SKIP Bank A pad note=%d", note)
            return False

        # Piano keys -> forward to REAPER
        if self._forward_piano_keys:
            _flog.info("FORWARD piano key note=%d vel=%d", note, velocity)
            self._stuff_midi(0x90 | self._midi_channel, note, velocity)
            self._log("REAPER", f"Key note={note} vel={velocity} -> REAPER",
                      color=(100, 255, 180))
            return True

        return False

    def on_pad_release(self, note: int) -> bool:
        if note in self._reaper_pad_notes:
            self._stuff_midi(0x80 | self._midi_channel, note, 0)
            return True

        if note in self._macropad_pad_notes:
            return False

        if self._forward_piano_keys:
            self._stuff_midi(0x80 | self._midi_channel, note, 0)
            return True

        return False

    def on_knob(self, cc: int, value: int) -> bool:
        _flog.debug("on_knob cc=%d val=%d ours=%s", cc, value, cc in self._reaper_knob_ccs)
        if cc not in self._reaper_knob_ccs:
            return False

        # Forward CC to REAPER
        _flog.info("FORWARD knob CC%d=%d", cc, value)
        self._stuff_midi(0xB0 | self._midi_channel, cc, value)

        # Also control mixer via reapy
        if self._reapy_connected:
            self._handle_reaper_knob(cc, value)

        return True

    def on_pitch_bend(self, value: int) -> bool:
        _flog.debug("on_pitch_bend value=%d", value)
        if not self._forward_pitch_bend:
            return False
        # MIDI pitch bend: 2 data bytes (LSB, MSB) from 14-bit value
        # reapy pitch value is -8192..+8191, MIDI is 0..16383
        midi_val = value + 8192
        lsb = midi_val & 0x7F
        msb = (midi_val >> 7) & 0x7F
        self._stuff_midi(0xE0 | self._midi_channel, lsb, msb)
        return True

    def _handle_reaper_knob(self, cc: int, value: int):
        """Map REAPER knobs to mixer actions via reapy."""
        cc_list = sorted(self._reaper_knob_ccs)
        if cc not in cc_list:
            return
        idx = cc_list.index(cc)
        level = value / 127.0

        def _set():
            try:
                project = self._reapy.Project()
                sel = project.selected_tracks
                if not sel:
                    return
                track = sel[0]
                if idx == 0:    # Knob 5: track volume
                    track.volume = level
                elif idx == 1:  # Knob 6: track pan
                    track.pan = (level * 2) - 1
                elif idx == 2:  # Knob 7: send level
                    sends = track.sends
                    if sends:
                        sends[0].volume = level
                elif idx == 3:  # Knob 8: master volume
                    project.master_track.volume = level
            except Exception:
                self._reapy_connected = False

        threading.Thread(target=_set, daemon=True).start()

    # ── Polling ────────────────────────────────────────────────────────

    def poll(self) -> None:
        now = time.time()
        if now - self._last_status_push < self._status_interval:
            return
        self._last_status_push = now

        if self._reapy_connected:
            self._push_status_to_reaper()
            self._pull_reaper_state()

    def _push_status_to_reaper(self):
        """Write macropad state to REAPER ExtState for the Lua panel."""
        def _push():
            try:
                api = self._reapy_api
                labels = {str(n): l for n, l in self._bank_b_labels.items()}
                api.SetExtState("macropad", "pad_labels", json.dumps(labels), False)
                api.SetExtState("macropad", "midi_connected", "1", False)
                api.SetExtState("macropad", "forwarded_count",
                                str(self._forwarded_count), False)
            except Exception:
                self._reapy_connected = False

        threading.Thread(target=_push, daemon=True).start()

    def _pull_reaper_state(self):
        """Read REAPER transport/track info for display."""
        def _pull():
            try:
                project = self._reapy.Project()
                state = project.play_state
                if state == 0:
                    self._reaper_transport = "stopped"
                elif state == 1:
                    self._reaper_transport = "playing"
                elif state == 5:
                    self._reaper_transport = "recording"
                else:
                    self._reaper_transport = f"state:{state}"

                self._reaper_bpm = project.bpm
                pos = project.cursor_position
                self._reaper_position = f"{int(pos // 60)}:{int(pos % 60):02d}"

                sel = project.selected_tracks
                self._reaper_track_name = sel[0].name if sel else "(no track)"
            except Exception:
                self._reapy_connected = False

        threading.Thread(target=_pull, daemon=True).start()

    # ── Transport control ─────────────────────────────────────────────

    def _reaper_action(self, command_id: int):
        def _exec():
            try:
                self._reapy_api.Main_OnCommandEx(command_id, 0, 0)
            except Exception:
                self._reapy_connected = False

        threading.Thread(target=_exec, daemon=True).start()

    # ── Action catalog ────────────────────────────────────────────────

    def get_action_catalog(self) -> list[dict]:
        return [
            {"id": "transport_play",    "label": "Play/Stop",   "icon": "play"},
            {"id": "transport_record",  "label": "Record",      "icon": "record"},
            {"id": "transport_undo",    "label": "Undo",        "icon": "undo"},
            {"id": "transport_metro",   "label": "Metronome",   "icon": "metronome"},
            {"id": "track_next",        "label": "Next Track",  "icon": "next"},
            {"id": "track_prev",        "label": "Prev Track",  "icon": "prev"},
            {"id": "track_mute",        "label": "Mute Track",  "icon": "mute"},
            {"id": "track_solo",        "label": "Solo Track",  "icon": "solo"},
        ]

    def execute_plugin_action(self, action_id: str, note: int, velocity: int) -> bool:
        actions = {
            "transport_play":   40044,
            "transport_record": 1013,
            "transport_undo":   40029,
            "transport_metro":  40364,
            "track_mute":       6,
            "track_solo":       7,
        }
        if action_id in actions:
            self._reaper_action(actions[action_id])
            self.emit_feedback("action.default")
            return True
        if action_id == "track_next":
            self._reaper_action(40285)
            self.emit_feedback("action.navigation")
            return True
        if action_id == "track_prev":
            self._reaper_action(40286)
            self.emit_feedback("action.navigation")
            return True
        return False

    # ── UI ─────────────────────────────────────────────────────────────

    def get_pad_labels(self) -> dict[int, str]:
        return dict(self._bank_b_labels)

    def get_status(self) -> tuple[str, tuple[int, int, int]] | None:
        if not self._reapy_connected:
            return (f"REAPER: {self._error_msg or 'connecting...'}", (255, 180, 80))

        parts = []
        icon = {"stopped": "||", "playing": ">", "recording": "REC"}
        parts.append(icon.get(self._reaper_transport, "?"))
        parts.append(f"{self._reaper_bpm:.0f}bpm")
        parts.append(self._reaper_position)
        if self._reaper_track_name:
            parts.append(self._reaper_track_name)
        return (" | ".join(parts), (100, 200, 255))

    def get_dynamic_label(self, action_id: str, note: int) -> str | None:
        if action_id == "transport_play":
            return "Stop" if self._reaper_transport == "playing" else "Play"
        if action_id == "transport_record":
            return "Stop Rec" if self._reaper_transport == "recording" else "Record"
        return None

    def register_windows(self) -> list[dict]:
        return [{"id": "reaper_bridge", "title": "REAPER Bridge", "default_open": True}]

    def build_window(self, window_id: str, parent_tag: str) -> None:
        if window_id != "reaper_bridge":
            return
        import dearpygui.dearpygui as dpg

        with dpg.group(parent=parent_tag):
            dpg.add_text("Connection", color=(180, 180, 180))
            dpg.add_separator()

            with dpg.group(horizontal=True):
                dpg.add_text("REAPER:")
                self._t_status = dpg.add_text(
                    "Connected" if self._reapy_connected else "Disconnected"
                )

            dpg.add_spacer(height=10)
            dpg.add_text("Transport", color=(180, 180, 180))
            dpg.add_separator()

            with dpg.group(horizontal=True):
                dpg.add_text("State:")
                self._t_transport = dpg.add_text(self._reaper_transport)

            with dpg.group(horizontal=True):
                dpg.add_text("BPM:")
                self._t_bpm = dpg.add_text(f"{self._reaper_bpm:.0f}")

            with dpg.group(horizontal=True):
                dpg.add_text("Track:")
                self._t_track = dpg.add_text(self._reaper_track_name or "-")

            dpg.add_spacer(height=10)
            dpg.add_text(f"MIDI forwarded: {self._forwarded_count}",
                         tag=f"reaper_fwd_{id(self)}")

            dpg.add_spacer(height=10)
            dpg.add_text("Layout", color=(180, 180, 180))
            dpg.add_separator()
            dpg.add_text("Pads 1-8  (Bank A) -> Macropad plugins",
                         color=(150, 200, 255))
            dpg.add_text("Pads 9-16 (Bank B) -> REAPER instruments",
                         color=(100, 255, 180))
            dpg.add_text("Knobs 1-4          -> Macropad control",
                         color=(150, 200, 255))
            dpg.add_text("Knobs 5-8          -> REAPER mixer",
                         color=(100, 255, 180))
            dpg.add_text("Piano keys         -> REAPER instruments",
                         color=(100, 255, 180))
            dpg.add_spacer(height=5)
            dpg.add_text("Mode: Direct injection (no virtual MIDI port)",
                         color=(200, 200, 100))

    def build_properties(self, parent_tag: str) -> None:
        import dearpygui.dearpygui as dpg

        with dpg.group(parent=parent_tag):
            dpg.add_text("REAPER Bridge Settings", color=(100, 200, 255))
            dpg.add_separator()
            dpg.add_text(f"REAPER pads: notes {sorted(self._reaper_pad_notes)}")
            dpg.add_text(f"REAPER knobs: CC {sorted(self._reaper_knob_ccs)}")
            dpg.add_text(f"MIDI channel: {self._midi_channel}")
            dpg.add_text(f"Forward pitch bend: {self._forward_pitch_bend}")
            dpg.add_text(f"Forward piano keys: {self._forward_piano_keys}")
            dpg.add_text("Method: reapy StuffMIDIMessage (direct)")

            if self._error_msg:
                dpg.add_spacer(height=10)
                dpg.add_text(self._error_msg, color=(255, 100, 100), wrap=400)
