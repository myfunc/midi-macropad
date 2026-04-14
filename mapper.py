"""Config-driven event mapper with pad preset management."""
import os
import tempfile
import toml
from pathlib import Path
from dataclasses import dataclass, field

from pad_registry import PadRegistry, PadEntry, pad_key

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
class KnobPreset:
    name: str
    knobs: list[KnobMapping] = field(default_factory=list)


# --- Piano presets (Map bank for piano panel) -----------------------
# Piano key range accepted by the map dispatch; anything outside this range
# falls through to the regular pad dispatch or is ignored.
PIANO_MAP_NOTE_MIN = 36
PIANO_MAP_NOTE_MAX = 72


@dataclass
class PianoKeyMapping:
    """Mapping for a single piano key in a preset."""
    note: int
    label: str | None = None
    action: dict | None = None  # same shape as pad action_dict


@dataclass
class PianoPreset:
    name: str
    keys: list[PianoKeyMapping] = field(default_factory=list)


@dataclass
class AppConfig:
    device_name: str = "MPK mini play"
    pad_presets: list[PadPreset] = field(default_factory=list)
    knob_presets: list[KnobPreset] = field(default_factory=list)
    knobs: list[KnobMapping] = field(default_factory=list)
    piano_presets: list[PianoPreset] = field(default_factory=list)


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

    for kp_data in data.get("knob_presets", []):
        kp = KnobPreset(name=kp_data["name"])
        for knob_data in kp_data.get("knobs", []):
            kp.knobs.append(KnobMapping(
                cc=knob_data["cc"],
                label=knob_data.get("label", f"CC{knob_data['cc']}"),
                action=_parse_action(knob_data.get("action", {"type": "volume"})),
            ))
        cfg.knob_presets.append(kp)

    if "pad_presets" in data:
        for preset_data in data["pad_presets"]:
            preset = PadPreset(name=preset_data["name"])
            for pad_data in preset_data.get("pads", []):
                preset.pads.append(_parse_pad(pad_data))
            cfg.pad_presets.append(preset)

    if not cfg.pad_presets:
        cfg.pad_presets.append(PadPreset(name="Default"))

    # Piano presets (optional)
    for pp_data in data.get("piano_presets", []) or []:
        pp = PianoPreset(name=pp_data.get("name", "Preset"))
        for key_data in pp_data.get("keys", []) or []:
            note_v = key_data.get("note")
            if not isinstance(note_v, int):
                continue
            label = key_data.get("label")
            action = key_data.get("action")
            if action is not None and not isinstance(action, dict):
                action = None
            pp.keys.append(PianoKeyMapping(
                note=int(note_v),
                label=label if isinstance(label, str) else None,
                action=dict(action) if isinstance(action, dict) else None,
            ))
        cfg.piano_presets.append(pp)

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

    # Knob presets
    kp_list = []
    for kp in config.knob_presets:
        kp_dict: dict = {"name": kp.name}
        kp_knobs = []
        for k in kp.knobs:
            kp_knobs.append({
                "cc": k.cc,
                "label": k.label,
                "action": _action_to_dict(k.action),
            })
        if kp_knobs:
            kp_dict["knobs"] = kp_knobs
        kp_list.append(kp_dict)
    if kp_list:
        data["knob_presets"] = kp_list

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

    # Piano presets
    pp_list: list[dict] = []
    for pp in config.piano_presets:
        pp_dict: dict = {"name": pp.name}
        keys_list: list[dict] = []
        for k in pp.keys:
            key_dict: dict = {"note": k.note}
            if k.label:
                key_dict["label"] = k.label
            if k.action:
                key_dict["action"] = dict(k.action)
            keys_list.append(key_dict)
        if keys_list:
            pp_dict["keys"] = keys_list
        pp_list.append(pp_dict)
    if pp_list:
        data["piano_presets"] = pp_list

    # Atomic write: serialize to a sibling temp file, fsync, then os.replace.
    # Prevents torn writes (and loss of all presets) if two writers race or
    # if the process is killed mid-write.
    p_parent = p.parent if p.parent.as_posix() else Path(".")
    p_parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=p.name + ".", suffix=".tmp", dir=str(p_parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            toml.dump(data, f)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                # fsync may fail on some filesystems (e.g. network mounts);
                # the os.replace below is still the atomic commit point.
                pass
        os.replace(tmp_path, str(p))
    except Exception:
        # Clean up the temp file on failure so we don't leak debris.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class Mapper:
    """Maps MIDI events to actions based on per-panel preset routing.

    Instead of a single ``current_preset_index``, the mapper maintains a
    routing table ``_active_midi_presets`` that maps panel IDs to preset
    names (e.g. ``{"bankA": "Spotify", "bankB": "OBS"}``).
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.current_preset_index = 0  # kept for backward compat / legacy code
        self._pad_maps: list[dict[int, PadMapping]] = []
        self._active_midi_presets: dict[str, str] = {}  # panel_id -> preset_name (legacy)
        # Per-panel knob routing: {"knobBank-A": preset_name, ...} (legacy)
        self._active_knob_presets: dict[str, str] = {}

        # Freeform panel model (new):
        # _panel_presets: instanceId -> preset_name (for ALL registered panels)
        # _active_panels:  (type, bank) -> instanceId  (exclusive active slot)
        self._panel_presets: dict[str, str] = {}
        self._active_panels: dict[tuple[str, str], str] = {}

        self._rebuild_pad_maps()
        self.registry = PadRegistry()
        self._sync_all_to_registry()

    def _rebuild_pad_maps(self):
        self._pad_maps = []
        for preset in self.config.pad_presets:
            by_note = {p.note: p for p in preset.pads}
            self._pad_maps.append(by_note)

    def _sync_all_to_registry(self) -> None:
        """Load ALL presets into the registry under composite keys."""
        for preset in self.config.pad_presets:
            self.registry.load_from_preset(preset.pads, preset.name)

    def _sync_registry(self) -> None:
        """Sync current preset pads to PadRegistry (legacy helper)."""
        self._sync_all_to_registry()

    # -- Routing table ------------------------------------------------

    def set_midi_routing(self, panel_id: str, preset_name: str) -> bool:
        """Set which preset is active for a given panel.

        Does NOT mutate pad_maps — just updates the routing lookup.
        Returns False if preset not found.
        """
        preset = self.get_preset_by_name(preset_name)
        if not preset:
            return False
        self._active_midi_presets[panel_id] = preset_name
        return True

    def get_midi_routing(self) -> dict[str, str]:
        """Return current panel -> preset routing table."""
        return dict(self._active_midi_presets)

    # -- Pad lookup ---------------------------------------------------

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
        self._sync_all_to_registry()

    def set_preset_by_name(self, name: str) -> bool:
        for i, preset in enumerate(self.config.pad_presets):
            if preset.name.lower() == name.lower():
                self.current_preset_index = i
                return True
        return False

    def lookup_pad(self, note: int) -> PadMapping | None:
        """Look up a pad by MIDI note using the routing table.

        Determines bank from note range, finds the routed preset for that
        bank, then returns the PadMapping from that preset.
        """
        # Determine which panel this note belongs to
        if note in PAD_NOTES_BANK_A:
            panel_id = "bankA"
        elif note in PAD_NOTES_BANK_B:
            panel_id = "bankB"
        else:
            # Note outside pad range — not a pad
            return None

        preset_name = self._active_midi_presets.get(panel_id)
        if preset_name:
            return self.get_pad_from_preset(preset_name, note)

        # Fallback: use current_preset_index (legacy)
        if not self._pad_maps:
            return None
        idx = min(self.current_preset_index, len(self._pad_maps) - 1)
        return self._pad_maps[idx].get(note)

    def get_pad_from_preset(self, preset_name: str, note: int) -> PadMapping | None:
        """Get a PadMapping from a specific preset by name."""
        preset = self.get_preset_by_name(preset_name)
        if not preset:
            return None
        for pad in preset.pads:
            if pad.note == note:
                return pad
        return None

    def lookup_knob(self, cc: int) -> KnobMapping | None:
        for knob in self.config.knobs:
            if knob.cc == cc:
                return knob
        return None

    # -- Knob routing per panel ---------------------------------------

    def _find_knob_preset(self, name: str) -> KnobPreset | None:
        for kp in self.config.knob_presets:
            if kp.name.lower() == name.lower():
                return kp
        return None

    def set_knob_routing(self, panel_id: str, preset_name: str) -> bool:
        """Set which knob preset is active for a given knob panel.

        Does NOT mutate config.knobs; stores routing only. Returns False if
        the knob preset is not found.
        """
        kp = self._find_knob_preset(preset_name)
        if kp is None:
            return False
        self._active_knob_presets[panel_id] = preset_name
        return True

    def get_knob_routing(self) -> dict[str, str]:
        """Return current knob panel -> preset routing table."""
        return dict(self._active_knob_presets)

    def lookup_knob_for_panel(self, panel_id: str, cc: int) -> KnobMapping | None:
        """Resolve a KnobMapping for a panel using its routed knob preset.

        Falls back to legacy ``config.knobs`` if the panel has no routing.
        """
        preset_name = self._active_knob_presets.get(panel_id)
        if preset_name:
            kp = self._find_knob_preset(preset_name)
            if kp is not None:
                for knob in kp.knobs:
                    if knob.cc == cc:
                        return knob
                return None
        # Fallback: legacy global knobs
        return self.lookup_knob(cc)

    # -- Freeform panel model -----------------------------------------

    def register_panel_preset(self, instance_id: str, preset_name: str) -> bool:
        """Store preset binding for a panel instance (any id, any preset)."""
        if not instance_id or not preset_name:
            return False
        self._panel_presets[instance_id] = preset_name
        return True

    def unregister_panel(self, instance_id: str) -> None:
        """Remove a panel instance from tracking; also clear active slot."""
        self._panel_presets.pop(instance_id, None)
        for key, pid in list(self._active_panels.items()):
            if pid == instance_id:
                self._active_panels.pop(key, None)

    def set_active_panel(
        self, panel_type: str, bank: str, instance_id: str | None,
    ) -> str | None:
        """Activate a panel for (type, bank). Returns previously-active id.

        Passing instance_id=None clears the slot.
        """
        key = (panel_type, bank)
        prev = self._active_panels.get(key)
        if instance_id is None:
            self._active_panels.pop(key, None)
        else:
            self._active_panels[key] = instance_id
        return prev

    def get_active_panel(self, panel_type: str, bank: str) -> str | None:
        return self._active_panels.get((panel_type, bank))

    def get_panel_preset(self, instance_id: str) -> str | None:
        return self._panel_presets.get(instance_id)

    def get_all_panel_presets(self) -> dict[str, str]:
        return dict(self._panel_presets)

    def get_all_active_panels(self) -> dict[str, str]:
        """Return {"pad:A": instanceId, ...} for JSON serialization."""
        return {f"{t}:{b}": pid for (t, b), pid in self._active_panels.items()}

    def lookup_pad_for_active(self, note: int) -> PadMapping | None:
        """Resolve pad via the active pad-panel for the note's bank."""
        if note in PAD_NOTES_BANK_A:
            bank = "A"
        elif note in PAD_NOTES_BANK_B:
            bank = "B"
        else:
            return None
        pid = self._active_panels.get(("pad", bank))
        if pid is not None:
            preset_name = self._panel_presets.get(pid)
            if preset_name:
                return self.get_pad_from_preset(preset_name, note)
        # Legacy fallback
        return self.lookup_pad(note)

    def lookup_knob_for_active_bank(
        self, bank: str, cc: int,
    ) -> KnobMapping | None:
        """Resolve knob via the active knob-panel for a given bank."""
        pid = self._active_panels.get(("knob", bank))
        if pid is not None:
            preset_name = self._panel_presets.get(pid)
            if preset_name:
                kp = self._find_knob_preset(preset_name)
                if kp is not None:
                    for knob in kp.knobs:
                        if knob.cc == cc:
                            return knob
                    return None
        # Legacy fallback to per-panel routing table
        legacy_panel = "knobBank-B" if bank == "B" else "knobBank-A"
        return self.lookup_knob_for_panel(legacy_panel, cc)

    def update_pad(self, preset_name: str, note: int, label: str, action_dict: dict, hotkey: str = ""):
        """Update a single pad in a specific preset (no full reload)."""
        action = _parse_action(action_dict)
        mapping = PadMapping(note=note, label=label, action=action, hotkey=hotkey)

        # Find preset by name
        preset = self.get_preset_by_name(preset_name)
        if not preset:
            return

        # Update pad_maps
        preset_idx = self.get_preset_index_by_name(preset_name)
        if preset_idx is not None and preset_idx < len(self._pad_maps):
            self._pad_maps[preset_idx][note] = mapping

        # Update config preset's pad list
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
        self.registry.set_pad(preset_name, note, PadEntry(
            note=note, preset=preset_name, label=label, source="config",
            action_type=action.type, action_data=action_data,
            hotkey=hotkey,
        ))

    def swap_pads(self, preset_name: str, note_a: int, note_b: int):
        """Swap two pads in-place for a specific preset."""
        preset = self.get_preset_by_name(preset_name)
        if not preset:
            return
        preset_idx = self.get_preset_index_by_name(preset_name)
        if preset_idx is None or preset_idx >= len(self._pad_maps):
            return
        pad_map = self._pad_maps[preset_idx]
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
        self._sync_all_to_registry()

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
        self._sync_all_to_registry()

    def get_current_preset_index(self) -> int:
        """Return the current preset index."""
        return self.current_preset_index

    def set_current_preset_index(self, value: int) -> None:
        """Set the current preset index (clamped to valid range)."""
        if self.config.pad_presets:
            self.current_preset_index = max(0, min(value, len(self.config.pad_presets) - 1))
        else:
            self.current_preset_index = 0

    def update_knob_action(
        self,
        cc: int,
        action_type: str,
        target: str,
        label: str,
        params: dict,
        config_path: str | Path = "config.toml",
    ) -> bool:
        """Update a knob action in config.toml and reload the mapper config."""
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
        # Atomic write (same pattern as save_config) to avoid corrupting
        # config.toml on concurrent writers.
        p_parent = path.parent if path.parent.as_posix() else Path(".")
        p_parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(p_parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                toml.dump(data, f)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp_path, str(path))
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        # Reload in-memory config
        new_cfg = load_config(path)
        self.config.knobs = new_cfg.knobs
        return True

    def swap_knobs(self, cc_a: int, cc_b: int) -> bool:
        """Swap labels and actions between two knobs in config.knobs."""
        knob_a = knob_b = None
        idx_a = idx_b = -1
        for i, k in enumerate(self.config.knobs):
            if k.cc == cc_a:
                knob_a = k
                idx_a = i
            elif k.cc == cc_b:
                knob_b = k
                idx_b = i
        if knob_a is None or knob_b is None:
            return False
        new_a = KnobMapping(cc=cc_a, label=knob_b.label, action=knob_b.action)
        new_b = KnobMapping(cc=cc_b, label=knob_a.label, action=knob_a.action)
        self.config.knobs[idx_a] = new_a
        self.config.knobs[idx_b] = new_b
        return True

    def apply_knob_preset(self, preset_name: str) -> bool:
        """Replace self.config.knobs with knobs from the named knob preset."""
        for kp in self.config.knob_presets:
            if kp.name.lower() == preset_name.lower():
                self.config.knobs = list(kp.knobs)
                return True
        return False

    # -- Piano presets ------------------------------------------------

    def get_piano_preset(self, name: str) -> PianoPreset | None:
        """Find a piano preset by name (case-insensitive)."""
        for pp in self.config.piano_presets:
            if pp.name.lower() == name.lower():
                return pp
        return None

    def lookup_piano_key(
        self, preset_name: str, note: int,
    ) -> PianoKeyMapping | None:
        """Return the PianoKeyMapping for a note in a preset, if any."""
        pp = self.get_piano_preset(preset_name)
        if pp is None:
            return None
        for k in pp.keys:
            if k.note == note:
                return k
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
