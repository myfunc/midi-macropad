"""Unified OBS plugin — scene switching, segmented recording, session diary, MIDI feedback."""

from __future__ import annotations

import importlib.util
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from base import Plugin
from logger import get_logger
import settings

log = get_logger("obs_session")

MODE_NAME = "OBS"
SETTINGS_KEY = "obs_session_plugin"

PAD_ACTIONS = [
    {"id": "scene_screen", "label": "Screen", "color": (100, 180, 255), "desc": "Screen share + PiP webcam"},
    {"id": "scene_camera", "label": "Camera", "color": (100, 180, 255), "desc": "Webcam fullscreen"},
    {"id": "scene_pip", "label": "PiP", "color": (100, 180, 255), "desc": "Screen + camera corner"},
    {"id": "scene_cam_app", "label": "Cam+App", "color": (100, 180, 255), "desc": "Camera 80% + app window 20%"},
    {"id": "mute_mic", "label": "Mute Mic", "color": (150, 150, 165), "desc": "Toggle microphone"},
    {"id": "mute_desktop", "label": "Mute Desktop", "color": (150, 150, 165), "desc": "Toggle desktop audio"},
    {"id": "mute_aux", "label": "Mute AUX", "color": (150, 150, 165), "desc": "Toggle AUX audio"},
    {"id": "session", "label": "Session", "color": (255, 200, 90), "desc": "Start or stop diary session"},
    {"id": "record", "label": "Record", "color": (255, 120, 120), "desc": "Record or toggle segment"},
    {"id": "save_replay", "label": "Save Replay", "color": (180, 130, 255), "desc": "Save replay buffer clip"},
]
PAD_ACTION_IDS = [a["id"] for a in PAD_ACTIONS]
_ACTION_CATALOG = [
    {"id": a["id"], "label": a["label"], "description": a["desc"]}
    for a in PAD_ACTIONS
]

DEFAULT_SLOT_MAP: dict[int, str | None] = {
    16: "scene_screen",
    17: "scene_camera",
    18: "scene_pip",
    19: "scene_cam_app",
    20: "mute_mic",
    21: "session",
    22: "record",
    23: "save_replay",
}
DEFAULT_NOTE_ORDER = sorted(DEFAULT_SLOT_MAP.keys())

_CONNECTED_OK = (100, 255, 150)
_CONNECTED_BAD = (255, 90, 90)
_STATUS_IDLE = (150, 150, 165)
_STATUS_ACTIVE = (255, 200, 90)
_STATUS_RECORDING = (255, 100, 100)
_STATUS_POSTPROCESS = (120, 190, 255)
_STATUS_WARN = (255, 200, 120)

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
_VOICE_SCRIBE_KEY_FILE = Path(__file__).resolve().parent.parent / "voice_scribe" / ".api_key"

_POSTPROCESS_MODULE = None


def _get_postprocess():
    """Load postprocess from this package directory (survives PluginManager sys.path cleanup)."""
    global _POSTPROCESS_MODULE
    if _POSTPROCESS_MODULE is not None:
        return _POSTPROCESS_MODULE
    path = Path(__file__).resolve().parent / "postprocess.py"
    name = "midi_macropad.obs_session_postprocess"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load obs_session postprocess")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    _POSTPROCESS_MODULE = mod
    return mod


@dataclass
class SegmentEntry:
    index: int
    started_utc: str
    stopped_utc: str | None = None
    file_path: str | None = None
    session_file_path: str | None = None
    note: str = ""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_local_clock(value: str | None) -> str:
    if not value:
        return "-"
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%H:%M:%S")
    except ValueError:
        return value


def _has_openai_api_key() -> bool:
    """Match postprocess / Voice Scribe: non-empty key only."""
    if _VOICE_SCRIBE_KEY_FILE.exists():
        try:
            if _VOICE_SCRIBE_KEY_FILE.read_text(encoding="utf-8").strip():
                return True
        except OSError:
            pass
    if _ENV_FILE.exists():
        try:
            for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("OPENAI_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return True
        except OSError:
            pass
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())



class OBSSessionPlugin(Plugin):
    name = "OBS"
    version = "0.4.0"
    description = "Unified OBS mode — scene switching, recording, session diary, MIDI feedback"
    mode_name = MODE_NAME

    def __init__(self):
        self._active = False

        self.host = "127.0.0.1"
        self.port = 4455
        self.password = ""
        self.scene_screen = "MM_Screen"
        self.scene_camera = "MM_Camera"
        self.scene_pip = "MM_ScreenPiP"
        self.scene_cam_app = ""
        self.right_screen_source = "Right Screen"  # must match OBS input name exactly
        self.camera_source = "WebCam"              # must match OBS input name exactly
        self.mic_source = "Mic/Aux"                # must match OBS input name exactly
        self.desktop_audio_source = "Desktop Audio"
        self.aux_audio_source = ""
        self.output_dir = ""
        self.postprocess_stitch = True
        self.postprocess_transcript = False
        self.postprocess_open_folder = True
        self._slot_map: dict[int, str | None] = dict(DEFAULT_SLOT_MAP)

        self.connected = False
        self.session_state = "idle"
        self.recording = False
        self.segments_count = 0
        self._last_action = "Waiting"
        self._connection_detail = ""

        self._obs = None
        self._session_folder: Path | None = None
        self._segments: list[SegmentEntry] = []
        self._record_paths: queue.Queue[str] = queue.Queue()
        self._latest_record_path: str | None = None
        self._last_poll = 0.0
        self._pending_segment_start: str | None = None
        self._record_cb_registered = False
        self._record_dir_saved: str | None = None
        self._record_dir_overridden = False
        self._postprocess_running = False
        self._segment_output_path: str | None = None
        self.replay_dir = ""
        self.replay_buffer_active = False
        self._replay_cb_registered = False
        self._replay_save_count = 0
        self._last_replay_path: str | None = None
        self._obs_scene_list: list[str] = []
        self._obs_scene_list_ts: float = 0.0

    # -- lifecycle ---------------------------------------------------------

    def _get_obs(self):
        if self._obs is None:
            try:
                parent = Path(__file__).resolve().parents[2]
                if str(parent) not in sys.path:
                    sys.path.insert(0, str(parent))
                from obs_controller import OBSController

                self._obs = OBSController(
                    host=self.host, port=int(self.port), password=self.password
                )
            except Exception as exc:
                log.exception("OBS Session: failed to import OBSController: %s", exc)
                self._last_action = f"OBS controller error: {exc}"
                return None
        else:
            self._obs.host = self.host
            self._obs.port = int(self.port)
            self._obs.password = self.password
        return self._obs

    def _fetch_obs_scenes(self, force: bool = False) -> list[str]:
        now = time.monotonic()
        if not force and now - self._obs_scene_list_ts < 3.0 and self._obs_scene_list:
            return self._obs_scene_list
        obs = self._get_obs()
        if not obs or not obs.connected:
            return self._obs_scene_list
        obs._refresh_state()
        self._obs_scene_list = obs.scene_names
        self._obs_scene_list_ts = now
        return self._obs_scene_list

    def on_load(self, config: dict) -> None:
        saved = settings.get(SETTINGS_KEY, {})

        def _m(key, default):
            return saved.get(key, config.get(key, default))

        self.host = str(_m("host", self.host)).strip()
        self.port = int(_m("port", self.port))
        self.password = str(_m("password", self.password))
        self.scene_screen = str(_m("scene_screen", self.scene_screen)).strip()
        self.scene_camera = str(_m("scene_camera", self.scene_camera)).strip()
        self.scene_pip = str(_m("scene_pip", self.scene_pip)).strip()
        self.scene_cam_app = str(_m("scene_cam_app", self.scene_cam_app)).strip()
        self.right_screen_source = str(_m("right_screen_source", self.right_screen_source)).strip()
        self.camera_source = str(_m("camera_source", self.camera_source)).strip()
        self.mic_source = str(_m("mic_source", self.mic_source)).strip()
        self.desktop_audio_source = str(_m("desktop_audio_source", self.desktop_audio_source)).strip()
        self.aux_audio_source = str(_m("aux_audio_source", self.aux_audio_source)).strip()
        self.output_dir = str(_m("output_dir", self.output_dir)).strip()
        self.postprocess_stitch = bool(_m("postprocess_stitch", self.postprocess_stitch))
        self.postprocess_transcript = bool(_m("postprocess_transcript", self.postprocess_transcript))
        self.postprocess_open_folder = bool(_m("postprocess_open_folder", self.postprocess_open_folder))
        self.replay_dir = str(_m("replay_dir", self.replay_dir)).strip()

        saved_slots = saved.get("pad_slots")
        if isinstance(saved_slots, dict):
            for note in DEFAULT_NOTE_ORDER:
                key = str(note)
                if key in saved_slots:
                    val = saved_slots[key]
                    self._slot_map[note] = val if val in PAD_ACTION_IDS else None
                else:
                    self._slot_map[note] = DEFAULT_SLOT_MAP.get(note)

        self._persist_settings()

        self.connected = False
        self.session_state = "idle"
        self.recording = False
        self.segments_count = 0
        self._last_action = "Configured; use Connect when OBS is running"
        self._connection_detail = ""

    def on_unload(self) -> None:
        self._persist_settings()
        self._unregister_obs_callbacks()
        self._restore_obs_record_directory()
        obs = self._get_obs()
        if obs and obs.connected:
            try:
                obs.disconnect()
            except Exception as exc:
                log.warning("OBS Session disconnect: %s", exc)
        self.connected = False

    def on_mode_changed(self, mode_name: str) -> None:
        self._active = mode_name == self.mode_name
        self._refresh_ui()

    def set_owned_notes(self, notes: set[int]) -> None:
        self._active = bool(notes)
        if not notes:
            self._slot_map = dict(DEFAULT_SLOT_MAP)
            self._rebuild_pad_grid()
            return
        old_sorted = sorted(self._slot_map.keys())
        vals = [self._slot_map.get(k) for k in old_sorted]
        new_sorted = sorted(notes)
        new_map: dict[int, str | None] = {}
        for i, note in enumerate(new_sorted):
            new_map[note] = vals[i] if i < len(vals) else None
        self._slot_map = new_map
        self._rebuild_pad_grid()

    # -- MIDI hooks --------------------------------------------------------

    def _play_cue(self, cue_id: str) -> None:
        fb = getattr(self, "_runtime_services", {}).get("feedback")
        if fb:
            try:
                fb.emit(cue_id)
            except Exception:
                pass

    def _note_to_action(self, note: int) -> str | None:
        return self._slot_map.get(note)

    def _dispatch_action(self, action: str) -> None:
        """Execute a pad action. Called from background thread to avoid UI freeze."""
        if action == "scene_screen":
            self._action_switch_scene(self.scene_screen)
        elif action == "scene_camera":
            self._action_switch_scene(self.scene_camera)
        elif action == "scene_pip":
            self._action_switch_scene(self.scene_pip)
        elif action == "scene_cam_app":
            self._action_switch_scene(self.scene_cam_app)
        elif action == "mute_mic":
            self._action_toggle_mute_mic()
        elif action == "mute_desktop":
            self._action_toggle_mute_source(self.desktop_audio_source)
        elif action == "mute_aux":
            self._action_toggle_mute_source(self.aux_audio_source)
        elif action == "session":
            if self.session_state == "running":
                self._action_stop_session()
            else:
                self._action_start_session()
        elif action == "record":
            if self.session_state == "running":
                self._action_toggle_record()
            else:
                self._action_toggle_record_simple()
        elif action == "save_replay":
            self._action_save_replay()

    def on_pad_press(self, note: int, velocity: int) -> bool:
        if not self._active:
            return False
        if note not in self._slot_map:
            return False
        action = self._note_to_action(note)
        if action is None:
            return False
        # Run action in background thread to avoid blocking UI
        threading.Thread(
            target=self._dispatch_action, args=(action,), daemon=True
        ).start()
        return True

    def on_pad_release(self, note: int) -> bool:
        return self._active and self._note_to_action(note) is not None

    def poll(self) -> None:
        now = time.monotonic()
        if now - self._last_poll < 0.25:
            self._refresh_ui()
            return
        self._last_poll = now

        obs = self._get_obs()
        if obs and obs.connected:
            if not obs.ping():
                self.connected = False
                self._connection_detail = "Connection lost"
                log.warning("OBS Session: ping failed; marked disconnected")
            else:
                obs.refresh_recording_state()
                self.recording = obs.is_recording
                self.replay_buffer_active = obs.is_replay_buffer_active
        elif obs and not obs.connected and self.connected:
            self.connected = False
            self._connection_detail = "Connection lost"
            log.warning("OBS Session: connection lost")

        self._refresh_ui()

    # -- status + pad labels ------------------------------------------------

    def _dynamic_label(self, action_id: str) -> str:
        in_session = self.session_state == "running"
        if action_id == "session":
            return "Stop Session" if in_session else "Start Session"
        if action_id == "record":
            if in_session and self.recording:
                return "Stop Segment"
            if in_session:
                return "Record Segment"
            return "Stop Rec" if self.recording else "Record"
        if action_id == "save_replay":
            return "Save Replay"
        for a in PAD_ACTIONS:
            if a["id"] == action_id:
                return a["label"]
        return action_id

    def get_action_catalog(self) -> list[dict]:
        return list(_ACTION_CATALOG)

    def get_pad_labels(self) -> dict[int, str]:
        if not self._active:
            return {}
        labels: dict[int, str] = {}
        for note, action_id in self._slot_map.items():
            if action_id:
                labels[note] = self._dynamic_label(action_id)
        return labels

    def get_status(self) -> tuple[str, tuple[int, int, int]] | None:
        if not self._active:
            return None
        connection = "OK" if self.connected else "Off"
        recording = "REC" if self.recording else "Standby"
        replay = " | Replay:ON" if self.replay_buffer_active else ""
        obs = self._get_obs()
        scene = obs.current_scene if obs and obs.connected else "?"
        text = (
            f"OBS | {connection} | Scene: {scene} | Session: {self._ui_session_state().title()} | "
            f"{recording} | Segments: {self.segments_count}{replay}"
        )
        if self.recording:
            color = _STATUS_RECORDING
        elif self.connected:
            color = _CONNECTED_OK
        else:
            color = _CONNECTED_BAD
        return text, color

    # -- right sidebar ------------------------------------------------------

    def build_properties(self, parent_tag: str) -> None:
        import dearpygui.dearpygui as dpg

        dpg.add_text("OBS Settings", parent=parent_tag, color=(110, 190, 255))
        dpg.add_text(
            "Scenes, sources, session output, and postprocessing.",
            parent=parent_tag,
            wrap=260,
            color=(120, 120, 140),
        )
        dpg.add_separator(parent=parent_tag)
        dpg.add_spacer(height=6, parent=parent_tag)

        dpg.add_text("OBS Connection", parent=parent_tag, color=(150, 150, 165))
        dpg.add_input_text(
            tag="obs_session_host",
            parent=parent_tag,
            default_value=self.host,
            hint="127.0.0.1",
            width=-1,
            callback=lambda sender, app_data: self._save_text("host", app_data),
        )
        with dpg.group(horizontal=True, parent=parent_tag):
            dpg.add_input_int(
                tag="obs_session_port",
                default_value=int(self.port),
                width=120,
                min_value=1,
                min_clamped=True,
                callback=lambda sender, app_data: self._save_port(app_data),
            )
            dpg.add_input_text(
                tag="obs_session_password",
                default_value=self.password,
                hint="OBS password",
                password=True,
                width=-1,
                callback=lambda sender, app_data: self._save_text("password", app_data),
            )

        dpg.add_spacer(height=8, parent=parent_tag)
        dpg.add_text("Scenes", parent=parent_tag, color=(150, 150, 165))
        scene_items = self._fetch_obs_scenes()
        for field, label, tag_suffix in [
            ("scene_screen", "Screen + PiP", "screen"),
            ("scene_camera", "Camera", "camera"),
            ("scene_pip", "PiP", "pip"),
            ("scene_cam_app", "Camera + App", "cam_app"),
        ]:
            dpg.add_text(label, parent=parent_tag, color=(120, 120, 140))
            dpg.add_combo(
                tag=f"obs_session_scene_{tag_suffix}_combo",
                items=scene_items,
                default_value=getattr(self, field),
                width=-1,
                parent=parent_tag,
                callback=lambda s, v, f=field: self._save_text(f, v),
            )
        dpg.add_button(
            label="Refresh scene list",
            parent=parent_tag,
            width=-1,
            callback=lambda: self._refresh_scene_combos(),
        )

        dpg.add_spacer(height=8, parent=parent_tag)
        dpg.add_text("Capture Sources", parent=parent_tag, color=(150, 150, 165))
        dpg.add_input_text(
            tag="obs_session_right_source",
            parent=parent_tag,
            default_value=self.right_screen_source,
            hint="Display or screen capture source name",
            width=-1,
            callback=lambda sender, app_data: self._save_text("right_screen_source", app_data),
        )
        dpg.add_input_text(
            tag="obs_session_camera_source",
            parent=parent_tag,
            default_value=self.camera_source,
            hint="Camera source name",
            width=-1,
            callback=lambda sender, app_data: self._save_text("camera_source", app_data),
        )
        dpg.add_input_text(
            tag="obs_session_mic_source",
            parent=parent_tag,
            default_value=self.mic_source,
            hint="Microphone input source name",
            width=-1,
            callback=lambda sender, app_data: self._save_text("mic_source", app_data),
        )

        dpg.add_spacer(height=8, parent=parent_tag)
        dpg.add_text("Audio Sources", parent=parent_tag, color=(150, 150, 165))
        audio_items = self._fetch_obs_audio_inputs()
        for field, label, tag_suffix in [
            ("desktop_audio_source", "Desktop Audio", "desktop"),
            ("mic_source", "Microphone", "mic_audio"),
            ("aux_audio_source", "AUX (optional)", "aux"),
        ]:
            dpg.add_text(label, parent=parent_tag, color=(120, 120, 140))
            dpg.add_combo(
                tag=f"obs_session_audio_{tag_suffix}_combo",
                items=audio_items,
                default_value=getattr(self, field),
                width=-1,
                parent=parent_tag,
                callback=lambda s, v, f=field: self._save_text(f, v),
            )

        dpg.add_spacer(height=8, parent=parent_tag)
        dpg.add_text("Output And Postprocessing", parent=parent_tag, color=(150, 150, 165))
        dpg.add_input_text(
            tag="obs_session_output_dir",
            parent=parent_tag,
            default_value=self.output_dir,
            hint=str(Path.home() / "Videos" / "OBS-Sessions"),
            width=-1,
            callback=lambda sender, app_data: self._save_text("output_dir", app_data),
        )
        dpg.add_text("Replay output", parent=parent_tag, color=(120, 120, 140))
        dpg.add_input_text(
            tag="obs_session_replay_dir",
            parent=parent_tag,
            default_value=self.replay_dir,
            hint=str(Path.home() / "Videos" / "OBS-Replays"),
            width=-1,
            callback=lambda sender, app_data: self._save_text("replay_dir", app_data),
        )
        dpg.add_checkbox(
            tag="obs_session_postprocess_stitch",
            label="Enable clip stitching step",
            parent=parent_tag,
            default_value=self.postprocess_stitch,
            callback=lambda sender, app_data: self._save_bool("postprocess_stitch", app_data),
        )
        dpg.add_checkbox(
            tag="obs_session_postprocess_transcript",
            label="Enable transcript step",
            parent=parent_tag,
            default_value=self.postprocess_transcript,
            callback=lambda sender, app_data: self._save_bool("postprocess_transcript", app_data),
        )
        dpg.add_checkbox(
            tag="obs_session_postprocess_open_folder",
            label="Reveal session folder when done",
            parent=parent_tag,
            default_value=self.postprocess_open_folder,
            callback=lambda sender, app_data: self._save_bool("postprocess_open_folder", app_data),
        )
        dpg.add_text(
            "",
            tag="obs_session_prereq_settings_hint",
            parent=parent_tag,
            wrap=260,
            color=_STATUS_WARN,
        )

        dpg.add_spacer(height=10, parent=parent_tag)
        dpg.add_separator(parent=parent_tag)
        dpg.add_spacer(height=6, parent=parent_tag)

        dpg.add_text("Current Status", parent=parent_tag, color=(150, 150, 165))
        self._add_status_row(parent_tag, "Connection", "obs_session_status_connection")
        self._add_status_row(parent_tag, "Session", "obs_session_status_session")
        self._add_status_row(parent_tag, "Recording", "obs_session_status_recording")
        self._add_status_row(parent_tag, "Segments", "obs_session_status_segments")
        self._add_status_row(parent_tag, "Session folder", "obs_session_status_folder", wrap=160)

        dpg.add_spacer(height=10, parent=parent_tag)
        dpg.add_text("Pad Mapping", parent=parent_tag, color=(150, 150, 165))
        for note in sorted(self._slot_map.keys()):
            action_id = self._slot_map.get(note)
            pad_name = self._note_label(note)
            action_name = self._dynamic_label(action_id) if action_id else "(free)"
            dpg.add_text(f"{pad_name}  {action_name}", parent=parent_tag)
        dpg.add_text(
            "Drag and drop pads in the main panel to rearrange.",
            parent=parent_tag,
            wrap=260,
            color=(120, 120, 140),
        )

        dpg.add_spacer(height=10, parent=parent_tag)
        dpg.add_text(
            "",
            tag="obs_session_props_last_action",
            parent=parent_tag,
            wrap=260,
            color=(120, 120, 140),
        )
        dpg.add_text(
            "",
            tag="obs_session_props_postprocess_hint",
            parent=parent_tag,
            wrap=260,
            color=(255, 200, 120),
        )

        self._refresh_ui()

    def _action_for_id(self, action_id: str) -> dict | None:
        for a in PAD_ACTIONS:
            if a["id"] == action_id:
                return a
        return None

    def _refresh_scene_combos(self) -> None:
        import dearpygui.dearpygui as dpg
        scenes = self._fetch_obs_scenes(force=True)
        for suffix in ("screen", "camera", "pip", "cam_app"):
            tag = f"obs_session_scene_{suffix}_combo"
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, items=scenes)

    def _fetch_obs_audio_inputs(self) -> list[str]:
        obs = self._get_obs()
        if not obs or not obs.connected:
            return []
        return obs.get_audio_input_names()

    def _exec_action(self, action_id: str) -> None:
        if action_id == "scene_screen":
            self._action_switch_scene(self.scene_screen)
        elif action_id == "scene_camera":
            self._action_switch_scene(self.scene_camera)
        elif action_id == "scene_pip":
            self._action_switch_scene(self.scene_pip)
        elif action_id == "scene_cam_app":
            self._action_switch_scene(self.scene_cam_app)
        elif action_id == "mute_mic":
            self._action_toggle_mute_mic()
        elif action_id == "mute_desktop":
            self._action_toggle_mute_source(self.desktop_audio_source)
        elif action_id == "mute_aux":
            self._action_toggle_mute_source(self.aux_audio_source)
        elif action_id == "session":
            if self.session_state == "running":
                self._action_stop_session()
            else:
                self._action_start_session()
        elif action_id == "record":
            if self.session_state == "running":
                self._action_toggle_record()
            else:
                self._action_toggle_record_simple()
        elif action_id == "save_replay":
            self._action_save_replay()

    def _swap_pads(self, note_a: int, note_b: int) -> None:
        if note_a == note_b:
            return
        self._slot_map[note_a], self._slot_map[note_b] = (
            self._slot_map[note_b],
            self._slot_map[note_a],
        )
        self._persist_settings()
        self._rebuild_pad_grid()

    def _rebuild_pad_grid(self) -> None:
        if os.environ.get("MACROPAD_HEADLESS"):
            return
        try:
            import dearpygui.dearpygui as dpg
        except Exception:
            return
        panel = "obs_session_pad_grid"
        if not dpg.does_item_exist(panel):
            return
        dpg.delete_item(panel, children_only=True)
        self._build_pad_grid_contents(dpg, panel)
        self._refresh_ui()

    def _grid_rows(self) -> tuple[list[int], list[int]]:
        notes = sorted(self._slot_map.keys())
        if len(notes) <= 4:
            return [], notes
        return notes[4:8], notes[0:4]

    def _note_label(self, note: int) -> str:
        top_row, bot_row = self._grid_rows()
        if note in top_row:
            return f"Pad {len(bot_row) + top_row.index(note) + 1}"
        if note in bot_row:
            return f"Pad {bot_row.index(note) + 1}"
        return f"N{note}"

    def _pad_number(self, note: int) -> int:
        top_row, bot_row = self._grid_rows()
        if note in top_row:
            return len(bot_row) + top_row.index(note) + 1
        if note in bot_row:
            return bot_row.index(note) + 1
        idx = sorted(self._slot_map.keys()).index(note)
        return idx + 1

    def _build_pad_grid_contents(self, dpg, parent: str) -> None:
        top_row, bot_row = self._grid_rows()
        rows: list[list[int]] = [r for r in (bot_row, top_row) if r]

        def _make_drop_cb(target_note):
            def cb(sender, app_data):
                source_note = app_data
                if isinstance(source_note, int):
                    self._swap_pads(source_note, target_note)
            return cb

        def _make_exec_cb(note):
            def cb():
                aid = self._slot_map.get(note)
                if aid:
                    self._exec_action(aid)
            return cb

        for row_notes in rows:
            with dpg.group(horizontal=True, parent=parent):
                for note in row_notes:
                    action_id = self._slot_map.get(note)
                    info = self._action_for_id(action_id) if action_id else None
                    pad_num = self._pad_number(note)
                    color = info["color"] if info else (60, 60, 75)
                    title = self._dynamic_label(action_id) if action_id else "—"
                    desc = info["desc"] if info else ""
                    card_tag = f"obs_pad_card_{note}"
                    btn_tag = f"obs_pad_btn_{note}"
                    lbl_tag = f"obs_pad_lbl_{note}"
                    drag_tag = f"obs_pad_drag_{note}"

                    with dpg.child_window(
                        tag=card_tag,
                        width=160,
                        height=100,
                        border=True,
                        drop_callback=_make_drop_cb(note),
                        payload_type="obs_pad",
                    ):
                        with dpg.group(horizontal=True):
                            dpg.add_button(
                                tag=drag_tag,
                                label=title if action_id else "—",
                                width=130,
                                small=True,
                                callback=_make_exec_cb(note) if action_id else None,
                            )
                            with dpg.drag_payload(
                                parent=drag_tag,
                                drag_data=note,
                                payload_type="obs_pad",
                            ):
                                dpg.add_text(f"Pad {pad_num}: {title}")
                            dpg.add_text(
                                str(pad_num),
                                color=(45, 45, 55),
                            )
                        dpg.add_text(title, tag=lbl_tag, color=(230, 232, 238))
                        if desc:
                            dpg.add_text(desc, wrap=140, color=(120, 120, 140))
                        if action_id:
                            dpg.add_button(
                                tag=btn_tag,
                                label=title,
                                width=-1,
                                small=True,
                                callback=_make_exec_cb(note),
                            )
            dpg.add_spacer(height=4, parent=parent)

    def build_ui(self, parent_tag: str) -> None:
        import dearpygui.dearpygui as dpg

        dpg.add_text("OBS Workflow", parent=parent_tag, color=(110, 190, 255))
        dpg.add_text(
            "Drag pads to rearrange assignments. "
            "Session and Record pads change behavior depending on whether a diary session is active.",
            parent=parent_tag,
            wrap=720,
            color=(120, 120, 140),
        )
        dpg.add_spacer(height=6, parent=parent_tag)

        with dpg.child_window(parent=parent_tag, height=136, border=True):
            with dpg.group(horizontal=True):
                with dpg.group():
                    dpg.add_text("Status", color=(150, 150, 165))
                    self._add_status_row(parent_tag=None, label="Scene", value_tag="obs_session_overview_scene")
                    self._add_status_row(parent_tag=None, label="State", value_tag="obs_session_overview_state")
                    self._add_status_row(parent_tag=None, label="Recording", value_tag="obs_session_overview_recording")
                    self._add_status_row(parent_tag=None, label="Segments", value_tag="obs_session_overview_segments")
                    self._add_status_row(parent_tag=None, label="Session folder", value_tag="obs_session_overview_folder", wrap=260)
                dpg.add_spacer(width=30)
                with dpg.group():
                    dpg.add_text("Prerequisites", color=(150, 150, 165))
                    self._add_status_row(parent_tag=None, label="OBS", value_tag="obs_session_prereq_obs", wrap=220)
                    self._add_status_row(parent_tag=None, label="ffmpeg", value_tag="obs_session_prereq_ffmpeg", wrap=220)
                    self._add_status_row(parent_tag=None, label="API key", value_tag="obs_session_prereq_api", wrap=220)
                    dpg.add_spacer(height=4)
                    with dpg.group(horizontal=True):
                        dpg.add_button(
                            tag="obs_session_btn_connect",
                            label="Connect to OBS",
                            width=130,
                            callback=lambda: self._action_connect(),
                        )
                        dpg.add_button(
                            tag="obs_session_btn_disconnect",
                            label="Disconnect",
                            width=110,
                            callback=lambda: self._action_disconnect(),
                        )
                    with dpg.group(horizontal=True):
                        dpg.add_button(
                            tag="obs_session_btn_replay_toggle",
                            label="Start Replay Buffer",
                            width=160,
                            callback=lambda: self._action_toggle_replay_buffer(),
                        )
                        dpg.add_text("", tag="obs_session_replay_status", color=_STATUS_IDLE)

        dpg.add_spacer(height=6, parent=parent_tag)
        dpg.add_child_window(
            tag="obs_session_pad_grid",
            parent=parent_tag,
            height=220,
            border=False,
        )
        self._build_pad_grid_contents(dpg, "obs_session_pad_grid")

        dpg.add_spacer(height=6, parent=parent_tag)
        dpg.add_text("Segment Timeline", parent=parent_tag, color=(150, 150, 165))
        dpg.add_text(
            "Each segment appears here with start/stop times and save status so it is obvious what made it into the session.",
            parent=parent_tag,
            wrap=720,
            color=(120, 120, 140),
        )
        dpg.add_child_window(
            tag="obs_session_segments_panel",
            parent=parent_tag,
            height=250,
            border=True,
        )
        dpg.add_spacer(height=8, parent=parent_tag)
        dpg.add_text("Sessions Catalog", parent=parent_tag, color=(150, 150, 165))
        dpg.add_text(
            "Recent sessions under the output folder (newest first, up to 20). Rescans every few seconds; use Refresh to update now.",
            parent=parent_tag,
            wrap=720,
            color=(120, 120, 140),
        )
        with dpg.group(horizontal=True, parent=parent_tag):
            dpg.add_button(
                label="Refresh",
                width=100,
                callback=lambda: self._catalog_refresh_clicked(),
            )
        dpg.add_child_window(
            tag="obs_session_catalog_panel",
            parent=parent_tag,
            height=220,
            border=True,
        )
        dpg.add_spacer(height=8, parent=parent_tag)
        dpg.add_text("Activity", parent=parent_tag, color=(150, 150, 165))
        dpg.add_text(
            "",
            tag="obs_session_last_action",
            parent=parent_tag,
            wrap=720,
            color=(120, 120, 140),
        )
        dpg.add_text(
            "",
            tag="obs_session_postprocess_hint",
            parent=parent_tag,
            wrap=720,
            color=(255, 200, 120),
        )
        dpg.add_spacer(height=6, parent=parent_tag)
        dpg.add_button(
            label="Reset local session state",
            parent=parent_tag,
            width=220,
            callback=self._action_reset_state,
        )

    # -- OBS callbacks (recv thread) ---------------------------------------

    def _on_record_state_changed(self, data) -> None:
        try:
            active = getattr(data, "output_active", None)
            if active is None and isinstance(data, dict):
                active = data.get("outputActive")
            if active is not None:
                self.recording = bool(active)
        except Exception as exc:
            log.debug("RecordStateChanged parse: %s", exc)

    def _on_record_file_changed(self, data) -> None:
        try:
            path = getattr(data, "output_path", None)
            if path is None and isinstance(data, dict):
                path = data.get("outputPath") or data.get("newOutputPath")
            if path:
                p = str(path)
                self._latest_record_path = p
                self._record_paths.put(p)
                if self.session_state == "running" and self._pending_segment_start is not None:
                    self._segment_output_path = p
        except Exception as exc:
            log.debug("RecordFileChanged parse: %s", exc)

    def _on_replay_buffer_saved(self, data) -> None:
        try:
            path = getattr(data, "saved_replay_path", None)
            if path is None and isinstance(data, dict):
                path = data.get("savedReplayPath")
            if not path:
                obs = self._get_obs()
                if obs:
                    path = obs.get_last_replay_buffer_replay()
            if path:
                copied = self._copy_replay_to_folder(str(path))
                self._replay_save_count += 1
                self._last_replay_path = copied or str(path)
                self._last_action = f"Replay saved: {self._last_replay_path}"
                self._play_cue("action.toggle_on")
                log.info("OBS replay saved: %s -> %s", path, copied)
            else:
                self._last_action = "Replay buffer saved but path not reported"
                log.warning("OBS replay saved but no path in event data")
        except Exception as exc:
            log.exception("OBS replay callback error: %s", exc)

    def _resolve_replay_dir(self) -> Path:
        if self.replay_dir.strip():
            p = Path(self.replay_dir).expanduser()
        else:
            p = Path.home() / "Videos" / "OBS-Replays"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _copy_replay_to_folder(self, source_path: str) -> str | None:
        src = Path(source_path)
        if not src.exists():
            return None
        try:
            replay_dir = self._resolve_replay_dir()
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            ext = src.suffix or ".mkv"
            dst = replay_dir / f"replay_{stamp}{ext}"
            counter = 2
            while dst.exists():
                dst = replay_dir / f"replay_{stamp}_{counter}{ext}"
                counter += 1
            if dst.resolve() == src.resolve():
                return str(dst)
            shutil.copy2(src, dst)
            return str(dst)
        except OSError as exc:
            log.warning("OBS replay copy failed: %s", exc)
            return None

    def _register_obs_callbacks(self) -> None:
        obs = self._get_obs()
        if not obs or not obs.connected:
            return
        try:
            obs.unregister_record_state_callback(self._on_record_state_changed)
        except Exception:
            pass
        obs.register_record_state_callback(self._on_record_state_changed)
        try:
            obs.unregister_record_file_callback(self._on_record_file_changed)
        except Exception:
            pass
        obs.register_record_file_callback(self._on_record_file_changed)
        self._record_cb_registered = True
        try:
            obs.unregister_replay_buffer_callback(self._on_replay_buffer_saved)
        except Exception:
            pass
        obs.register_replay_buffer_callback(self._on_replay_buffer_saved)
        self._replay_cb_registered = True

    def _unregister_obs_callbacks(self) -> None:
        obs = self._get_obs()
        if obs and self._record_cb_registered:
            try:
                obs.unregister_record_state_callback(self._on_record_state_changed)
            except Exception:
                pass
            try:
                obs.unregister_record_file_callback(self._on_record_file_changed)
            except Exception:
                pass
            try:
                obs.unregister_replay_buffer_callback(self._on_replay_buffer_saved)
            except Exception:
                pass
        self._record_cb_registered = False
        self._replay_cb_registered = False

    # -- actions -----------------------------------------------------------

    def _action_switch_scene(self, scene_name: str) -> None:
        if not self._ensure_connected():
            self._refresh_ui()
            return
        obs = self._get_obs()
        if obs and obs.switch_scene(scene_name):
            self._last_action = f"Switched to scene: {scene_name}"
            self._play_cue("action.navigation")
        else:
            self._last_action = f"Failed to switch to scene: {scene_name}"
        self._refresh_ui()

    def _action_toggle_mute_mic(self) -> None:
        if not self._ensure_connected():
            self._refresh_ui()
            return
        obs = self._get_obs()
        if obs and obs.toggle_source_mute(self.mic_source):
            self._last_action = f"Toggled mute: {self.mic_source}"
            self._play_cue("action.toggle_on")
        else:
            self._last_action = f"Mute toggle failed: {self.mic_source}"
        self._refresh_ui()

    def _action_toggle_mute_source(self, source_name: str) -> None:
        if not source_name:
            self._last_action = "Audio source not configured"
            self._refresh_ui()
            return
        if not self._ensure_connected():
            self._refresh_ui()
            return
        obs = self._get_obs()
        if obs and obs.toggle_source_mute(source_name):
            self._last_action = f"Toggled mute: {source_name}"
            self._play_cue("action.toggle_on")
        else:
            self._last_action = f"Mute toggle failed: {source_name}"
        self._refresh_ui()

    def _action_save_replay(self) -> None:
        if not self._ensure_connected():
            self._refresh_ui()
            return
        obs = self._get_obs()
        if not obs:
            return
        if not obs.get_replay_buffer_status():
            self._last_action = "Replay Buffer not active. Enable it first."
            self._refresh_ui()
            return
        old_count = self._replay_save_count
        if obs.save_replay_buffer():
            self._last_action = "Saving replay buffer clip..."
            self._play_cue("action.navigation")
            self._refresh_ui()
            # Fallback: if callback doesn't fire within 2s, fetch path manually
            time.sleep(2.0)
            if self._replay_save_count == old_count:
                path = obs.get_last_replay_buffer_replay()
                if path:
                    copied = self._copy_replay_to_folder(str(path))
                    self._replay_save_count += 1
                    self._last_replay_path = copied or str(path)
                    self._last_action = f"Replay saved (fallback): {self._last_replay_path}"
                    log.info("OBS replay saved via fallback: %s", path)
                else:
                    self._last_action = "Replay saved by OBS but path unknown"
        else:
            self._last_action = "Save replay buffer failed"
        self._refresh_ui()

    def _action_toggle_replay_buffer(self) -> None:
        if not self._ensure_connected():
            self._refresh_ui()
            return
        obs = self._get_obs()
        if not obs:
            return
        if obs.toggle_replay_buffer():
            self.replay_buffer_active = obs.is_replay_buffer_active
            state = "started" if self.replay_buffer_active else "stopped"
            self._last_action = f"Replay Buffer {state}"
            self._play_cue("action.toggle_on" if self.replay_buffer_active else "action.toggle_off")
        else:
            self._last_action = "Toggle Replay Buffer failed -- enable it in OBS Settings -> Output first"
        self._refresh_ui()

    def _action_toggle_record_simple(self) -> None:
        """Simple recording toggle (no session/segments)."""
        if not self._ensure_connected():
            self._refresh_ui()
            return
        obs = self._get_obs()
        if obs is None:
            return
        obs.refresh_recording_state()
        was_recording = obs.is_recording
        if obs.toggle_recording():
            self.recording = obs.is_recording
            if was_recording:
                self._last_action = "Recording stopped"
                self._play_cue("action.toggle_off")
            else:
                self._last_action = "Recording started"
                self._play_cue("action.toggle_on")
        else:
            self._last_action = "Toggle recording failed"
        self._refresh_ui()

    def _action_connect(self) -> None:
        obs = self._get_obs()
        if obs is None:
            return
        self._unregister_obs_callbacks()
        if obs.connected:
            try:
                obs.disconnect()
            except Exception:
                pass
        ok = obs.connect()
        self.connected = ok
        if ok:
            self._register_obs_callbacks()
            ver = obs.obs_version or "?"
            self._connection_detail = f"OBS {ver}"
            self._last_action = f"Connected ({self._connection_detail})"
            log.info("OBS Session connected to %s:%s", self.host, self.port)
            self._fetch_obs_scenes(force=True)
            self._refresh_scene_combos()
            if self.session_state == "running" and not obs.is_recording:
                self._last_action += " | WARNING: Previous session may not have stopped cleanly. Use 'Reset local session state' to clear."
        else:
            err = obs.last_connect_error or "unknown error"
            self._connection_detail = err
            self._last_action = f"Connect failed: {err}"
            log.warning("OBS Session connect failed: %s", err)
        self._refresh_ui()

    def _action_disconnect(self) -> None:
        self._restore_obs_record_directory()
        obs = self._get_obs()
        self._unregister_obs_callbacks()
        if obs and obs.connected:
            try:
                obs.disconnect()
            except Exception as exc:
                log.warning("OBS disconnect: %s", exc)
        self.connected = False
        self.recording = False
        self._connection_detail = ""
        self._last_action = "Disconnected"
        self._refresh_ui()

    def _ensure_connected(self) -> bool:
        obs = self._get_obs()
        if obs is None:
            return False
        if obs.connected:
            self.connected = True
            self._register_obs_callbacks()
            return True
        ok = obs.connect()
        self.connected = ok
        if ok:
            self._register_obs_callbacks()
            self._connection_detail = obs.obs_version or "OBS"
            log.info("OBS Session auto-connected for action")
        else:
            self._connection_detail = obs.last_connect_error or "error"
            self._last_action = f"Connect failed: {self._connection_detail}"
        return ok

    def _resolve_output_base(self) -> Path:
        if self.output_dir.strip():
            return Path(self.output_dir).expanduser()
        return Path.home() / "Videos" / "OBS-Sessions"

    def _restore_obs_record_directory(self) -> None:
        if not self._record_dir_overridden:
            return
        obs = self._get_obs()
        if not obs or not obs.connected:
            return
        prev = self._record_dir_saved
        if not prev:
            log.warning("OBS Session: no saved record directory to restore")
            self._record_dir_overridden = False
            self._record_dir_saved = None
            return
        ok, msg = obs.set_record_directory(prev)
        if ok:
            log.info("OBS Session: restored recording directory to %s", prev)
            self._record_dir_overridden = False
            self._record_dir_saved = None
        else:
            log.warning("OBS Session: restore recording directory failed: %s", msg)

    def _postprocess_worker(self) -> None:
        pp = _get_postprocess()

        folder = self._session_folder
        try:
            if folder and folder.is_dir():
                result = pp.run_session_postprocess(
                    folder,
                    do_stitch=self.postprocess_stitch,
                    do_transcript=self.postprocess_transcript,
                    do_open_folder=self.postprocess_open_folder,
                    transcription_model="whisper-1",
                    transcript_language=None,
                )
                st = result.stitch.get("status", "?")
                tr = result.transcript.get("status", "?")
                parts = [f"stitch={st}", f"transcript={tr}"]
                if result.final_path:
                    parts.append("final video ready")
                elif result.stitched_path:
                    parts.append("stitched video ready")
                self._last_action = f"Post-process finished — {', '.join(parts)}"
                log.info("OBS Session postprocess complete %s", parts)
            else:
                self._last_action = "Post-process skipped (no session folder)"
        except Exception as exc:
            log.exception("OBS Session postprocess failed: %s", exc)
            self._last_action = f"Post-process error: {exc}"
        finally:
            self._postprocess_running = False
            self.session_state = "idle"

    def _action_start_session(self) -> None:
        if self._postprocess_running or self.session_state == "postprocessing":
            self._last_action = "Wait for post-processing to finish before starting a session"
            self._refresh_ui()
            return
        if self.session_state == "running":
            self._last_action = "Session already running — stop it first or reset state"
            self._refresh_ui()
            return
        if not self._ensure_connected():
            self._refresh_ui()
            return
        obs = self._get_obs()
        assert obs is not None

        obs.refresh_recording_state()
        if obs.is_recording:
            self._last_action = (
                "OBS is already recording. Stop recording first "
                "so segment start/stop stays one segment at a time."
            )
            self._refresh_ui()
            return

        setup_notes: list[str] = []
        obs._refresh_state()
        existing = set(obs.scene_names)
        for slot_label, scene_name in [
            ("screen", self.scene_screen),
            ("camera", self.scene_camera),
            ("pip", self.scene_pip),
            ("cam_app", self.scene_cam_app),
        ]:
            if scene_name and scene_name not in existing:
                self._last_action = f"Scene '{scene_name}' not found in OBS. Create it in OBS or map a different scene."
                self._refresh_ui()
                return
            if scene_name:
                setup_notes.append(f"{slot_label}: {scene_name}")

        if not obs.switch_scene(self.scene_pip):
            self._last_action = f"Could not switch to scene '{self.scene_pip}'"
            self._refresh_ui()
            return

        base = self._resolve_output_base()
        try:
            base.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._last_action = f"Output folder error: {exc}"
            self._refresh_ui()
            return

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._session_folder = base / f"session_{stamp}"
        try:
            self._session_folder.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            self._last_action = f"Session folder error: {exc}"
            self._refresh_ui()
            return

        self._record_dir_saved = obs.get_record_directory()
        rd_ok, rd_msg = obs.set_record_directory(
            str(self._session_folder.resolve())
        )
        if rd_ok:
            self._record_dir_overridden = True
            setup_notes.append(f"Recording directory → {self._session_folder.name}")
        else:
            self._record_dir_overridden = False
            setup_notes.append(
                f"Recording directory unchanged (OBS SetRecordDirectory failed: {rd_msg})"
            )
            log.warning("OBS Session: SetRecordDirectory failed: %s", rd_msg)

        self._segments = []
        self.segments_count = 0
        self._latest_record_path = None
        while True:
            try:
                self._record_paths.get_nowait()
            except queue.Empty:
                break
        self._pending_segment_start = None
        self._segment_output_path = None
        self.session_state = "running"
        self.recording = obs.is_recording
        n_join = "; ".join(setup_notes[:4])
        if len(setup_notes) > 4:
            n_join += "…"
        self._last_action = f"Session started — {self._session_folder.name}. {n_join}"
        log.info(
            "OBS Session started folder=%s setup=%s",
            self._session_folder,
            setup_notes,
        )
        self._play_cue("session.start")
        self._refresh_ui()

    def _drain_record_path(self, timeout: float = 3.0) -> str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                return self._record_paths.get(timeout=0.05)
            except queue.Empty:
                if self._segment_output_path:
                    return self._segment_output_path
        return self._latest_record_path

    def _copy_segment_into_session(self, source_path: str | None, segment: SegmentEntry | None = None) -> str | None:
        """Copy segment file into session folder. If segment is provided, runs async."""
        if not source_path or not self._session_folder:
            return None
        src = Path(source_path)
        if not src.exists():
            return None
        dst = self._session_folder / src.name
        if dst.resolve() == src.resolve():
            if segment:
                segment.session_file_path = str(dst)
            return str(dst)
        if segment:
            segment.note = "copying..."
            threading.Thread(
                target=self._copy_segment_bg, args=(segment, src, dst), daemon=True
            ).start()
            return None
        try:
            shutil.copy2(src, dst)
            return str(dst)
        except OSError as exc:
            log.warning("OBS Session copy segment failed: %s", exc)
            return None

    def _copy_segment_bg(self, segment: SegmentEntry, src: Path, dst: Path) -> None:
        try:
            shutil.copy2(src, dst)
            segment.session_file_path = str(dst)
            segment.note = ""
            log.info("OBS Session segment copied async: %s", dst)
        except OSError as exc:
            segment.note = f"copy failed: {exc}"
            log.warning("OBS Session async copy failed: %s", exc)

    def _action_toggle_record(self) -> None:
        if self.session_state != "running":
            self._last_action = "Start a session first with the Session pad"
            self._refresh_ui()
            return
        if not self._ensure_connected():
            self._refresh_ui()
            return
        obs = self._get_obs()
        assert obs is not None
        obs.refresh_recording_state()

        if not obs.is_recording:
            while True:
                try:
                    self._record_paths.get_nowait()
                except queue.Empty:
                    break
            self._segment_output_path = None
            self._latest_record_path = None
            self._pending_segment_start = _utc_now_iso()
            if not obs.start_recording():
                self._pending_segment_start = None
                self._last_action = "StartRecord failed (check OBS logs / output settings)"
                self._refresh_ui()
                return
            self.recording = True
            deadline = time.monotonic() + 2.0
            while self._segment_output_path is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self._last_action = "Recording segment started"
            self._play_cue("session.segment_start")
            log.info("OBS Session segment start")
        else:
            if not obs.stop_recording():
                self._last_action = "StopRecord failed"
                self._refresh_ui()
                return
            path = self._segment_output_path or self._drain_record_path()
            self._segment_output_path = None
            stopped = _utc_now_iso()
            self.segments_count += 1
            seg = SegmentEntry(
                index=self.segments_count,
                started_utc=self._pending_segment_start or stopped,
                stopped_utc=stopped,
                file_path=path,
                session_file_path=None,
                note="" if path else "path not reported by OBS yet",
            )
            self._segments.append(seg)
            self._copy_segment_into_session(path, seg)
            self._pending_segment_start = None
            self.recording = obs.is_recording
            short = path or "(path unknown)"
            self._last_action = f"Segment {self.segments_count} saved: {short}"
            self._play_cue("session.segment_stop")
            log.info("OBS Session segment stop file=%s", path)
        self._refresh_ui()

    def _write_manifest(self, post_notes: dict) -> Path | None:
        if not self._session_folder:
            return None
        manifest = self._session_folder / "session_manifest.json"
        payload = {
            "schema": "midi-macropad.obs_session.v1",
            "created_utc": _utc_now_iso(),
            "scenes": {
                "screen": self.scene_screen,
                "camera": self.scene_camera,
                "pip": self.scene_pip,
                "cam_app": self.scene_cam_app,
            },
            "sources": {
                "right_screen": self.right_screen_source,
                "camera": self.camera_source,
                "microphone": self.mic_source,
            },
            "session_state": self.session_state,
            "segments": [asdict(s) for s in self._segments],
            "postprocess": post_notes,
        }
        try:
            manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            log.info("OBS Session manifest written %s", manifest)
            return manifest
        except OSError as exc:
            log.error("OBS Session manifest write failed: %s", exc)
            return None

    def _action_stop_session(self) -> None:
        if self.session_state == "postprocessing" or self._postprocess_running:
            self._last_action = "Post-process still running — wait for it to finish"
            self._refresh_ui()
            return
        if self.session_state == "stopped":
            self._last_action = "Session already stopped"
            self._refresh_ui()
            return
        if self.session_state != "running":
            self._last_action = "Start a session first with the Session pad"
            self._refresh_ui()
            return

        obs = self._get_obs()
        post: dict = {
            "stitch": {"status": "pending", "message": ""},
            "transcript": {"status": "pending", "message": ""},
        }

        if obs and obs.connected:
            obs.refresh_recording_state()
        if obs and obs.connected and obs.is_recording:
            if not obs.stop_recording():
                self._last_action = "StopRecord failed — fix OBS state or try again"
                if obs:
                    obs.refresh_recording_state()
                    self.recording = obs.is_recording
                self._refresh_ui()
                return
            path = self._segment_output_path or self._drain_record_path(2.0)
            self._segment_output_path = None
            stopped = _utc_now_iso()
            self.segments_count += 1
            copied = self._copy_segment_into_session(path)
            self._segments.append(
                SegmentEntry(
                    index=self.segments_count,
                    started_utc=self._pending_segment_start or stopped,
                    stopped_utc=stopped,
                    file_path=path,
                    session_file_path=copied,
                    note="closed on session stop",
                )
            )
            self._pending_segment_start = None
            self.recording = obs.is_recording if obs else False

        self._restore_obs_record_directory()

        self.session_state = "stopped"
        mf = self._write_manifest(post)
        self.recording = False
        if obs:
            obs.refresh_recording_state()

        if mf:
            self._last_action = (
                f"Session stopped — running post-process… manifest: {mf.name}"
                if (self.postprocess_stitch or self.postprocess_transcript)
                else f"Session stopped — manifest: {mf.name}"
            )
        else:
            self._last_action = "Session stopped (manifest not written)"

        log.info("OBS Session stopped manifest=%s segments=%s", mf, self.segments_count)
        self._play_cue("session.stop")

        run_post = self.postprocess_stitch or self.postprocess_transcript
        if run_post and mf and self._session_folder:
            self.session_state = "postprocessing"
            self._postprocess_running = True
            threading.Thread(target=self._postprocess_worker, daemon=True).start()
        else:
            self.session_state = "idle"
            if self.postprocess_open_folder and self._session_folder:
                try:
                    _get_postprocess().reveal_session_folder(self._session_folder)
                except Exception as exc:
                    log.debug("open folder: %s", exc)

        self._refresh_ui()

    def _action_reset_state(self) -> None:
        self._restore_obs_record_directory()
        self.session_state = "idle"
        self.recording = False
        self._segments = []
        self.segments_count = 0
        self._session_folder = None
        self._latest_record_path = None
        self._pending_segment_start = None
        self._segment_output_path = None
        while True:
            try:
                self._record_paths.get_nowait()
            except queue.Empty:
                break
        self._last_action = "Local session state reset (OBS untouched)"
        self._refresh_ui()

    # -- helpers ------------------------------------------------------------

    def _add_status_row(
        self,
        parent_tag: str | None,
        label: str,
        value_tag: str,
        wrap: int = 0,
    ) -> None:
        import dearpygui.dearpygui as dpg

        with dpg.group(horizontal=True, parent=parent_tag):
            dpg.add_text(f"{label}:", color=(120, 120, 140))
            dpg.add_text("-", tag=value_tag, wrap=wrap)

    def _save_text(self, field: str, value: str) -> None:
        setattr(self, field, str(value).strip())
        self._persist_settings()
        self._last_action = f"Saved setting: {field}"
        self._refresh_ui()

    def _save_port(self, value: int) -> None:
        self.port = max(1, int(value))
        self._persist_settings()
        self._last_action = "Saved setting: port"
        self._refresh_ui()

    def _save_bool(self, field: str, value: bool) -> None:
        setattr(self, field, bool(value))
        self._persist_settings()
        self._last_action = f"Saved setting: {field}"
        self._refresh_ui()

    def _persist_settings(self) -> None:
        settings.put(
            SETTINGS_KEY,
            {
                "host": self.host,
                "port": self.port,
                "password": self.password,
                "scene_screen": self.scene_screen,
                "scene_camera": self.scene_camera,
                "scene_pip": self.scene_pip,
                "scene_cam_app": self.scene_cam_app,
                "right_screen_source": self.right_screen_source,
                "camera_source": self.camera_source,
                "mic_source": self.mic_source,
                "desktop_audio_source": self.desktop_audio_source,
                "aux_audio_source": self.aux_audio_source,
                "output_dir": self.output_dir,
                "replay_dir": self.replay_dir,
                "postprocess_stitch": self.postprocess_stitch,
                "postprocess_transcript": self.postprocess_transcript,
                "postprocess_open_folder": self.postprocess_open_folder,
                "pad_slots": {str(k): v for k, v in self._slot_map.items()},
            },
        )

    def _ui_session_state(self) -> str:
        if self.session_state == "postprocessing":
            return "postprocessing"
        if self.session_state == "running" and self.recording:
            return "recording"
        if self.session_state == "running":
            return "active"
        return "idle"

    def _ui_session_state_color(self) -> tuple[int, int, int]:
        state = self._ui_session_state()
        if state == "recording":
            return _STATUS_RECORDING
        if state == "active":
            return _STATUS_ACTIVE
        if state == "postprocessing":
            return _STATUS_POSTPROCESS
        return _STATUS_IDLE

    def _session_folder_label(self) -> str:
        return self._session_folder.name if self._session_folder else "Not created yet"

    def _segment_status(self, segment: SegmentEntry) -> str:
        if not segment.file_path and not segment.session_file_path:
            return "Waiting for file"
        if "closed on session stop" in segment.note:
            return "Closed on stop"
        return "Saved"

    def _render_segments_panel(self, dpg) -> None:
        panel_tag = "obs_session_segments_panel"
        if not dpg.does_item_exist(panel_tag):
            return
        dpg.delete_item(panel_tag, children_only=True)

        if not self._segments and not self.recording:
            dpg.add_text(
                "No segments yet. Start a session, then use the Record pad to create the first recorded segment.",
                parent=panel_tag,
                wrap=700,
                color=(120, 120, 140),
            )
            return

        if self.recording and self._pending_segment_start:
            dpg.add_text(
                f"Recording now since {_format_local_clock(self._pending_segment_start)}. Press Record again to close this segment.",
                parent=panel_tag,
                wrap=700,
                color=_STATUS_RECORDING,
            )
            dpg.add_spacer(height=6, parent=panel_tag)

        with dpg.table(
            parent=panel_tag,
            header_row=True,
            borders_outerH=True,
            borders_outerV=True,
            borders_innerH=True,
            borders_innerV=True,
            row_background=True,
            resizable=True,
            policy=dpg.mvTable_SizingStretchProp,
        ):
            dpg.add_table_column(label="#", width_fixed=True, init_width_or_weight=30)
            dpg.add_table_column(label="Started")
            dpg.add_table_column(label="Stopped")
            dpg.add_table_column(label="Status")
            dpg.add_table_column(label="File")
            for segment in self._segments:
                with dpg.table_row():
                    dpg.add_text(str(segment.index))
                    dpg.add_text(_format_local_clock(segment.started_utc))
                    dpg.add_text(_format_local_clock(segment.stopped_utc))
                    dpg.add_text(self._segment_status(segment))
                    dpg.add_text(
                        segment.session_file_path or segment.file_path or segment.note or "-",
                        wrap=360,
                    )

    def _scan_sessions_catalog(self) -> list[dict]:
        base = self._resolve_output_base()
        rows: list[dict] = []
        if not base.exists():
            self._catalog_state = "no_dir"
            return rows
        if not base.is_dir():
            self._catalog_state = "not_a_dir"
            return rows
        try:
            candidates = sorted(base.iterdir(), key=lambda p: p.name)
        except OSError as exc:
            self._catalog_state = f"scan_error: {exc}"
            return rows
        for path in candidates:
            if not path.is_dir() or not path.name.startswith("session_"):
                continue
            manifest_path = path / "session_manifest.json"
            created_utc = ""
            segments_count = 0
            status = "unknown"
            has_final_video = False
            if manifest_path.is_file():
                try:
                    data = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                    created_utc = str(data.get("created_utc") or "")
                    segs = data.get("segments") or []
                    segments_count = len(segs) if isinstance(segs, list) else 0
                    status = str(data.get("session_state") or "unknown")
                    post = data.get("postprocess") or {}
                    artifacts = post.get("artifacts") or {}
                    final_ref = artifacts.get("final")
                    has_final_video = bool(final_ref) or (
                        path / "session_final.mp4"
                    ).is_file()
                except (OSError, json.JSONDecodeError, TypeError):
                    status = "manifest error"
                    has_final_video = (path / "session_final.mp4").is_file()
            else:
                status = "no manifest"
                has_final_video = (path / "session_final.mp4").is_file()
            rows.append(
                {
                    "folder_name": path.name,
                    "folder_path": str(path.resolve()),
                    "created_utc": created_utc,
                    "segments_count": segments_count,
                    "status": status,
                    "has_final_video": has_final_video,
                }
            )

        def sort_key(row: dict) -> tuple:
            iso = row.get("created_utc") or ""
            if iso:
                try:
                    dt = datetime.fromisoformat(
                        iso.replace("Z", "+00:00")
                    )
                    return (True, dt)
                except ValueError:
                    pass
            name = row.get("folder_name") or ""
            if name.startswith("session_") and "_" in name[8:]:
                rest = name[8:]
                try:
                    date_part, time_part = rest.split("_", 1)
                    dt = datetime.strptime(
                        f"{date_part}_{time_part}", "%Y%m%d_%H%M%S"
                    ).replace(tzinfo=timezone.utc)
                    return (True, dt)
                except ValueError:
                    pass
            return (False, datetime.min.replace(tzinfo=timezone.utc))

        rows.sort(key=sort_key, reverse=True)
        self._catalog_state = "ok" if rows else "empty"
        return rows[:20]

    def _catalog_refresh_clicked(self) -> None:
        self._catalog_force_rescan = True
        self._refresh_ui()

    def _open_session_folder_explorer(self, sender, app_data, user_data) -> None:
        path = user_data
        if not path or not isinstance(path, str):
            return
        if sys.platform == "win32":
            try:
                subprocess.Popen(["explorer", path])
            except OSError as exc:
                log.warning("OBS Session catalog: open folder failed: %s", exc)

    def _format_catalog_date(self, created_utc: str) -> str:
        if not created_utc:
            return "-"
        try:
            dt = datetime.fromisoformat(created_utc.replace("Z", "+00:00"))
            return dt.astimezone().strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return created_utc

    def _render_catalog_panel(self, dpg) -> None:
        panel_tag = "obs_session_catalog_panel"
        if not dpg.does_item_exist(panel_tag):
            return
        dpg.delete_item(panel_tag, children_only=True)
        entries = getattr(self, "_catalog_entries", None) or []
        state = getattr(self, "_catalog_state", "empty")
        if not entries:
            if state == "no_dir":
                msg = f"Output directory does not exist yet: {self._resolve_output_base()}"
                clr = _STATUS_WARN
            elif state == "not_a_dir":
                msg = f"Output path exists but is not a directory: {self._resolve_output_base()}"
                clr = _CONNECTED_BAD
            elif state.startswith("scan_error"):
                msg = f"Cannot read output directory: {state}"
                clr = _CONNECTED_BAD
            else:
                msg = "No session folders found. Start a session to create recordings here."
                clr = (120, 120, 140)
            dpg.add_text(msg, parent=panel_tag, wrap=700, color=clr)
            return
        with dpg.table(
            parent=panel_tag,
            header_row=True,
            borders_outerH=True,
            borders_outerV=True,
            borders_innerH=True,
            borders_innerV=True,
            row_background=True,
            resizable=True,
            policy=dpg.mvTable_SizingStretchProp,
        ):
            dpg.add_table_column(label="Date")
            dpg.add_table_column(
                label="Segments", width_fixed=True, init_width_or_weight=70
            )
            dpg.add_table_column(label="Status")
            dpg.add_table_column(label="Folder")
            dpg.add_table_column(
                label="", width_fixed=True, init_width_or_weight=96
            )
            for entry in entries:
                date_s = self._format_catalog_date(entry.get("created_utc", ""))
                seg_n = entry.get("segments_count", 0)
                st = entry.get("status", "")
                if entry.get("has_final_video"):
                    st_disp = f"{st} · final video"
                else:
                    st_disp = str(st)
                folder_s = entry.get("folder_name", "-")
                fpath = entry.get("folder_path", "")
                with dpg.table_row():
                    dpg.add_text(date_s)
                    dpg.add_text(str(seg_n))
                    dpg.add_text(st_disp)
                    dpg.add_text(folder_s, wrap=280)
                    dpg.add_button(
                        label="Open Folder",
                        width=-1,
                        user_data=fpath,
                        callback=self._open_session_folder_explorer,
                    )

    def _refresh_ui(self) -> None:
        if os.environ.get("MACROPAD_HEADLESS"):
            return
        try:
            import dearpygui.dearpygui as dpg
        except Exception:
            return

        if self._active:
            now_cat = time.monotonic()
            last_scan = getattr(self, "_last_catalog_scan", 0.0)
            force = getattr(self, "_catalog_force_rescan", False)
            if force:
                self._catalog_force_rescan = False
                self._last_catalog_scan = now_cat
                self._catalog_entries = self._scan_sessions_catalog()
            elif now_cat - last_scan >= 5.0:
                self._last_catalog_scan = now_cat
                self._catalog_entries = self._scan_sessions_catalog()
            elif not hasattr(self, "_catalog_entries"):
                self._last_catalog_scan = now_cat
                self._catalog_entries = self._scan_sessions_catalog()

        obs = self._get_obs()
        live = obs.connected if obs else False
        ui_connected = live
        ui_state = self._ui_session_state()
        current_scene = obs.current_scene if obs and live else "?"
        connection_text = (
            f"Connected · {obs.obs_version}"
            if live and obs
            else (f"Disconnected · {self._connection_detail}" if self._connection_detail else "Disconnected")
        )
        connection_color = _CONNECTED_OK if ui_connected else _CONNECTED_BAD
        session_color = self._ui_session_state_color()
        recording_text = "Recording" if self.recording else "Standby"
        recording_color = _STATUS_RECORDING if self.recording else _STATUS_IDLE
        in_session = self.session_state == "running"
        ffmpeg_ready = shutil.which("ffmpeg") is not None
        api_key_ready = _has_openai_api_key()

        if ffmpeg_ready:
            ffmpeg_text = "Ready"
            ffmpeg_color = _CONNECTED_OK
        elif self.postprocess_stitch:
            ffmpeg_text = "Missing - required for clip stitching"
            ffmpeg_color = _CONNECTED_BAD
        else:
            ffmpeg_text = "Missing - stitching toggle is off"
            ffmpeg_color = _STATUS_WARN

        if api_key_ready:
            api_text = "Ready"
            api_color = _CONNECTED_OK
        elif self.postprocess_transcript:
            api_text = "Missing - required for transcript step"
            api_color = _CONNECTED_BAD
        else:
            api_text = "Missing - transcript toggle is off"
            api_color = _STATUS_WARN

        if not ui_connected:
            prereq_hint = "OBS is disconnected. Connect OBS before starting a session or recording segments."
        elif not ffmpeg_ready and self.postprocess_stitch:
            prereq_hint = "ffmpeg is missing, so the stitch step cannot run after session stop."
        elif not api_key_ready and self.postprocess_transcript:
            prereq_hint = "No OpenAI API key found, so the transcript step cannot run after session stop."
        else:
            prereq_hint = "Prerequisites look good for the currently enabled workflow."

        self._set_text_if_exists(
            dpg, "obs_session_status_connection", connection_text, connection_color
        )
        self._set_text_if_exists(
            dpg, "obs_session_status_session", ui_state.title(), session_color
        )
        self._set_text_if_exists(
            dpg, "obs_session_status_recording", recording_text, recording_color
        )
        self._set_text_if_exists(
            dpg,
            "obs_session_status_segments",
            str(self.segments_count),
            _STATUS_ACTIVE if self.segments_count else _STATUS_IDLE,
        )
        self._set_text_if_exists(
            dpg,
            "obs_session_status_folder",
            self._session_folder_label(),
            _STATUS_POSTPROCESS if self._session_folder else _STATUS_IDLE,
        )
        self._set_text_if_exists(
            dpg, "obs_session_overview_scene", current_scene,
            _CONNECTED_OK if ui_connected else _STATUS_IDLE,
        )
        self._set_text_if_exists(
            dpg, "obs_session_overview_state", ui_state.title(), session_color,
        )
        self._set_text_if_exists(
            dpg, "obs_session_overview_recording", recording_text, recording_color,
        )
        self._set_text_if_exists(
            dpg, "obs_session_overview_segments",
            str(self.segments_count),
            _STATUS_ACTIVE if self.segments_count else _STATUS_IDLE,
        )
        self._set_text_if_exists(
            dpg, "obs_session_overview_folder",
            self._session_folder_label(),
            _STATUS_POSTPROCESS if self._session_folder else _STATUS_IDLE,
        )
        self._set_text_if_exists(
            dpg,
            "obs_session_prereq_obs",
            connection_text,
            connection_color,
        )
        self._set_text_if_exists(
            dpg,
            "obs_session_prereq_ffmpeg",
            ffmpeg_text,
            ffmpeg_color,
        )
        self._set_text_if_exists(
            dpg,
            "obs_session_prereq_api",
            api_text,
            api_color,
        )
        self._set_text_if_exists(
            dpg, "obs_session_last_action", self._last_action, (120, 120, 140),
        )
        self._set_text_if_exists(
            dpg, "obs_session_props_last_action", self._last_action, (120, 120, 140),
        )
        self._set_text_if_exists(
            dpg,
            "obs_session_prereq_settings_hint",
            prereq_hint,
            _CONNECTED_OK if prereq_hint.startswith("Prerequisites") else _STATUS_WARN,
        )
        if self._postprocess_running or self.session_state == "postprocessing":
            hint = (
                "Post-processing is running (ffmpeg / OpenAI when enabled). "
                "Watch Activity for completion; the manifest is updated with outputs."
            )
            hint_color = _STATUS_POSTPROCESS
        elif self.postprocess_stitch or self.postprocess_transcript:
            hint = (
                "After Stop Session: segments stitch with ffmpeg, optional Whisper subtitles "
                f"({'on' if self.postprocess_transcript else 'off'}), then burn-in for final video."
            )
            hint_color = (200, 200, 220)
        else:
            hint = (
                "Post-process steps are disabled; enable stitch and/or transcript above "
                "to produce session_stitched.mp4 / session_final.mp4 automatically."
            )
            hint_color = _STATUS_WARN
        self._set_text_if_exists(
            dpg, "obs_session_postprocess_hint", hint, hint_color,
        )
        self._set_text_if_exists(
            dpg, "obs_session_props_postprocess_hint", hint, hint_color,
        )
        self._render_segments_panel(dpg)
        self._render_catalog_panel(dpg)

        for note in sorted(self._slot_map.keys()):
            action_id = self._slot_map.get(note)
            lbl = self._dynamic_label(action_id) if action_id else "—"
            self._set_text_if_exists(dpg, f"obs_pad_lbl_{note}", lbl, (230, 232, 238))
            drag_tag = f"obs_pad_drag_{note}"
            if dpg.does_item_exist(drag_tag):
                dpg.configure_item(drag_tag, label=lbl)
            if action_id:
                btn_tag = f"obs_pad_btn_{note}"
                if dpg.does_item_exist(btn_tag):
                    dpg.configure_item(btn_tag, label=lbl)
                    can_use = ui_connected and not self._postprocess_running
                    if action_id in ("scene_screen", "scene_camera", "scene_pip", "scene_cam_app", "mute_mic", "mute_desktop", "mute_aux"):
                        can_use = ui_connected
                    dpg.configure_item(btn_tag, enabled=can_use)

        self._set_button_enabled_if_exists(dpg, "obs_session_btn_connect", not ui_connected)
        self._set_button_enabled_if_exists(dpg, "obs_session_btn_disconnect", ui_connected)
        replay_label = "Stop Replay" if self.replay_buffer_active else "Start Replay"
        self._set_button_label_if_exists(dpg, "obs_session_btn_replay_toggle", replay_label)
        replay_status = f"ON ({self._replay_save_count} saved)" if self.replay_buffer_active else "OFF"
        replay_color = _CONNECTED_OK if self.replay_buffer_active else _STATUS_IDLE
        self._set_text_if_exists(dpg, "obs_session_replay_status", replay_status, replay_color)

    @staticmethod
    def _set_text_if_exists(dpg, tag: str, value: str, color: tuple[int, int, int]) -> None:
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, value)
            dpg.configure_item(tag, color=color)

    @staticmethod
    def _set_button_enabled_if_exists(dpg, tag: str, enabled: bool) -> None:
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, enabled=enabled)

    @staticmethod
    def _set_button_label_if_exists(dpg, tag: str, label: str) -> None:
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, label=label)
