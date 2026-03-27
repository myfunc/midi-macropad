"""OBS WebSocket controller — scene switching, recording, streaming."""
from __future__ import annotations

import time
from typing import Any, Callable

try:
    from obswebsocket import obsws, requests as obs_requests
    HAS_OBS = True
except ImportError:
    HAS_OBS = False


class OBSController:
    """Controls OBS Studio via WebSocket protocol (obs-websocket plugin, built-in since OBS 28)."""

    def __init__(self, host: str = "localhost", port: int = 4455, password: str = ""):
        self.host = host
        self.port = port
        self.password = password
        self._ws = None
        self.connected = False
        self.current_scene = ""
        self.is_recording = False
        self.is_streaming = False
        self._scenes = []
        self.obs_version = ""
        self.websocket_version = ""
        self.last_connect_error: str | None = None

    def connect(self):
        if not HAS_OBS:
            self.last_connect_error = "obs-websocket-py not installed"
            return False
        self.last_connect_error = None
        try:
            self._ws = obsws(self.host, self.port, self.password)
            self._ws.connect()
            self.connected = True
            self._hydrate_versions()
            self._refresh_state()
            return True
        except Exception as e:
            self.connected = False
            self.last_connect_error = str(e)
            return False

    def disconnect(self):
        if self._ws:
            try:
                self._ws.disconnect()
            except Exception:
                pass
            self._ws = None
        self.connected = False

    def _refresh_state(self):
        if not self.connected:
            return
        try:
            scene_resp = self._ws.call(obs_requests.GetCurrentProgramScene())
            self.current_scene = scene_resp.getSceneName() if hasattr(scene_resp, 'getSceneName') else ""
        except Exception:
            pass
        try:
            rec_resp = self._ws.call(obs_requests.GetRecordStatus())
            self.is_recording = rec_resp.getOutputActive() if hasattr(rec_resp, 'getOutputActive') else False
        except Exception:
            pass
        try:
            stream_resp = self._ws.call(obs_requests.GetStreamStatus())
            self.is_streaming = stream_resp.getOutputActive() if hasattr(stream_resp, 'getOutputActive') else False
        except Exception:
            pass
        try:
            scenes_resp = self._ws.call(obs_requests.GetSceneList())
            if hasattr(scenes_resp, "getScenes"):
                raw = scenes_resp.getScenes()
                self._scenes = [s["sceneName"] for s in raw] if raw else []
            else:
                self._scenes = []
        except Exception:
            self._scenes = []

    def _hydrate_versions(self) -> None:
        if not self.connected or not self._ws:
            return
        try:
            ver = self._ws.call(obs_requests.GetVersion())
            self.obs_version = ver.getObsVersion() if hasattr(ver, "getObsVersion") else ""
            self.websocket_version = (
                ver.getObsWebSocketVersion() if hasattr(ver, "getObsWebSocketVersion") else ""
            )
        except Exception:
            self.obs_version = ""
            self.websocket_version = ""

    def ping(self) -> bool:
        """Return True if the WebSocket session is still alive."""
        if not self.connected or not self._ws:
            return False
        try:
            self._ws.call(obs_requests.GetVersion())
            return True
        except Exception:
            try:
                self.disconnect()
            except Exception:
                self.connected = False
                self._ws = None
            return False

    def refresh_recording_state(self) -> None:
        """Sync is_recording from OBS (call after external Start/StopRecord)."""
        if not self.connected:
            return
        try:
            rec_resp = self._ws.call(obs_requests.GetRecordStatus())
            self.is_recording = (
                rec_resp.getOutputActive() if hasattr(rec_resp, "getOutputActive") else False
            )
        except Exception:
            pass

    def register_record_state_callback(self, fn: Callable[[Any], None]) -> None:
        """Subscribe to RecordStateChanged (runs on websocket recv thread)."""
        if not self._ws:
            return
        try:
            from obswebsocket import events

            self._ws.register(fn, events.RecordStateChanged)
        except Exception:
            pass

    def unregister_record_state_callback(self, fn: Callable[[Any], None]) -> None:
        if not self._ws:
            return
        try:
            from obswebsocket import events

            self._ws.unregister(fn, events.RecordStateChanged)
        except Exception:
            pass

    def register_record_file_callback(self, fn: Callable[[Any], None]) -> None:
        if not self._ws:
            return
        try:
            from obswebsocket import events

            self._ws.register(fn, events.RecordFileChanged)
        except Exception:
            pass

    def unregister_record_file_callback(self, fn: Callable[[Any], None]) -> None:
        if not self._ws:
            return
        try:
            from obswebsocket import events

            self._ws.unregister(fn, events.RecordFileChanged)
        except Exception:
            pass

    def start_recording(self) -> bool:
        if not self.connected:
            return False
        try:
            self._ws.call(obs_requests.StartRecord())
            self.refresh_recording_state()
            return True
        except Exception:
            return False

    def stop_recording(self) -> bool:
        if not self.connected:
            return False
        try:
            self._ws.call(obs_requests.StopRecord())
            self.refresh_recording_state()
            return True
        except Exception:
            return False

    def get_input_names(self) -> set[str]:
        if not self.connected:
            return set()
        try:
            resp = self._ws.call(obs_requests.GetInputList())
            rows = resp.getInputs() if hasattr(resp, "getInputs") else []
            return {row["inputName"] for row in rows} if rows else set()
        except Exception:
            return set()

    def get_scene_source_names(self, scene_name: str) -> set[str]:
        if not self.connected:
            return set()
        try:
            resp = self._ws.call(obs_requests.GetSceneItemList(sceneName=scene_name))
            items = resp.getSceneItems() if hasattr(resp, "getSceneItems") else []
            names = set()
            for it in items or []:
                if isinstance(it, dict) and "sourceName" in it:
                    names.add(it["sourceName"])
            return names
        except Exception:
            return set()

    def get_scene_item_id(self, scene_name: str, source_name: str) -> int | None:
        if not self.connected:
            return None
        try:
            resp = self._ws.call(obs_requests.GetSceneItemList(sceneName=scene_name))
            items = resp.getSceneItems() if hasattr(resp, "getSceneItems") else []
            for it in items or []:
                if not isinstance(it, dict):
                    continue
                if it.get("sourceName") == source_name and "sceneItemId" in it:
                    return int(it["sceneItemId"])
            return None
        except Exception:
            return None

    def ensure_scene_exists(self, scene_name: str) -> bool:
        """Create *scene_name* if missing. Does not remove or rename existing scenes."""
        if not self.connected:
            return False
        self._refresh_state()
        if scene_name in self._scenes:
            return True
        try:
            self._ws.call(obs_requests.CreateScene(sceneName=scene_name))
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
        """Create a new input on *scene_name* or link an existing global input."""
        if not self.connected:
            return False, "not connected"
        settings = input_settings or {}
        try:
            in_scene = self.get_scene_source_names(scene_name)
            if source_name in in_scene:
                return True, "already in scene"
            inputs = self.get_input_names()
            if source_name in inputs:
                self._ws.call(
                    obs_requests.CreateSceneItem(
                        sceneName=scene_name, sourceName=source_name
                    )
                )
                return True, "linked existing input"
            self._ws.call(
                obs_requests.CreateInput(
                    sceneName=scene_name,
                    inputName=source_name,
                    inputKind=input_kind,
                    inputSettings=settings,
                    sceneItemEnabled=True,
                )
            )
            return True, f"created {input_kind}"
        except Exception as exc:
            return False, str(exc)

    def get_default_input_settings(self, input_kind: str) -> dict[str, Any]:
        if not self.connected:
            return {}
        try:
            resp = self._ws.call(
                obs_requests.GetInputDefaultSettings(inputKind=input_kind)
            )
            d = resp.getDefaultInputSettings() if hasattr(resp, "getDefaultInputSettings") else {}
            return dict(d or {})
        except Exception:
            return {}

    def get_input_settings(self, input_name: str) -> dict[str, Any]:
        if not self.connected:
            return {}
        try:
            resp = self._ws.call(obs_requests.GetInputSettings(inputName=input_name))
            s = resp.getInputSettings() if hasattr(resp, "getInputSettings") else {}
            return dict(s) if isinstance(s, dict) else {}
        except Exception:
            return {}

    def get_video_settings(self) -> dict[str, Any]:
        if not self.connected:
            return {}
        try:
            resp = self._ws.call(obs_requests.GetVideoSettings())
            out: dict[str, Any] = {}
            if hasattr(resp, "getBaseWidth"):
                out["baseWidth"] = int(resp.getBaseWidth())
            if hasattr(resp, "getBaseHeight"):
                out["baseHeight"] = int(resp.getBaseHeight())
            if hasattr(resp, "getOutputWidth"):
                out["outputWidth"] = int(resp.getOutputWidth())
            if hasattr(resp, "getOutputHeight"):
                out["outputHeight"] = int(resp.getOutputHeight())
            return out
        except Exception:
            return {}

    def crop_source_to_right_half(self, scene_name: str, source_name: str) -> tuple[bool, str]:
        if not self.connected:
            return False, "not connected"
        item_id = self.get_scene_item_id(scene_name, source_name)
        if item_id is None:
            return False, "scene item not found"
        settings = self.get_input_settings(source_name)
        src_w = 0
        for key in ("cx", "width", "resolution_x"):
            v = settings.get(key)
            if v is not None:
                try:
                    src_w = int(float(v))
                    break
                except (TypeError, ValueError):
                    pass
        if src_w <= 0:
            res = settings.get("resolution")
            if isinstance(res, str) and "x" in res.lower():
                try:
                    part = res.lower().split("x", 1)[0].strip()
                    src_w = int(float(part))
                except (TypeError, ValueError):
                    src_w = 0
        video = self.get_video_settings()
        base_w = int(video.get("baseWidth") or 0)
        if src_w > 0:
            crop_left = max(0, src_w // 2)
            hint_base = f"source_w={src_w}"
        elif base_w > 0:
            crop_left = max(0, base_w // 2)
            hint_base = f"canvas_w={base_w} (fallback)"
        else:
            return False, "unknown width for crop"
        try:
            self._ws.call(
                obs_requests.SetSceneItemTransform(
                    sceneName=scene_name,
                    sceneItemId=item_id,
                    sceneItemTransform={
                        "cropLeft": crop_left,
                        "cropTop": 0,
                        "cropRight": 0,
                        "cropBottom": 0,
                        "positionX": 0.0,
                        "positionY": 0.0,
                        "scaleX": 1.0,
                        "scaleY": 1.0,
                    },
                )
            )
            return True, f"cropLeft={crop_left} ({hint_base})"
        except Exception as exc:
            return False, str(exc)

    def switch_scene(self, scene_name: str) -> bool:
        if not self.connected:
            return False
        try:
            self._ws.call(obs_requests.SetCurrentProgramScene(sceneName=scene_name))
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
            next_idx = (idx + 1) % len(self._scenes)
            return self.switch_scene(self._scenes[next_idx])
        except (ValueError, IndexError):
            return False

    def prev_scene(self) -> bool:
        if not self.connected or not self._scenes:
            self._refresh_state()
        if not self._scenes:
            return False
        try:
            idx = self._scenes.index(self.current_scene)
            prev_idx = (idx - 1) % len(self._scenes)
            return self.switch_scene(self._scenes[prev_idx])
        except (ValueError, IndexError):
            return False

    def toggle_recording(self) -> bool:
        if not self.connected:
            return False
        try:
            self._ws.call(obs_requests.ToggleRecord())
            self.refresh_recording_state()
            return True
        except Exception:
            return False

    def toggle_streaming(self) -> bool:
        if not self.connected:
            return False
        try:
            self._ws.call(obs_requests.ToggleStream())
            self.is_streaming = not self.is_streaming
            return True
        except Exception:
            return False

    def toggle_source_mute(self, source_name: str) -> bool:
        if not self.connected:
            return False
        try:
            self._ws.call(obs_requests.ToggleInputMute(inputName=source_name))
            return True
        except Exception:
            return False

    def get_record_directory(self) -> str | None:
        """Return OBS's current recording directory, or None on failure."""
        if not self.connected or not self._ws:
            return None
        try:
            resp = self._ws.call(obs_requests.GetRecordDirectory())
            if hasattr(resp, "getRecordDirectory"):
                path = resp.getRecordDirectory()
                return str(path) if path else None
        except Exception:
            pass
        return None

    def set_record_directory(self, directory: str) -> tuple[bool, str]:
        """Set folder where OBS writes recordings. Path should exist."""
        if not self.connected or not self._ws:
            return False, "not connected"
        path = str(directory).strip()
        if not path:
            return False, "empty path"
        try:
            self._ws.call(obs_requests.SetRecordDirectory(recordDirectory=path))
            return True, path
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
        """
        Place *source_name* as a bottom-right picture-in-picture on *scene_name*.

        Uses assumed source pixel size for layout when actual dimensions are unknown
        (typical webcam / capture card presets).
        """
        if not self.connected:
            return False, "not connected"
        item_id = self.get_scene_item_id(scene_name, source_name)
        if item_id is None:
            return False, "scene item not found"
        video = self.get_video_settings()
        base_w = float(video.get("baseWidth") or 1920)
        base_h = float(video.get("baseHeight") or 1080)
        settings = self.get_input_settings(source_name)
        src_w = float(assumed_base_size[0])
        src_h = float(assumed_base_size[1])
        for wkey, hkey in (("cx", "cy"), ("width", "height")):
            if wkey in settings and hkey in settings:
                try:
                    src_w = float(settings[wkey])
                    src_h = float(settings[hkey])
                    break
                except (TypeError, ValueError):
                    pass
        short_side = min(base_w, base_h)
        disp_h = short_side * float(scale)
        if src_h > 0:
            disp_w = disp_h * (src_w / src_h)
        else:
            disp_w = disp_h * (16.0 / 9.0)
        sx = max(0.02, disp_w / src_w) if src_w > 0 else float(scale)
        sy = max(0.02, disp_h / src_h) if src_h > 0 else float(scale)
        disp_w = src_w * sx
        disp_h = src_h * sy
        pos_x = max(0.0, base_w - margin - disp_w)
        pos_y = max(0.0, base_h - margin - disp_h)
        try:
            self._ws.call(
                obs_requests.SetSceneItemTransform(
                    sceneName=scene_name,
                    sceneItemId=item_id,
                    sceneItemTransform={
                        "cropLeft": 0,
                        "cropTop": 0,
                        "cropRight": 0,
                        "cropBottom": 0,
                        "positionX": pos_x,
                        "positionY": pos_y,
                        "scaleX": sx,
                        "scaleY": sy,
                    },
                )
            )
            return True, f"pip scale={scale} pos=({pos_x:.0f},{pos_y:.0f})"
        except Exception as exc:
            return False, str(exc)
