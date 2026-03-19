"""Abstract base class for midi-macropad plugins."""
from abc import ABC, abstractmethod


class Plugin(ABC):
    """Base class for midi-macropad plugins.

    Subclasses must implement on_load() and on_unload().
    Override the event hooks (on_pad_press, on_knob, etc.) to intercept MIDI events.
    Return True from any hook to consume the event and skip the default handler.
    """

    name: str = "Unnamed Plugin"
    version: str = "0.0.0"
    description: str = ""
    _log_fn = None

    @abstractmethod
    def on_load(self, config: dict) -> None:
        """Called when the plugin is loaded. *config* comes from plugin.toml [settings]."""
        ...

    @abstractmethod
    def on_unload(self) -> None:
        """Called when the plugin is unloaded."""
        ...

    # -- MIDI event hooks (return True to consume) ----------------------------

    def on_pad_press(self, note: int, velocity: int) -> bool:
        return False

    def on_pad_release(self, note: int) -> bool:
        return False

    def on_knob(self, cc: int, value: int) -> bool:
        return False

    def on_pitch_bend(self, value: int) -> bool:
        return False

    # -- UI hooks -------------------------------------------------------------

    def get_pad_labels(self) -> dict[int, str]:
        """Return custom pad labels ``{note: label}`` for UI display."""
        return {}

    def on_mode_changed(self, mode_name: str) -> None:
        pass

    def get_status(self) -> tuple[str, tuple[int, int, int]] | None:
        """Return (text, color) for the header status strip, or None."""
        return None

    def build_ui(self, parent_tag: str) -> None:
        """Add DearPyGui widgets to the plugin's sidebar section."""
        pass

    # -- Dockable window API (new) -------------------------------------------

    def register_windows(self) -> list[dict]:
        """Return window descriptors for the central panel.

        Each dict: ``{"id": str, "title": str, "default_open": bool}``
        """
        return []

    def build_window(self, window_id: str, parent_tag: str) -> None:
        """Build UI for a registered dockable window."""
        pass

    def build_properties(self, parent_tag: str) -> None:
        """Build UI for the right-panel properties when this plugin is selected."""
        pass
