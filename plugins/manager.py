"""Plugin manager — discovers, loads, and routes events to plugins."""
import importlib
import sys
import traceback
from pathlib import Path

import toml

from plugins.base import Plugin


class PluginManager:
    """Discovers, loads, and manages plugins stored in sub-directories."""

    def __init__(self, plugins_dir: Path, log_fn=None):
        self.plugins_dir = plugins_dir
        self.plugins: dict[str, Plugin] = {}
        self.enabled: list[str] = []
        self._discovered: list[dict] = []
        self._log = log_fn or (lambda *a, **kw: None)
        self._runtime_services: dict[str, object] = {}

    # -- Discovery ------------------------------------------------------------

    def discover(self) -> list[dict]:
        if self._discovered:
            return self._discovered
        results: list[dict] = []
        if not self.plugins_dir.is_dir():
            return results
        for child in sorted(self.plugins_dir.iterdir()):
            manifest = child / "plugin.toml"
            if not child.is_dir() or not manifest.exists():
                continue
            try:
                data = toml.load(str(manifest))
                meta = data.get("plugin", {})
                results.append({
                    "name": meta.get("name", child.name),
                    "version": meta.get("version", "0.0.0"),
                    "description": meta.get("description", ""),
                    "entry": meta.get("entry", ""),
                    "settings": data.get("settings", {}),
                    "path": child,
                })
            except Exception:
                self._log("PLUGIN",
                          f"Bad manifest in {child.name}: {traceback.format_exc()}",
                          color=(255, 80, 80))
        self._discovered = results
        return results

    # -- Loading / unloading --------------------------------------------------

    def set_runtime_services(self, services: dict[str, object]) -> None:
        self._runtime_services = dict(services)
        for plugin in self.plugins.values():
            try:
                plugin.set_runtime_services(self._runtime_services)
            except Exception:
                self._log("PLUGIN",
                          f"Failed to update runtime services: {traceback.format_exc()}",
                          color=(255, 80, 80))

    def load_plugin(self, plugin_info: dict) -> None:
        entry: str = plugin_info["entry"]
        if not entry:
            self._log("PLUGIN", f"No entry point for {plugin_info['name']}",
                      color=(255, 80, 80))
            return
        module_part, class_name = entry.rsplit(".", 1)
        plugin_path: Path = plugin_info["path"]
        added_dirs: list[str] = []
        pdir = str(plugin_path)
        plugins_root = str(plugin_path.parent)
        for d in (pdir, plugins_root):
            if d not in sys.path:
                sys.path.insert(0, d)
                added_dirs.append(d)
        try:
            if module_part in sys.modules:
                module = importlib.reload(sys.modules[module_part])
            else:
                module = importlib.import_module(module_part)
            cls = getattr(module, class_name)
            instance: Plugin = cls()
            instance._log_fn = self._log
            instance._logger = __import__("logger").get_logger(plugin_info["name"])
            instance.set_runtime_services(self._runtime_services)
            instance.on_load(plugin_info.get("settings", {}))
            name = plugin_info["name"]
            self.plugins[name] = instance
            if name not in self.enabled:
                self.enabled.append(name)
            self._log("PLUGIN",
                      f"Loaded {name} v{plugin_info['version']}",
                      color=(100, 255, 150))
        except Exception:
            self._log("PLUGIN",
                      f"Failed to load {plugin_info['name']}: {traceback.format_exc()}",
                      color=(255, 80, 80))
        finally:
            for d in added_dirs:
                if d in sys.path:
                    sys.path.remove(d)

    def unload_plugin(self, name: str) -> None:
        plugin = self.plugins.pop(name, None)
        if name in self.enabled:
            self.enabled.remove(name)
        if plugin is None:
            return
        try:
            plugin.on_unload()
            self._log("PLUGIN", f"Unloaded {name}", color=(255, 200, 80))
        except Exception:
            self._log("PLUGIN",
                      f"Error unloading {name}: {traceback.format_exc()}",
                      color=(255, 80, 80))

    def load_all(self) -> None:
        for info in self.discover():
            self.load_plugin(info)

    def unload_all(self) -> None:
        for name in list(self.plugins):
            self.unload_plugin(name)

    # -- Event routing --------------------------------------------------------

    def on_pad_press(self, note: int, velocity: int) -> bool:
        for name in list(self.enabled):
            plugin = self.plugins.get(name)
            if plugin is None:
                continue
            try:
                if plugin.on_pad_press(note, velocity):
                    return True
            except Exception:
                self._log("PLUGIN",
                          f"{name}.on_pad_press error: {traceback.format_exc()}",
                          color=(255, 80, 80))
        return False

    def on_pad_release(self, note: int) -> bool:
        for name in list(self.enabled):
            plugin = self.plugins.get(name)
            if plugin is None:
                continue
            try:
                if plugin.on_pad_release(note):
                    return True
            except Exception:
                self._log("PLUGIN",
                          f"{name}.on_pad_release error: {traceback.format_exc()}",
                          color=(255, 80, 80))
        return False

    def on_knob(self, cc: int, value: int) -> bool:
        for name in list(self.enabled):
            plugin = self.plugins.get(name)
            if plugin is None:
                continue
            try:
                if plugin.on_knob(cc, value):
                    return True
            except Exception:
                self._log("PLUGIN",
                          f"{name}.on_knob error: {traceback.format_exc()}",
                          color=(255, 80, 80))
        return False

    def on_pitch_bend(self, value: int) -> bool:
        for name in list(self.enabled):
            plugin = self.plugins.get(name)
            if plugin is None:
                continue
            try:
                if plugin.on_pitch_bend(value):
                    return True
            except Exception:
                self._log("PLUGIN",
                          f"{name}.on_pitch_bend error: {traceback.format_exc()}",
                          color=(255, 80, 80))
        return False

    def poll_all(self) -> None:
        for name in list(self.enabled):
            plugin = self.plugins.get(name)
            if plugin is None:
                continue
            try:
                plugin.poll()
            except Exception:
                self._log("PLUGIN",
                          f"{name}.poll error: {traceback.format_exc()}",
                          color=(255, 80, 80))

    def on_mode_changed(self, mode_name: str) -> None:
        for name in list(self.enabled):
            plugin = self.plugins.get(name)
            if plugin is None:
                continue
            try:
                plugin.on_mode_changed(mode_name)
            except Exception:
                self._log("PLUGIN",
                          f"{name}.on_mode_changed error: {traceback.format_exc()}",
                          color=(255, 80, 80))

    def notify_preset_changed(self, mapper) -> None:
        for name in list(self.enabled):
            plugin = self.plugins.get(name)
            if plugin is None:
                continue
            notes = mapper.get_plugin_notes(name)
            try:
                plugin.set_owned_notes(notes)
            except Exception:
                pass

    def get_active_status(self) -> tuple[str, tuple[int, int, int]] | None:
        for name in list(self.enabled):
            plugin = self.plugins.get(name)
            if plugin is None:
                continue
            try:
                status = plugin.get_status()
                if status is not None:
                    return status
            except Exception:
                pass
        return None

    # -- Action catalog -------------------------------------------------------

    def get_all_action_catalogs(self) -> dict[str, list[dict]]:
        """Return ``{plugin_name: [action_dicts]}`` for all loaded plugins."""
        catalogs: dict[str, list[dict]] = {}
        for name in list(self.enabled):
            plugin = self.plugins.get(name)
            if plugin is None:
                continue
            try:
                cat = plugin.get_action_catalog()
                if cat:
                    catalogs[name] = cat
            except Exception:
                self._log("PLUGIN",
                          f"{name}.get_action_catalog error: {traceback.format_exc()}",
                          color=(255, 80, 80))
        return catalogs

    def get_all_knob_catalogs(self) -> dict[str, list[dict]]:
        """Return ``{plugin_name: [knob_action_dicts]}`` for all loaded plugins."""
        catalogs: dict[str, list[dict]] = {}
        for name in list(self.enabled):
            plugin = self.plugins.get(name)
            if plugin is None:
                continue
            try:
                cat = plugin.get_knob_catalog()
                if cat:
                    catalogs[name] = cat
            except Exception:
                self._log("PLUGIN",
                          f"{name}.get_knob_catalog error: {traceback.format_exc()}",
                          color=(255, 80, 80))
        return catalogs

    def dispatch_knob_action(
        self, target: str, value: int, params: dict
    ) -> bool:
        """Route a knob action to a specific plugin by ``target`` = "Plugin:action_id"."""
        if not target:
            return False
        plugin_name, _, action_id = target.partition(":")
        plugin = self.plugins.get(plugin_name)
        if plugin is None or plugin_name not in self.enabled:
            return False
        try:
            return bool(plugin.execute_plugin_knob(action_id, value, params))
        except Exception:
            self._log("PLUGIN",
                      f"{plugin_name}.execute_plugin_knob error: {traceback.format_exc()}",
                      color=(255, 80, 80))
            return False

    # -- UI helpers -----------------------------------------------------------

    def get_all_pad_labels(self) -> dict[int, str]:
        labels: dict[int, str] = {}
        for name in self.enabled:
            plugin = self.plugins.get(name)
            if plugin is None:
                continue
            try:
                labels.update(plugin.get_pad_labels())
            except Exception:
                self._log("PLUGIN",
                          f"{name}.get_pad_labels error: {traceback.format_exc()}",
                          color=(255, 80, 80))
        return labels

    def get_pad_labels_by_plugin(self) -> dict[str, dict[int, str]]:
        """Return pad labels grouped by plugin name: {plugin_name: {note: label}}."""
        result: dict[str, dict[int, str]] = {}
        for name in self.enabled:
            plugin = self.plugins.get(name)
            if plugin is None:
                continue
            try:
                plugin_labels = plugin.get_pad_labels()
                if plugin_labels:
                    result[name] = plugin_labels
            except Exception:
                self._log("PLUGIN",
                          f"{name}.get_pad_labels error: {traceback.format_exc()}",
                          color=(255, 80, 80))
        return result

    def get_pad_states_by_plugin(self) -> dict[str, dict[int, bool | None]]:
        """Return pad states grouped by plugin name: {plugin_name: {note: state}}."""
        result: dict[str, dict[int, bool | None]] = {}
        for name in self.enabled:
            plugin = self.plugins.get(name)
            if plugin is None:
                continue
            try:
                plugin_states = plugin.get_pad_states()
                if plugin_states:
                    result[name] = plugin_states
            except Exception:
                pass
        return result

    def get_all_pad_states(self) -> dict[int, bool | None]:
        states: dict[int, bool | None] = {}
        for name in self.enabled:
            plugin = self.plugins.get(name)
            if plugin is None:
                continue
            try:
                states.update(plugin.get_pad_states())
            except Exception:
                pass
        return states

    def get_plugin_controlled_notes(self) -> set[int]:
        """Return the set of MIDI notes currently managed by any active plugin."""
        notes: set[int] = set()
        for name in self.enabled:
            plugin = self.plugins.get(name)
            if plugin is None:
                continue
            try:
                notes.update(plugin.get_pad_labels().keys())
            except Exception:
                pass
        return notes

    # -- Dockable window API --------------------------------------------------

    def get_all_windows(self) -> list[dict]:
        """Collect window descriptors from all enabled plugins."""
        windows: list[dict] = []
        for name in list(self.enabled):
            plugin = self.plugins.get(name)
            if plugin is None:
                continue
            try:
                for w in plugin.register_windows():
                    w["_plugin"] = name
                    windows.append(w)
            except Exception:
                self._log("PLUGIN",
                          f"{name}.register_windows error: {traceback.format_exc()}",
                          color=(255, 80, 80))
        return windows

    def build_plugin_window(self, plugin_name: str, window_id: str,
                            parent_tag: str) -> None:
        plugin = self.plugins.get(plugin_name)
        if plugin is None:
            return
        try:
            plugin.build_window(window_id, parent_tag)
        except Exception:
            self._log("PLUGIN",
                      f"{plugin_name}.build_window error: {traceback.format_exc()}",
                      color=(255, 80, 80))

    def build_plugin_properties(self, plugin_name: str,
                                parent_tag: str) -> None:
        plugin = self.plugins.get(plugin_name)
        if plugin is None:
            return
        try:
            plugin.build_properties(parent_tag)
        except Exception:
            self._log("PLUGIN",
                      f"{plugin_name}.build_properties error: {traceback.format_exc()}",
                      color=(255, 80, 80))
