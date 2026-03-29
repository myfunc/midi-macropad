"""Config-driven event mapper with mode management."""
import toml
from pathlib import Path
from dataclasses import dataclass, field

PAD_NOTE_MIN = 16
PAD_NOTE_MAX = 23
JOYSTICK_CC = 16

@dataclass
class ActionDef:
    type: str
    keys: str = ""
    target: str = ""
    command: str = ""
    process: str = ""

@dataclass
class PadMapping:
    note: int
    label: str
    action: ActionDef

@dataclass
class KnobMapping:
    cc: int
    label: str
    action: ActionDef

@dataclass 
class Mode:
    name: str
    color: str
    icon: str = ""
    pads: list[PadMapping] = field(default_factory=list)

@dataclass
class ContextRule:
    process: str
    mode: str

@dataclass
class AppConfig:
    device_name: str = "MPK mini play"
    modes: list[Mode] = field(default_factory=list)
    knobs: list[KnobMapping] = field(default_factory=list)
    contexts: list[ContextRule] = field(default_factory=list)


def load_config(path: str | Path) -> AppConfig:
    """Load config from TOML file."""
    data = toml.load(str(path))
    
    cfg = AppConfig()
    if "device" in data:
        cfg.device_name = data["device"].get("name", cfg.device_name)

    for mode_data in data.get("modes", []):
        mode = Mode(
            name=mode_data["name"],
            color=mode_data.get("color", "#888888"),
            icon=mode_data.get("icon", ""),
        )
        for pad_data in mode_data.get("pads", []):
            act = pad_data["action"]
            mode.pads.append(PadMapping(
                note=pad_data["note"],
                label=pad_data["label"],
                action=ActionDef(
                    type=act["type"],
                    keys=act.get("keys", ""),
                    target=act.get("target", ""),
                    command=act.get("command", ""),
                    process=act.get("process", ""),
                ),
            ))
        cfg.modes.append(mode)
    
    for knob_data in data.get("knobs", []):
        act = knob_data["action"]
        cfg.knobs.append(KnobMapping(
            cc=knob_data["cc"],
            label=knob_data["label"],
            action=ActionDef(
                type=act["type"],
                keys=act.get("keys", ""),
                target=act.get("target", ""),
                command=act.get("command", ""),
                process=act.get("process", ""),
            ),
        ))
    
    mode_names_loaded = {m["name"].lower() for m in data.get("modes", [])}
    for ctx_data in data.get("contexts", []):
        target = ctx_data.get("mode", "")
        if target.lower() not in mode_names_loaded:
            continue
        cfg.contexts.append(
            ContextRule(process=ctx_data["process"], mode=target)
        )

    return cfg


class Mapper:
    """Maps MIDI events to actions based on current mode."""
    
    def __init__(self, config: AppConfig):
        self.config = config
        self.current_mode_index = 0
        self._pad_maps = []
        self._rebuild_pad_maps()
    
    def _rebuild_pad_maps(self):
        self._pad_maps = []
        for mode in self.config.modes:
            by_note = {p.note: p for p in mode.pads}
            self._pad_maps.append(by_note)
    
    @property
    def current_mode(self) -> Mode:
        if not self.config.modes:
            return Mode(name="Default", color="#888888", icon="DEF")
        return self.config.modes[self.current_mode_index]
    
    @property
    def mode_count(self) -> int:
        return len(self.config.modes)
    
    def set_mode(self, index: int):
        if not self.config.modes:
            self.current_mode_index = 0
            return
        self.current_mode_index = max(0, min(int(index), len(self.config.modes) - 1))
    
    def set_mode_by_name(self, name: str) -> bool:
        for i, mode in enumerate(self.config.modes):
            if mode.name.lower() == name.lower():
                self.current_mode_index = i
                return True
        return False

    def lookup_pad(self, note: int) -> PadMapping | None:
        if not self._pad_maps:
            return None
        return self._pad_maps[self.current_mode_index].get(note)
    
    def lookup_knob(self, cc: int) -> KnobMapping | None:
        for knob in self.config.knobs:
            if knob.cc == cc:
                return knob
        return None
