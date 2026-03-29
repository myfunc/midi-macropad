"""OBS WebSocket controller — scene switching, recording, streaming (protocol v5)."""
from __future__ import annotations

import sys
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

        return [on_record_state_changed, on_record_file_changed]

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

    # -- Recording / streaming -------------------------------------------------

    def start_recording(self) -> bool:
        if not self.connected or not self._req:
            return False
        try:
            self._req.start_record()
            time.sleep(0.3)
            self.refresh_recording_state()
            return True
        except Exception:
            return False

    def stop_recording(self) -> bool:
        if not self.connected or not self._req:
            return False
        try:
            self._req.stop_record()
            time.sleep(0.3)
            self.refresh_recording_state()
            return True
        except Exception:
            return False

    def toggle_recording(self) -> bool:
        if not self.connected or not self._req:
            return False
        try:
            self._req.toggle_record()
            time.sleep(0.3)
            self.refresh_recording_state()
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

    def ensure_scene_exists(self, scene_name: str) -> bool:
        if not self.connected or not self._req:
            return False
        self._refresh_state()
        if scene_name in self._scenes:
            return True
        try:
            self._req.create_scene(name=scene_name)
            self._refresh_state()
            return scene_name in self._scenes
        except Exception:
            return False

    def add_source_to_scene(
        self,
        scene_name: str,
        source_name: str,
        input_kind: str,
        input_settings: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        if not self.connected or not self._req:
            return False, "not connected"
        settings = input_settings or {}
        try:
            in_scene = self.get_scene_source_names(scene_name)
            if source_name in in_scene:
                return True, "already in scene"
            inputs = self.get_input_names()
            if source_name in inputs:
                self._req.create_scene_item(scene_name, source_name, enabled=True)
                return True, "linked existing input"
            self._req.create_input(scene_name, source_name, input_kind, settings, True)
            return True, f"created {input_kind}"
        except Exception as exc:
            return False, str(exc)

    # -- Transforms ------------------------------------------------------------

    def get_video_settings(self) -> dict[str, Any]:
        if not self.connected or not self._req:
            return {}
        try:
            resp = self._req.get_video_settings()
            out: dict[str, Any] = {}
            for attr, key in [
                ("base_width", "baseWidth"),
                ("base_height", "baseHeight"),
                ("output_width", "outputWidth"),
                ("output_height", "outputHeight"),
            ]:
                val = getattr(resp, attr, None)
                if val is not None:
                    out[key] = int(val)
            return out
        except Exception:
            return {}

    def crop_source_to_right_half(self, scene_name: str, source_name: str) -> tuple[bool, str]:
        if not self.connected or not self._req:
            return False, "not connected"
        item_id = self.get_scene_item_id(scene_name, source_name)
        if item_id is None:
            return False, "scene item not found"
        video = self.get_video_settings()
        base_w = int(video.get("baseWidth") or 0)
        if base_w > 0:
            crop_left = max(0, base_w // 2)
            hint_base = f"canvas_w={base_w}"
        else:
            return False, "unknown width for crop"
        try:
            self._req.set_scene_item_transform(scene_name, item_id, {
                "cropLeft": crop_left,
                "cropTop": 0,
                "cropRight": 0,
                "cropBottom": 0,
                "positionX": 0.0,
                "positionY": 0.0,
                "scaleX": 1.0,
                "scaleY": 1.0,
            })
            return True, f"cropLeft={crop_left} ({hint_base})"
        except Exception as exc:
            return False, str(exc)

    def position_camera_pip(
        self,
        scene_name: str,
        source_name: str,
        *,
        scale: float = 0.28,
        margin: float = 24.0,
        assumed_base_size: tuple[int, int] = (1280, 720),
    ) -> tuple[bool, str]:
        if not self.connected or not self._req:
            return False, "not connected"
        item_id = self.get_scene_item_id(scene_name, source_name)
        if item_id is None:
            return False, "scene item not found"
        video = self.get_video_settings()
        base_w = float(video.get("baseWidth") or 1920)
        base_h = float(video.get("baseHeight") or 1080)
        src_w = float(assumed_base_size[0])
        src_h = float(assumed_base_size[1])
        short_side = min(base_w, base_h)
        disp_h = short_side * float(scale)
        disp_w = disp_h * (src_w / src_h) if src_h > 0 else disp_h * (16.0 / 9.0)
        sx = max(0.02, disp_w / src_w) if src_w > 0 else float(scale)
        sy = max(0.02, disp_h / src_h) if src_h > 0 else float(scale)
        disp_w = src_w * sx
        disp_h = src_h * sy
        pos_x = max(0.0, base_w - margin - disp_w)
        pos_y = max(0.0, base_h - margin - disp_h)
        try:
            self._req.set_scene_item_transform(scene_name, item_id, {
                "cropLeft": 0,
                "cropTop": 0,
                "cropRight": 0,
                "cropBottom": 0,
                "positionX": pos_x,
                "positionY": pos_y,
                "scaleX": sx,
                "scaleY": sy,
            })
            return True, f"pip scale={scale} pos=({pos_x:.0f},{pos_y:.0f})"
        except Exception as exc:
            return False, str(exc)

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

    # -- Composite setup -------------------------------------------------------

    def setup_three_scenes(
        self,
        scene_screen: str,
        scene_camera: str,
        scene_pip: str,
        right_source: str,
        camera_source: str,
        mic_source: str,
    ) -> tuple[bool, list[str]]:
        """Build three OBS scenes for a unified screen / camera / PiP workflow."""
        notes: list[str] = []
        if not self.connected:
            return False, ["not connected"]

        if sys.platform == "darwin":
            kind_right, kind_cam, kind_mic = (
                "monitor_capture", "av_capture_input", "coreaudio_input_capture",
            )
        else:
            kind_right, kind_cam, kind_mic = (
                "monitor_capture", "dshow_input", "wasapi_input_capture",
            )
        notes.append(f"platform={sys.platform}")

        success = True

        for label, name in (
            ("screen", scene_screen),
            ("camera", scene_camera),
            ("pip", scene_pip),
        ):
            if self.ensure_scene_exists(name):
                notes.append(f"{label} ({name}): ready")
            else:
                success = False
                notes.append(f"{label} ({name}): create failed")

        ok, msg = self.add_source_to_scene(scene_screen, right_source, kind_right)
        if not ok:
            success = False
        notes.append(f"screen + {right_source}: {msg}")

        ok, msg = self.crop_source_to_right_half(scene_screen, right_source)
        if not ok:
            success = False
        notes.append(f"screen crop: {msg}")

        ok, msg = self.add_source_to_scene(scene_camera, camera_source, kind_cam)
        if not ok:
            success = False
        notes.append(f"camera + {camera_source}: {msg}")

        for lbl, sn in (("screen", scene_screen), ("camera", scene_camera), ("pip", scene_pip)):
            ok, msg = self.add_source_to_scene(sn, mic_source, kind_mic)
            if not ok:
                success = False
            notes.append(f"{lbl} + {mic_source}: {msg}")

        ok, msg = self.add_source_to_scene(scene_pip, right_source, kind_right)
        if not ok:
            success = False
        notes.append(f"pip + {right_source}: {msg}")

        ok, msg = self.crop_source_to_right_half(scene_pip, right_source)
        if not ok:
            success = False
        notes.append(f"pip crop: {msg}")

        ok, msg = self.add_source_to_scene(scene_pip, camera_source, kind_cam)
        if not ok:
            success = False
        notes.append(f"pip + {camera_source}: {msg}")

        ok, msg = self.position_camera_pip(scene_pip, camera_source)
        if not ok:
            success = False
        notes.append(f"pip camera position: {msg}")

        return success, notes
