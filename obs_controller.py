"""OBS WebSocket controller — scene switching, recording, streaming."""
import threading
import time

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

    def connect(self):
        if not HAS_OBS:
            return False
        try:
            self._ws = obsws(self.host, self.port, self.password)
            self._ws.connect()
            self.connected = True
            self._refresh_state()
            return True
        except Exception as e:
            self.connected = False
            return False

    def disconnect(self):
        if self._ws:
            try:
                self._ws.disconnect()
            except Exception:
                pass
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
            self.scenes = [s['sceneName'] for s in scenes_resp.getScenes()] if hasattr(scenes_resp, 'getScenes') else []
        except Exception:
            self._scenes = []

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
            self.is_recording = not self.is_recording
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
