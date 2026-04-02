"""OBS WebSocket controller — scene switching, recording, streaming (protocol v5)."""
from __future__ import annotations

import time
from typing import Any, Callable

try:
    import obsws_python as obsws
    HAS_OBS = True
except ImportError:
    HAS_OBS = False


class OBSController:
    """Controls OBS Studio via WebSocket protocol v5 (OBS 28+)."""

    def __init__(self, host: str = "localhost", port: int = 4455, password: str = ""):
        self.host = host
        self.port = port
        self.password = password
        self._req = None
        self._evt = None
        self.connected = False
        self.current_scene = ""
        self.is_recording = False
        self.is_streaming = False
        self._scenes: list[str] = []
        self.obs_version = ""
        self.websocket_version = ""
        self.last_connect_error: str | None = None
        self._record_state_cbs: list[Callable] = []
        self._record_file_cbs: list[Callable] = []
        self._replay_buffer_cbs: list[Callable] = []
        self.is_replay_buffer_active = False

    def _conn_kwargs(self) -> dict[str, Any]:
        kw: dict[str, Any] = {"host": self.host, "port": int(self.port), "timeout": 5}
        if self.password:
            kw["password"] = self.password
        return kw

    def connect(self) -> bool:
        if not HAS_OBS:
            self.last_connect_error = "obsws-python not installed"
            return False
        self.last_connect_error = None
        try:
            self._req = obsws.ReqClient(**self._conn_kwargs())
            self.connected = True
            self._hydrate_versions()
            self._refresh_state()
            self._connect_events()
            return True
        except Exception as e:
            self.connected = False
            self.last_connect_error = str(e)
            self._req = None
            return False

    def _connect_events(self) -> None:
        try:
            if self._evt is not None:
                try:
                    self._evt.disconnect()
                except Exception:
                    pass
            self._evt = obsws.EventClient(**self._conn_kwargs())
            self._evt.callback.register(self._make_event_callbacks())
        except Exception:
            self._evt = None

    def _make_event_callbacks(self) -> list[Callable]:
        """Return a list of named functions that obsws-python's Callback can route."""
        controller = self

        def on_record_state_changed(data):
            for cb in controller._record_state_cbs:
                try:
                    cb(data)
                except Exception:
                    pass

        def on_record_file_changed(data):
            for cb in controller._record_file_cbs:
                try:
                    cb(data)
                except Exception:
                    pass

        def on_replay_buffer_saved(data):
            for cb in controller._replay_buffer_cbs:
                try:
                    cb(data)
                except Exception:
                    pass

        return [on_record_state_changed, on_record_file_changed, on_replay_buffer_saved]

    def disconnect(self) -> None:
        if self._evt:
            try:
                self._evt.disconnect()
            except Exception:
                pass
            self._evt = None
        if self._req:
            try:
                self._req.disconnect()
            except Exception:
                pass
            self._req = None
        self.connected = False

    def _refresh_state(self) -> None:
        if not self.connected or not self._req:
            return
        try:
            resp = self._req.get_current_program_scene()
            self.current_scene = getattr(resp, "scene_name", "") or getattr(resp, "current_program_scene_name", "")
        except Exception:
            pass
        try:
            resp = self._req.get_record_status()
            self.is_recording = getattr(resp, "output_active", False)
        except Exception:
            pass
        try:
            resp = self._req.get_stream_status()
            self.is_streaming = getattr(resp, "output_active", False)
        except Exception:
            pass
        try:
            resp = self._req.get_scene_list()
            scenes_raw = getattr(resp, "scenes", [])
            self._scenes = [s["sceneName"] for s in scenes_raw] if scenes_raw else []
        except Exception:
            self._scenes = []
        try:
            resp = self._req.get_replay_buffer_status()
            self.is_replay_buffer_active = getattr(resp, "output_active", False)
        except Exception:
            self.is_replay_buffer_active = False

    def _hydrate_versions(self) -> None:
        if not self.connected or not self._req:
            return
        try:
            ver = self._req.get_version()
            self.obs_version = getattr(ver, "obs_version", "")
            self.websocket_version = getattr(ver, "obs_web_socket_version", "")
        except Exception:
            self.obs_version = ""
            self.websocket_version = ""

    @property
    def scene_names(self) -> list[str]:
        """Return cached list of scene names in OBS."""
        return list(self._scenes)

    def ping(self) -> bool:
        if not self.connected or not self._req:
            return False
        try:
            self._req.get_version()
            return True
        except Exception:
            try:
                self.disconnect()
            except Exception:
                self.connected = False
                self._req = None
            return False

    def refresh_recording_state(self) -> None:
        if not self.connected or not self._req:
            return
        try:
            resp = self._req.get_record_status()
            self.is_recording = getattr(resp, "output_active", False)
        except Exception:
            pass

    # -- Event callbacks -------------------------------------------------------

    def register_record_state_callback(self, fn: Callable) -> None:
        if fn not in self._record_state_cbs:
            self._record_state_cbs.append(fn)

    def unregister_record_state_callback(self, fn: Callable) -> None:
        try:
            self._record_state_cbs.remove(fn)
        except ValueError:
            pass

    def register_record_file_callback(self, fn: Callable) -> None:
        if fn not in self._record_file_cbs:
            self._record_file_cbs.append(fn)

    def unregister_record_file_callback(self, fn: Callable) -> None:
        try:
            self._record_file_cbs.remove(fn)
        except ValueError:
            pass

    def register_replay_buffer_callback(self, fn: Callable) -> None:
        if fn not in self._replay_buffer_cbs:
            self._replay_buffer_cbs.append(fn)

    def unregister_replay_buffer_callback(self, fn: Callable) -> None:
        try:
            self._replay_buffer_cbs.remove(fn)
        except ValueError:
            pass

    # -- Recording / streaming -------------------------------------------------

    def start_recording(self) -> bool:
        if not self.connected or not self._req:
            return False
        try:
            self._req.start_record()
            self.is_recording = True
            return True
        except Exception:
            return False

    def stop_recording(self) -> bool:
        if not self.connected or not self._req:
            return False
        try:
            self._req.stop_record()
            self.is_recording = False
            return True
        except Exception:
            return False

    def toggle_recording(self) -> bool:
        if not self.connected or not self._req:
            return False
        try:
            self._req.toggle_record()
            self.is_recording = not self.is_recording
            return True
        except Exception:
            return False

    def toggle_streaming(self) -> bool:
        if not self.connected or not self._req:
            return False
        try:
            self._req.toggle_stream()
            self.is_streaming = not self.is_streaming
            return True
        except Exception:
            return False

    # -- Replay buffer ---------------------------------------------------------

    def start_replay_buffer(self) -> bool:
        if not self.connected or not self._req:
            return False
        try:
            self._req.start_replay_buffer()
            self.is_replay_buffer_active = True
            return True
        except Exception:
            return False

    def stop_replay_buffer(self) -> bool:
        if not self.connected or not self._req:
            return False
        try:
            self._req.stop_replay_buffer()
            self.is_replay_buffer_active = False
            return True
        except Exception:
            return False

    def toggle_replay_buffer(self) -> bool:
        if not self.connected or not self._req:
            return False
        try:
            self._req.toggle_replay_buffer()
            try:
                resp = self._req.get_replay_buffer_status()
                self.is_replay_buffer_active = getattr(resp, "output_active", False)
            except Exception:
                self.is_replay_buffer_active = not self.is_replay_buffer_active
            return True
        except Exception:
            return False

    def save_replay_buffer(self) -> bool:
        if not self.connected or not self._req:
            return False
        try:
            self._req.save_replay_buffer()
            return True
        except Exception:
            return False

    def get_replay_buffer_status(self) -> bool:
        if not self.connected or not self._req:
            return False
        try:
            resp = self._req.get_replay_buffer_status()
            self.is_replay_buffer_active = getattr(resp, "output_active", False)
            return self.is_replay_buffer_active
        except Exception:
            return False

    def get_last_replay_buffer_replay(self) -> str | None:
        if not self.connected or not self._req:
            return None
        try:
            resp = self._req.get_last_replay_buffer_replay()
            return getattr(resp, "saved_replay_path", None)
        except Exception:
            return None

    # -- Audio volume / mute ---------------------------------------------------

    def get_input_volume(self, input_name: str) -> float | None:
        """Return input volume in dB, or None on error."""
        if not self.connected or not self._req:
            return None
        try:
            resp = self._req.get_input_volume(name=input_name)
            return float(getattr(resp, "input_volume_db", 0.0))
        except Exception:
            return None

    def set_input_volume(self, input_name: str, volume_db: float) -> bool:
        if not self.connected or not self._req:
            return False
        try:
            self._req.set_input_volume(name=input_name, input_volume_db=volume_db)
            return True
        except Exception:
            return False

    def get_input_mute(self, input_name: str) -> bool | None:
        """Return mute state, or None on error."""
        if not self.connected or not self._req:
            return None
        try:
            resp = self._req.get_input_mute(name=input_name)
            return bool(getattr(resp, "input_muted", False))
        except Exception:
            return None

    def set_input_mute(self, input_name: str, muted: bool) -> bool:
        if not self.connected or not self._req:
            return False
        try:
            self._req.set_input_mute(name=input_name, input_muted=muted)
            return True
        except Exception:
            return False

    def get_audio_input_names(self) -> list[str]:
        """Return names of audio-type inputs only (WASAPI, PulseAudio, CoreAudio)."""
        if not self.connected or not self._req:
            return []
        try:
            resp = self._req.get_input_list()
            rows = getattr(resp, "inputs", [])
            audio_kinds = {"wasapi_input_capture", "wasapi_output_capture",
                           "pulse_input_capture", "pulse_output_capture",
                           "coreaudio_input_capture", "coreaudio_output_capture"}
            result = []
            for row in (rows or []):
                kind = row.get("inputKind", "")
                if kind in audio_kinds or "audio" in kind.lower():
                    result.append(row["inputName"])
            return sorted(result)
        except Exception:
            return []

    # -- Inputs ----------------------------------------------------------------

    def get_input_names(self) -> set[str]:
        if not self.connected or not self._req:
            return set()
        try:
            resp = self._req.get_input_list()
            rows = getattr(resp, "inputs", [])
            return {row["inputName"] for row in rows} if rows else set()
        except Exception:
            return set()

    def get_input_settings(self, input_name: str) -> dict[str, Any]:
        if not self.connected or not self._req:
            return {}
        try:
            resp = self._req.get_input_settings(name=input_name)
            s = getattr(resp, "input_settings", {})
            return dict(s) if isinstance(s, dict) else {}
        except Exception:
            return {}

    def get_default_input_settings(self, input_kind: str) -> dict[str, Any]:
        if not self.connected or not self._req:
            return {}
        try:
            resp = self._req.get_input_default_settings(kind=input_kind)
            d = getattr(resp, "default_input_settings", {})
            return dict(d or {})
        except Exception:
            return {}

    # -- Scenes ----------------------------------------------------------------

    def get_scene_source_names(self, scene_name: str) -> set[str]:
        if not self.connected or not self._req:
            return set()
        try:
            resp = self._req.get_scene_item_list(name=scene_name)
            items = getattr(resp, "scene_items", [])
            names = set()
            for it in items or []:
                if isinstance(it, dict) and "sourceName" in it:
                    names.add(it["sourceName"])
            return names
        except Exception:
            return set()

    def get_scene_item_id(self, scene_name: str, source_name: str) -> int | None:
        if not self.connected or not self._req:
            return None
        try:
            resp = self._req.get_scene_item_id(scene_name, source_name)
            return int(getattr(resp, "scene_item_id", 0)) or None
        except Exception:
            return None


    # -- Scene switching -------------------------------------------------------

    def switch_scene(self, scene_name: str) -> bool:
        if not self.connected or not self._req:
            return False
        try:
            self._req.set_current_program_scene(name=scene_name)
            self.current_scene = scene_name
            return True
        except Exception:
            return False

    def next_scene(self) -> bool:
        if not self.connected or not self._scenes:
            self._refresh_state()
        if not self._scenes:
            return False
        try:
            idx = self._scenes.index(self.current_scene)
            return self.switch_scene(self._scenes[(idx + 1) % len(self._scenes)])
        except (ValueError, IndexError):
            return False

    def prev_scene(self) -> bool:
        if not self.connected or not self._scenes:
            self._refresh_state()
        if not self._scenes:
            return False
        try:
            idx = self._scenes.index(self.current_scene)
            return self.switch_scene(self._scenes[(idx - 1) % len(self._scenes)])
        except (ValueError, IndexError):
            return False

    # -- Source mute ------------------------------------------------------------

    def toggle_source_mute(self, source_name: str) -> bool:
        if not self.connected or not self._req:
            return False
        try:
            self._req.toggle_input_mute(name=source_name)
            return True
        except Exception:
            return False

    # -- Record directory ------------------------------------------------------

    def get_record_directory(self) -> str | None:
        if not self.connected or not self._req:
            return None
        try:
            resp = self._req.get_record_directory()
            return str(getattr(resp, "record_directory", "")) or None
        except Exception:
            return None

    def set_record_directory(self, directory: str) -> tuple[bool, str]:
        if not self.connected or not self._req:
            return False, "not connected"
        path = str(directory).strip()
        if not path:
            return False, "empty path"
        try:
            self._req.set_record_directory(path)
            return True, path
        except Exception as exc:
            return False, str(exc)

