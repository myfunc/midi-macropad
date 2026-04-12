"""Config-driven event mapper with pad preset management."""
import toml
from pathlib import Path
from dataclasses import dataclass, field

from pad_registry import PadRegistry, PadEntry

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
    params: dict = field(default_factory=dict)


@dataclass
class PadMapping:
    note: int
    label: str
    action: ActionDef
    hotkey: str = ""


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
        params=dict(act.get("params", {}) or {}),
    )


def _parse_pad(pad_data: dict) -> PadMapping:
    return PadMapping(
        note=pad_data["note"],
        label=pad_data.get("label", f"Pad {pad_data['note']}"),
        action=_parse_action(pad_data.get("action", {"type": "keystroke"})),
        hotkey=pad_data.get("hotkey", ""),
    )


def load_config(path: str | Path) -> AppConfig:
    """Load config from TOML file."""
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

    if not cfg.pad_presets:
        cfg.pad_presets.append(PadPreset(name="Default"))

    return cfg


def _action_to_dict(action: ActionDef) -> dict:
    """Convert an ActionDef to a TOML-serializable dict."""
    d: dict = {"type": action.type}
    if action.keys:
        d["keys"] = action.keys
    if action.target:
        d["target"] = action.target
    if action.command:
        d["command"] = action.command
    if action.process:
        d["process"] = action.process
    if action.params:
        d["params"] = dict(action.params)
    return d


def save_config(config: AppConfig, path: str | Path) -> None:
    """Write the full AppConfig back to a TOML file."""
    p = Path(path)
    data: dict = {}

    # Device section
    data["device"] = {"name": config.device_name}

    # Knobs
    knobs_list = []
    for k in config.knobs:
        knobs_list.append({
            "cc": k.cc,
            "label": k.label,
            "action": _action_to_dict(k.action),
        })
    if knobs_list:
        data["knobs"] = knobs_list

    # Pad presets
    presets_list = []
    for preset in config.pad_presets:
        preset_dict: dict = {"name": preset.name}
        pads_list = []
        for pad in preset.pads:
            pad_dict: dict = {
                "note": pad.note,
                "label": pad.label,
                "action": _action_to_dict(pad.action),
            }
            if pad.hotkey:
                pad_dict["hotkey"] = pad.hotkey
            pads_list.append(pad_dict)
        if pads_list:
            preset_dict["pads"] = pads_list
        presets_list.append(preset_dict)
    if presets_list:
        data["pad_presets"] = presets_list

    with open(str(p), "w", encoding="utf-8") as f:
        toml.dump(data, f)


class Mapper:
    """Maps MIDI events to actions based on current pad preset."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.current_preset_index = 0
        self._pad_maps: list[dict[int, PadMapping]] = []
        self._rebuild_pad_maps()
        self.registry = PadRegistry()
        self._sync_registry()

    def _rebuild_pad_maps(self):
        self._pad_maps = []
        for preset in self.config.pad_presets:
            by_note = {p.note: p for p in preset.pads}
            self._pad_maps.append(by_note)

    def _sync_registry(self) -> None:
        """Sync current preset pads to PadRegistry."""
        preset = self.current_preset
        if preset:
            self.registry.load_from_preset(preset.pads)

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
        self._sync_registry()

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

    def update_pad(self, note: int, label: str, action_dict: dict, hotkey: str = ""):
        """Update a single pad in-place for the current preset (no full reload)."""
        action = _parse_action(action_dict)
        mapping = PadMapping(note=note, label=label, action=action, hotkey=hotkey)
        idx = min(self.current_preset_index, len(self._pad_maps) - 1)
        self._pad_maps[idx][note] = mapping
        # Also update the config preset's pad list
        preset = self.config.pad_presets[idx]
        for i, p in enumerate(preset.pads):
            if p.note == note:
                preset.pads[i] = mapping
                break
        else:
            preset.pads.append(mapping)
        # Sync to registry
        action_data = {}
        if action.keys: action_data['keys'] = action.keys
        if action.process: action_data['process'] = action.process
        if action.command: action_data['command'] = action.command
        if action.target: action_data['target'] = action.target
        self.registry.set_pad(note, PadEntry(
            note=note, label=label, source="config",
            action_type=action.type, action_data=action_data,
            hotkey=hotkey,
        ))

    def swap_pads(self, note_a: int, note_b: int):
        """Swap two pads in-place for the current preset (no full reload)."""
        idx = min(self.current_preset_index, len(self._pad_maps) - 1)
        pad_map = self._pad_maps[idx]
        a = pad_map.get(note_a)
        b = pad_map.get(note_b)
        if a and b:
            new_a = PadMapping(note=note_a, label=b.label, action=b.action, hotkey=b.hotkey)
            new_b = PadMapping(note=note_b, label=a.label, action=a.action, hotkey=a.hotkey)
        elif a:
            new_a = PadMapping(note=note_a, label=f"Pad {note_a - 15}",
                               action=ActionDef(type="keystroke"))
            new_b = PadMapping(note=note_b, label=a.label, action=a.action, hotkey=a.hotkey)
        elif b:
            new_a = PadMapping(note=note_a, label=b.label, action=b.action, hotkey=b.hotkey)
            new_b = PadMapping(note=note_b, label=f"Pad {note_b - 15}",
                               action=ActionDef(type="keystroke"))
        else:
            return
        pad_map[note_a] = new_a
        pad_map[note_b] = new_b
        # Update config preset pad list
        preset = self.config.pad_presets[idx]
        found_a = found_b = False
        for i, p in enumerate(preset.pads):
            if p.note == note_a:
                preset.pads[i] = new_a
                found_a = True
            elif p.note == note_b:
                preset.pads[i] = new_b
                found_b = True
        if not found_a:
            preset.pads.append(new_a)
        if not found_b:
            preset.pads.append(new_b)
        self._sync_registry()

    def get_preset_by_name(self, name: str) -> PadPreset | None:
        """Find a preset by name (case-insensitive). Returns None if not found."""
        for preset in self.config.pad_presets:
            if preset.name.lower() == name.lower():
                return preset
        return None

    def get_preset_index_by_name(self, name: str) -> int | None:
        """Find a preset index by name (case-insensitive)."""
        for i, preset in enumerate(self.config.pad_presets):
            if preset.name.lower() == name.lower():
                return i
        return None

    def rebuild_maps(self) -> None:
        """Public wrapper for _rebuild_pad_maps."""
        self._rebuild_pad_maps()

    def get_current_preset_index(self) -> int:
        """Return the current preset index."""
        return self.current_preset_index

    def set_current_preset_index(self, value: int) -> None:
        """Set the current preset index (clamped to valid range)."""
        if self.config.pad_presets:
            self.current_preset_index = max(0, min(value, len(self.config.pad_presets) - 1))
        else:
            self.current_preset_index = 0

    def apply_partial_preset(self, preset_name: str, note_filter: list[int]) -> bool:
        """Apply pads from a named preset, but only for notes in note_filter.

        This allows switching a single bank (e.g. bankA = notes 16-23) to a
        different preset without touching the other bank.
        Returns True on success, False if preset not found.
        """
        preset = self.get_preset_by_name(preset_name)
        if not preset:
            return False
        # Build a map of note -> PadMapping for the target preset
        target_pads = {p.note: p for p in preset.pads if p.note in note_filter}
        # Update the current preset's pad map for filtered notes
        idx = min(self.current_preset_index, len(self._pad_maps) - 1)
        pad_map = self._pad_maps[idx]
        for note in note_filter:
            if note in target_pads:
                pad_map[note] = target_pads[note]
            else:
                # Clear pad if target preset has no mapping for this note
                pad_map[note] = PadMapping(
                    note=note,
                    label=f"Pad {note - 15}",
                    action=ActionDef(type=""),
                )
        # Sync filtered pads to registry
        filter_set = set(note_filter)
        filtered_mappings = [pad_map[n] for n in note_filter if n in pad_map]
        self.registry.load_from_preset(filtered_mappings)
        return True

    def update_knob_action(
        self,
        cc: int,
        action_type: str,
        target: str,
        label: str,
        params: dict,
        config_path: str | Path = "config.toml",
    ) -> bool:
        """Update a knob action in config.toml and reload the mapper config.

        Finds the [[knobs]] entry with matching cc, updates its action and label,
        writes back to config.toml, and reloads the in-memory config.
        Returns True on success, False if cc not found.
        """
        path = Path(config_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent / path
        data = toml.load(str(path))
        knobs_list = data.get("knobs", [])
        found = False
        for knob_data in knobs_list:
            if knob_data.get("cc") == cc:
                knob_data["label"] = label
                knob_data["action"] = {"type": action_type, "target": target}
                if params:
                    knob_data["action"]["params"] = params
                found = True
                break
        if not found:
            return False
        with open(str(path), "w", encoding="utf-8") as f:
            toml.dump(data, f)
        # Reload in-memory config
        new_cfg = load_config(path)
        self.config.knobs = new_cfg.knobs
        return True

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
