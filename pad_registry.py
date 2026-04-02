"""Central pad registry — single source of truth for all pad assignments."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

PAD_NOTES_BANK_A = [16, 17, 18, 19, 20, 21, 22, 23]
PAD_NOTES_BANK_B = [24, 25, 26, 27, 28, 29, 30, 31]
PAD_NOTES_ALL = PAD_NOTES_BANK_A + PAD_NOTES_BANK_B


@dataclass
class PadEntry:
    """Unified pad assignment."""
    note: int
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

    Usage:
        registry = PadRegistry()
        registry.load_from_preset(preset_pads)  # Load from config.toml
        registry.set_pad(note, PadEntry(...))    # Plugin claims a pad
        registry.on_change(callback)             # UI subscribes to changes
    """

    def __init__(self):
        self._pads: dict[int, PadEntry] = {}
        self._change_callbacks: list[Callable[[int, PadEntry | None], None]] = []
        # Initialize all notes with empty entries
        for note in PAD_NOTES_ALL:
            self._pads[note] = PadEntry(note=note)

    def get_pad(self, note: int) -> PadEntry | None:
        """Get pad entry for a specific note."""
        return self._pads.get(note)

    def set_pad(self, note: int, entry: PadEntry) -> None:
        """Set or update a pad entry. Triggers change callbacks."""
        entry.note = note
        self._pads[note] = entry
        self._notify(note, entry)

    def clear_pad(self, note: int) -> None:
        """Reset a pad to empty state."""
        entry = PadEntry(note=note)
        self._pads[note] = entry
        self._notify(note, entry)

    def swap_pads(self, note_a: int, note_b: int) -> bool:
        """Swap two pads. Returns False if either is locked."""
        pad_a = self._pads.get(note_a)
        pad_b = self._pads.get(note_b)
        if not pad_a or not pad_b:
            return False
        if pad_a.locked or pad_b.locked:
            return False
        # Swap entries but keep notes correct
        pad_a.note, pad_b.note = note_b, note_a
        self._pads[note_a] = pad_b
        self._pads[note_b] = pad_a
        self._notify(note_a, pad_b)
        self._notify(note_b, pad_a)
        return True

    def get_bank(self, bank: str) -> list[PadEntry]:
        """Get all pad entries for a bank ('A' or 'B')."""
        notes = PAD_NOTES_BANK_A if bank == "A" else PAD_NOTES_BANK_B
        return [self._pads[n] for n in notes if n in self._pads]

    def get_all(self) -> dict[int, PadEntry]:
        """Get all pad entries."""
        return dict(self._pads)

    def get_labels(self) -> dict[int, str]:
        """Get label map for all non-empty pads."""
        return {
            note: entry.display_label
            for note, entry in self._pads.items()
            if entry.label or entry.action_type
        }

    def get_plugin_notes(self, plugin_name: str) -> set[int]:
        """Get notes assigned to a specific plugin."""
        return {
            note for note, entry in self._pads.items()
            if entry.plugin_name.lower() == plugin_name.lower()
        }

    def get_locked_notes(self) -> set[int]:
        """Get all locked (plugin-owned) notes."""
        return {note for note, entry in self._pads.items() if entry.locked}

    def load_from_preset(self, pad_mappings: list) -> None:
        """Load pad assignments from a config preset (list of PadMapping).

        Only overwrites non-locked pads. Call clear_plugin_pads() first
        if you want plugins to re-register their pads after preset change.
        """
        for mapping in pad_mappings:
            note = mapping.note
            existing = self._pads.get(note)
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
                label=mapping.label,
                source="config",
                action_type=action.type if action else "",
                action_data=action_data,
                hotkey=getattr(mapping, 'hotkey', '') or '',
                locked=False,
            )
            self._pads[note] = entry
            self._notify(note, entry)

    def clear_plugin_pads(self, plugin_name: str | None = None) -> None:
        """Clear all pads from a specific plugin, or all plugin pads if None."""
        for note in PAD_NOTES_ALL:
            entry = self._pads.get(note)
            if not entry or not entry.is_plugin:
                continue
            if plugin_name and entry.plugin_name.lower() != plugin_name.lower():
                continue
            self._pads[note] = PadEntry(note=note)
            self._notify(note, self._pads[note])

    def register_plugin_pads(
        self,
        plugin_name: str,
        notes: set[int],
        actions: list[dict],
        locked: bool = True,
    ) -> None:
        """Register pads for a plugin from its action catalog.

        Args:
            plugin_name: Name of the plugin (e.g., "OBS", "Voicemeeter")
            notes: Set of MIDI notes assigned to this plugin
            actions: List of action dicts with 'id', 'label', 'color', 'desc'
            locked: Whether these pads should be locked from user editing
        """
        sorted_notes = sorted(notes)
        for i, note in enumerate(sorted_notes):
            if i < len(actions):
                action = actions[i]
                entry = PadEntry(
                    note=note,
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
                    label="",
                    source=f"plugin:{plugin_name}",
                    action_type="plugin",
                    action_data={"target": plugin_name},
                    locked=locked,
                )
            self._pads[note] = entry
            self._notify(note, entry)

    def on_change(self, callback: Callable[[int, PadEntry | None], None]) -> None:
        """Register a callback for pad changes. Called with (note, entry)."""
        if callback not in self._change_callbacks:
            self._change_callbacks.append(callback)

    def off_change(self, callback: Callable) -> None:
        """Unregister a change callback."""
        try:
            self._change_callbacks.remove(callback)
        except ValueError:
            pass

    def _notify(self, note: int, entry: PadEntry | None) -> None:
        for cb in self._change_callbacks:
            try:
                cb(note, entry)
            except Exception:
                pass

    def to_config_dict(self, notes: list[int] | None = None) -> list[dict]:
        """Export non-plugin pad entries as config.toml format dicts."""
        result = []
        for note in (notes or sorted(self._pads.keys())):
            entry = self._pads.get(note)
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
