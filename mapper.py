"""Config-driven event mapper with pad preset management."""
import toml
from pathlib import Path
from dataclasses import dataclass, field

PAD_NOTES_BANK_A = [16, 17, 18, 19, 20, 21, 22, 23]
PAD_NOTES_BANK_B = [24, 25, 26, 27, 28, 29, 30, 31]
PAD_NOTES_ALL = PAD_NOTES_BANK_A + PAD_NOTES_BANK_B
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
class PadPreset:
    name: str
    pads: list[PadMapping] = field(default_factory=list)


@dataclass
class AppConfig:
    device_name: str = "MPK mini play"
    pad_presets: list[PadPreset] = field(default_factory=list)
    knobs: list[KnobMapping] = field(default_factory=list)


def _parse_action(act: dict) -> ActionDef:
    return ActionDef(
        type=act.get("type", "keystroke"),
        keys=act.get("keys", ""),
        target=act.get("target", ""),
        command=act.get("command", ""),
        process=act.get("process", ""),
    )


def _parse_pad(pad_data: dict) -> PadMapping:
    return PadMapping(
        note=pad_data["note"],
        label=pad_data.get("label", f"Pad {pad_data['note']}"),
        action=_parse_action(pad_data.get("action", {"type": "keystroke"})),
    )


def load_config(path: str | Path) -> AppConfig:
    """Load config from TOML file. Supports both new (pad_presets) and legacy (modes) format."""
    data = toml.load(str(path))

    cfg = AppConfig()
    if "device" in data:
        cfg.device_name = data["device"].get("name", cfg.device_name)

    for knob_data in data.get("knobs", []):
        cfg.knobs.append(KnobMapping(
            cc=knob_data["cc"],
            label=knob_data.get("label", f"CC{knob_data['cc']}"),
            action=_parse_action(knob_data.get("action", {"type": "volume"})),
        ))

    if "pad_presets" in data:
        for preset_data in data["pad_presets"]:
            preset = PadPreset(name=preset_data["name"])
            for pad_data in preset_data.get("pads", []):
                preset.pads.append(_parse_pad(pad_data))
            cfg.pad_presets.append(preset)
    elif "modes" in data:
        for mode_data in data["modes"]:
            preset = PadPreset(name=mode_data["name"])
            for pad_data in mode_data.get("pads", []):
                preset.pads.append(_parse_pad(pad_data))
            if not preset.pads:
                plugin_name = mode_data["name"]
                for note in PAD_NOTES_BANK_A:
                    preset.pads.append(PadMapping(
                        note=note,
                        label=f"Pad {note - 15}",
                        action=ActionDef(type="plugin", target=plugin_name),
                    ))
            cfg.pad_presets.append(preset)

    if not cfg.pad_presets:
        cfg.pad_presets.append(PadPreset(name="Default"))

    return cfg


class Mapper:
    """Maps MIDI events to actions based on current pad preset."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.current_preset_index = 0
        self._pad_maps: list[dict[int, PadMapping]] = []
        self._rebuild_pad_maps()

    def _rebuild_pad_maps(self):
        self._pad_maps = []
        for preset in self.config.pad_presets:
            by_note = {p.note: p for p in preset.pads}
            self._pad_maps.append(by_note)

    @property
    def current_preset(self) -> PadPreset:
        if not self.config.pad_presets:
            return PadPreset(name="Empty")
        idx = min(self.current_preset_index, len(self.config.pad_presets) - 1)
        return self.config.pad_presets[idx]

    @property
    def preset_count(self) -> int:
        return len(self.config.pad_presets)

    def set_preset(self, index: int):
        if not self.config.pad_presets:
            self.current_preset_index = 0
            return
        self.current_preset_index = max(0, min(int(index), len(self.config.pad_presets) - 1))

    def set_preset_by_name(self, name: str) -> bool:
        for i, preset in enumerate(self.config.pad_presets):
            if preset.name.lower() == name.lower():
                self.current_preset_index = i
                return True
        return False

    def lookup_pad(self, note: int) -> PadMapping | None:
        if not self._pad_maps:
            return None
        idx = min(self.current_preset_index, len(self._pad_maps) - 1)
        return self._pad_maps[idx].get(note)

    def lookup_knob(self, cc: int) -> KnobMapping | None:
        for knob in self.config.knobs:
            if knob.cc == cc:
                return knob
        return None

    def get_plugin_notes(self, plugin_name: str) -> set[int]:
        """Return notes assigned to a specific plugin in the current preset."""
        notes: set[int] = set()
        pad_map = self._pad_maps[self.current_preset_index] if self._pad_maps else {}
        low_name = plugin_name.lower()
        for note, mapping in pad_map.items():
            if mapping.action.type == "plugin":
                target_plugin = mapping.action.target.split(":")[0].lower()
                if target_plugin == low_name:
                    notes.add(note)
        return notes
