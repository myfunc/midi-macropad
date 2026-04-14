"""Central pad registry — single source of truth for all pad assignments."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

PAD_NOTES_BANK_A = [16, 17, 18, 19, 20, 21, 22, 23]
PAD_NOTES_BANK_B = [24, 25, 26, 27, 28, 29, 30, 31]
PAD_NOTES_ALL = PAD_NOTES_BANK_A + PAD_NOTES_BANK_B


def pad_key(preset: str, note: int) -> str:
    """Composite key for addressing a pad within a specific preset."""
    return f"{preset}:{note}"


@dataclass
class PadEntry:
    """Unified pad assignment."""
    note: int
    preset: str = ""
    label: str = ""
    source: str = "config"           # "config" | "plugin:OBS" | "plugin:Voicemeeter" | ...
    action_type: str = ""            # "keystroke" | "app_keystroke" | "shell" | "launch" | "obs" | "volume" | "scroll" | "plugin"
    action_data: dict = field(default_factory=dict)  # type-specific params: keys, process, command, target
    hotkey: str = ""
    locked: bool = False             # True = plugin-owned, not editable by user
    color: tuple[int, int, int] = (100, 100, 100)

    @property
    def is_plugin(self) -> bool:
        return self.source.startswith("plugin:")

    @property
    def plugin_name(self) -> str:
        if self.source.startswith("plugin:"):
            return self.source[7:]
        return ""

    @property
    def display_label(self) -> str:
        return self.label or f"Pad {self.note}"


class PadRegistry:
    """Central registry for all pad assignments across banks and presets.

    Keys are composite ``"PresetName:note"`` strings so pads from different
    presets coexist without collision.

    Usage:
        registry = PadRegistry()
        registry.load_from_preset(preset_pads, "Spotify")
        registry.set_pad("Spotify", 16, PadEntry(...))
        registry.on_change(callback)
    """

    def __init__(self):
        self._pads: dict[str, PadEntry] = {}
        self._change_callbacks: list[Callable[[str, PadEntry | None], None]] = []

    # -- Core access --------------------------------------------------

    def get_pad(self, preset: str | None, note: int) -> PadEntry | None:
        """Get pad entry by preset + note.

        If *preset* is ``None``, fall back to legacy integer-key lookup
        (searches all presets, returns first match — for backward compat).
        """
        if preset is not None:
            return self._pads.get(pad_key(preset, note))
        # Legacy: find first entry with this note
        for key, entry in self._pads.items():
            if entry.note == note:
                return entry
        return None

    def set_pad(self, preset: str, note: int, entry: PadEntry) -> None:
        """Set or update a pad entry. Triggers change callbacks."""
        entry.note = note
        entry.preset = preset
        key = pad_key(preset, note)
        self._pads[key] = entry
        self._notify(key, entry)

    def clear_pad(self, preset: str, note: int) -> None:
        """Reset a pad to empty state."""
        entry = PadEntry(note=note, preset=preset)
        key = pad_key(preset, note)
        self._pads[key] = entry
        self._notify(key, entry)

    def swap_pads(self, preset: str, note_a: int, note_b: int) -> bool:
        """Swap two pads within the same preset. Returns False if either is locked."""
        key_a = pad_key(preset, note_a)
        key_b = pad_key(preset, note_b)
        pad_a = self._pads.get(key_a)
        pad_b = self._pads.get(key_b)
        if not pad_a or not pad_b:
            return False
        if pad_a.locked or pad_b.locked:
            return False
        # Swap entries but keep notes correct
        pad_a.note, pad_b.note = note_b, note_a
        self._pads[key_a] = pad_b
        self._pads[key_b] = pad_a
        self._notify(key_a, pad_b)
        self._notify(key_b, pad_a)
        return True

    # -- Preset-level queries -----------------------------------------

    def get_preset_pads(self, preset_name: str) -> dict[int, PadEntry]:
        """Get all pad entries for a given preset, keyed by note."""
        prefix = f"{preset_name}:"
        result: dict[int, PadEntry] = {}
        for key, entry in self._pads.items():
            if key.startswith(prefix):
                result[entry.note] = entry
        return result

    def get_bank(self, preset: str, bank: str) -> list[PadEntry]:
        """Get all pad entries for a bank ('A' or 'B') within a preset."""
        notes = PAD_NOTES_BANK_A if bank == "A" else PAD_NOTES_BANK_B
        result = []
        for n in notes:
            entry = self._pads.get(pad_key(preset, n))
            if entry:
                result.append(entry)
        return result

    def get_all(self) -> dict[str, PadEntry]:
        """Get all pad entries keyed by composite key."""
        return dict(self._pads)

    def get_labels(self) -> dict[str, str]:
        """Get label map for all non-empty pads."""
        return {
            key: entry.display_label
            for key, entry in self._pads.items()
            if entry.label or entry.action_type
        }

    def get_all_hotkeys(self) -> list[tuple[str, int, str]]:
        """Return list of (preset_name, note, hotkey_spec) for all pads with hotkeys."""
        result = []
        for key, entry in self._pads.items():
            if entry.hotkey:
                result.append((entry.preset, entry.note, entry.hotkey))
        return result

    # -- Plugin pad management ----------------------------------------

    def get_plugin_notes(self, plugin_name: str) -> set[int]:
        """Get notes assigned to a specific plugin (across all presets)."""
        return {
            entry.note for entry in self._pads.values()
            if entry.plugin_name.lower() == plugin_name.lower()
        }

    def get_locked_notes(self) -> set[int]:
        """Get all locked (plugin-owned) notes."""
        return {entry.note for entry in self._pads.values() if entry.locked}

    # -- Loading from config ------------------------------------------

    def load_from_preset(self, pad_mappings: list, preset_name: str = "") -> None:
        """Load pad assignments from a config preset (list of PadMapping).

        Only overwrites non-locked pads. Loads under ``preset_name:N`` keys
        without touching other presets.
        """
        for mapping in pad_mappings:
            note = mapping.note
            key = pad_key(preset_name, note)
            existing = self._pads.get(key)
            if existing and existing.locked:
                continue  # Don't overwrite plugin-owned pads
            action = mapping.action
            action_data = {}
            if hasattr(action, 'keys') and action.keys:
                action_data['keys'] = action.keys
            if hasattr(action, 'process') and action.process:
                action_data['process'] = action.process
            if hasattr(action, 'command') and action.command:
                action_data['command'] = action.command
            if hasattr(action, 'target') and action.target:
                action_data['target'] = action.target
            entry = PadEntry(
                note=note,
                preset=preset_name,
                label=mapping.label,
                source="config",
                action_type=action.type if action else "",
                action_data=action_data,
                hotkey=getattr(mapping, 'hotkey', '') or '',
                locked=False,
            )
            self._pads[key] = entry
            self._notify(key, entry)

    def load_active_and_hotkey_pads(
        self,
        active_presets: dict[str, str],
        all_presets: list,
    ) -> None:
        """Load active MIDI pads + pads with hotkeys from all presets.

        Args:
            active_presets: ``{"bankA": "Spotify", "bankB": "OBS"}`` — panel->preset routing.
            all_presets: Full list of PadPreset objects from config.
        """
        # First load ALL presets (so hotkeys from inactive presets work)
        for preset in all_presets:
            self.load_from_preset(preset.pads, preset.name)

    # -- Plugin registration ------------------------------------------

    def clear_plugin_pads(self, plugin_name: str | None = None) -> None:
        """Clear all pads from a specific plugin, or all plugin pads if None."""
        for key in list(self._pads.keys()):
            entry = self._pads[key]
            if not entry.is_plugin:
                continue
            if plugin_name and entry.plugin_name.lower() != plugin_name.lower():
                continue
            new_entry = PadEntry(note=entry.note, preset=entry.preset)
            self._pads[key] = new_entry
            self._notify(key, new_entry)

    def register_plugin_pads(
        self,
        plugin_name: str,
        notes: set[int],
        actions: list[dict],
        locked: bool = True,
        preset: str = "",
    ) -> None:
        """Register pads for a plugin from its action catalog.

        Args:
            plugin_name: Name of the plugin (e.g., "OBS", "Voicemeeter")
            notes: Set of MIDI notes assigned to this plugin
            actions: List of action dicts with 'id', 'label', 'color', 'desc'
            locked: Whether these pads should be locked from user editing
            preset: Preset name to register under
        """
        sorted_notes = sorted(notes)
        for i, note in enumerate(sorted_notes):
            if i < len(actions):
                action = actions[i]
                entry = PadEntry(
                    note=note,
                    preset=preset,
                    label=action.get("label", ""),
                    source=f"plugin:{plugin_name}",
                    action_type="plugin",
                    action_data={"target": f"{plugin_name}:{action['id']}"},
                    locked=locked,
                    color=action.get("color", (100, 100, 100)),
                )
            else:
                entry = PadEntry(
                    note=note,
                    preset=preset,
                    label="",
                    source=f"plugin:{plugin_name}",
                    action_type="plugin",
                    action_data={"target": plugin_name},
                    locked=locked,
                )
            key = pad_key(preset, note)
            self._pads[key] = entry
            self._notify(key, entry)

    # -- Change notification ------------------------------------------

    def on_change(self, callback: Callable[[str, PadEntry | None], None]) -> None:
        """Register a callback for pad changes. Called with (composite_key, entry)."""
        if callback not in self._change_callbacks:
            self._change_callbacks.append(callback)

    def off_change(self, callback: Callable) -> None:
        """Unregister a change callback."""
        try:
            self._change_callbacks.remove(callback)
        except ValueError:
            pass

    def _notify(self, key: str, entry: PadEntry | None) -> None:
        for cb in self._change_callbacks:
            try:
                cb(key, entry)
            except Exception:
                pass

    # -- Export --------------------------------------------------------

    def to_config_dict(self, preset_name: str, notes: list[int] | None = None) -> list[dict]:
        """Export non-plugin pad entries for a preset as config.toml format dicts."""
        result = []
        preset_pads = self.get_preset_pads(preset_name)
        for note in (notes or sorted(preset_pads.keys())):
            entry = preset_pads.get(note)
            if not entry or entry.is_plugin or not entry.action_type:
                continue
            pad_dict: dict[str, Any] = {
                "note": note,
                "label": entry.label,
                "action": {"type": entry.action_type, **entry.action_data},
            }
            if entry.hotkey:
                pad_dict["hotkey"] = entry.hotkey
            result.append(pad_dict)
        return result
