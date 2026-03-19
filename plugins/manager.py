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
        self.enabled: set[str] = set()
        self._discovered: list[dict] = []
        self._log = log_fn or (lambda *a, **kw: None)

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

    def load_plugin(self, plugin_info: dict) -> None:
        entry: str = plugin_info["entry"]
        if not entry:
            self._log("PLUGIN", f"No entry point for {plugin_info['name']}",
                      color=(255, 80, 80))
            return
        module_part, class_name = entry.rsplit(".", 1)
        plugin_path: Path = plugin_info["path"]
        added = False
        pdir = str(plugin_path)
        if pdir not in sys.path:
            sys.path.insert(0, pdir)
            added = True
        try:
            if module_part in sys.modules:
                module = importlib.reload(sys.modules[module_part])
            else:
                module = importlib.import_module(module_part)
            cls = getattr(module, class_name)
            instance: Plugin = cls()
            instance._log_fn = self._log
            instance.on_load(plugin_info.get("settings", {}))
            name = plugin_info["name"]
            self.plugins[name] = instance
            self.enabled.add(name)
            self._log("PLUGIN",
                      f"Loaded {name} v{plugin_info['version']}",
                      color=(100, 255, 150))
        except Exception:
            self._log("PLUGIN",
                      f"Failed to load {plugin_info['name']}: {traceback.format_exc()}",
                      color=(255, 80, 80))
        finally:
            if added and pdir in sys.path:
                sys.path.remove(pdir)

    def unload_plugin(self, name: str) -> None:
        plugin = self.plugins.pop(name, None)
        self.enabled.discard(name)
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
