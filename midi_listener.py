"""MIDI listener — background thread that reads from MPK Mini Play."""
import threading
import queue
import time
import mido


class MidiEvent:
    """Normalized MIDI event."""
    __slots__ = ("type", "note", "velocity", "cc", "value", "pitch", "channel", "timestamp")
    
    def __init__(self, type, timestamp, **kw):
        self.type = type
        self.timestamp = timestamp
        self.note = kw.get("note")
        self.velocity = kw.get("velocity")
        self.cc = kw.get("cc")
        self.value = kw.get("value")
        self.pitch = kw.get("pitch")
        self.channel = kw.get("channel", 0)

    def __repr__(self):
        parts = [f"type={self.type}"]
        if self.note is not None:
            parts.append(f"note={self.note}")
        if self.velocity is not None:
            parts.append(f"vel={self.velocity}")
        if self.cc is not None:
            parts.append(f"cc={self.cc}")
        if self.value is not None:
            parts.append(f"val={self.value}")
        if self.pitch is not None:
            parts.append(f"pitch={self.pitch}")
        return f"MidiEvent({', '.join(parts)})"


class MidiListener:
    """Runs in a background thread, pushes MidiEvent to a queue."""
    
    def __init__(self, device_name: str, event_queue: queue.Queue, log_fn=None):
        self.device_name = device_name.lower()
        self.event_queue = event_queue
        self._thread = None
        self._running = False
        self.port_name = None
        self.connected = False
        self._log = log_fn or (lambda *args, **kwargs: None)

    def _emit_log(self, level: str, message: str):
        self._log(level, message)
    
    def find_port(self) -> str | None:
        try:
            for name in mido.get_input_names():
                if self.device_name in name.lower():
                    return name
        except Exception as exc:
            self._emit_log("error", f"MIDI device discovery failed: {exc}")
        return None
    
    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
    
    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
    
    def _run(self):
        warned_missing = False
        while self._running:
            self.port_name = self.find_port()
            if not self.port_name:
                self.connected = False
                if not warned_missing:
                    self._emit_log(
                        "warning",
                        f"MIDI device '{self.device_name}' not found. Waiting for connection.",
                    )
                    warned_missing = True
                time.sleep(1)
                continue
            
            try:
                warned_missing = False
                with mido.open_input(self.port_name) as port:
                    self.connected = True
                    self._emit_log("info", f"Connected to MIDI input: {self.port_name}")
                    for msg in port:
                        if not self._running:
                            break
                        event = self._normalize(msg)
                        if event:
                            self.event_queue.put(event)
            except Exception as exc:
                self.connected = False
                self._emit_log("error", f"MIDI input '{self.port_name}' failed: {exc}")
                time.sleep(1)
    
    def _normalize(self, msg) -> MidiEvent | None:
        ts = time.time()
        if msg.type == "note_on" and msg.velocity > 0:
            return MidiEvent("pad_press", ts, note=msg.note, velocity=msg.velocity, channel=msg.channel)
        if msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            return MidiEvent("pad_release", ts, note=msg.note, velocity=0, channel=msg.channel)
        if msg.type == "control_change":
            return MidiEvent("knob", ts, cc=msg.control, value=msg.value, channel=msg.channel)
        if msg.type == "pitchwheel":
            return MidiEvent("pitch_bend", ts, pitch=msg.pitch, channel=msg.channel)
        return None
