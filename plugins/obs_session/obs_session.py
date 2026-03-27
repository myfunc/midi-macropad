"""OBS Session plugin — connect, auto scene setup, segmented recording, session manifest."""

from __future__ import annotations

import importlib.util
import json
import os
import queue
import shutil
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

MODE_NAME = "OBS Session"
SETTINGS_KEY = "obs_session_plugin"

PAD_START_SESSION = 16
PAD_RECORD_TOGGLE = 17
PAD_STOP_SESSION = 18

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


def _session_source_plan() -> list[tuple[str, str]]:
    """(source_label, obs_input_kind) for auto-setup; Windows-focused, safe on others."""
    if sys.platform == "win32":
        return [
            ("__right__", "monitor_capture"),
            ("__camera__", "dshow_input"),
            ("__mic__", "wasapi_input_capture"),
        ]
    if sys.platform == "darwin":
        return [
            ("__right__", "monitor_capture"),
            ("__camera__", "av_capture_input"),
            ("__mic__", "coreaudio_input_capture"),
        ]
    return []


class OBSSessionPlugin(Plugin):
    name = "OBS Session"
    version = "0.3.0"
    description = "Session recording workflow for OBS (connect, scene setup, segments, manifest)"
    mode_name = MODE_NAME

    def __init__(self):
        self._active = False

        self.host = "127.0.0.1"
        self.port = 4455
        self.password = ""
        self.working_scene = "MM_Session"
        self.right_screen_source = "MM_Right Screen"
        self.camera_source = "MM_Camera"
        self.mic_source = "MM_Microphone"
        self.output_dir = ""
        self.auto_setup_scene = True
        self.postprocess_stitch = True
        self.postprocess_transcript = False
        self.postprocess_open_folder = True

        self.connected = False
        self.session_state = "idle"
        self.recording = False
        self.segments_count = 0
        self._last_action = "Waiting for session start"
        self._connection_detail = ""

        self._obs = None  # lazy: OBSController from project root
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

    def on_load(self, config: dict) -> None:
        saved = settings.get(SETTINGS_KEY, {})
        merged = {
            "host": saved.get("host", config.get("host", self.host)),
            "port": saved.get("port", config.get("port", self.port)),
            "password": saved.get("password", config.get("password", self.password)),
            "working_scene": saved.get(
                "working_scene", config.get("working_scene", self.working_scene)
            ),
            "right_screen_source": saved.get(
                "right_screen_source",
                config.get("right_screen_source", self.right_screen_source),
            ),
            "camera_source": saved.get(
                "camera_source", config.get("camera_source", self.camera_source)
            ),
            "mic_source": saved.get(
                "mic_source", config.get("mic_source", self.mic_source)
            ),
            "output_dir": saved.get("output_dir", config.get("output_dir", self.output_dir)),
            "auto_setup_scene": saved.get(
                "auto_setup_scene",
                config.get("auto_setup_scene", self.auto_setup_scene),
            ),
            "postprocess_stitch": saved.get(
                "postprocess_stitch",
                config.get("postprocess_stitch", self.postprocess_stitch),
            ),
            "postprocess_transcript": saved.get(
                "postprocess_transcript",
                config.get("postprocess_transcript", self.postprocess_transcript),
            ),
            "postprocess_open_folder": saved.get(
                "postprocess_open_folder",
                config.get("postprocess_open_folder", self.postprocess_open_folder),
            ),
        }
        self.host = str(merged["host"]).strip()
        self.port = int(merged["port"])
        self.password = str(merged["password"])
        self.working_scene = str(merged["working_scene"]).strip()
        self.right_screen_source = str(merged["right_screen_source"]).strip()
        self.camera_source = str(merged["camera_source"]).strip()
        self.mic_source = str(merged["mic_source"]).strip()
        self.output_dir = str(merged["output_dir"]).strip()
        self.auto_setup_scene = bool(merged["auto_setup_scene"])
        self.postprocess_stitch = bool(merged["postprocess_stitch"])
        self.postprocess_transcript = bool(merged["postprocess_transcript"])
        self.postprocess_open_folder = bool(merged["postprocess_open_folder"])
        self._persist_settings()

        self.connected = False
        self.session_state = "idle"
        self.recording = False
        self.segments_count = 0
        self._last_action = "Configured; use Connect or Start Session when OBS is running"
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

    # -- MIDI hooks --------------------------------------------------------

    def on_pad_press(self, note: int, velocity: int) -> bool:
        if not self._active:
            return False
        if note == PAD_START_SESSION:
            self._action_start_session()
            return True
        if note == PAD_RECORD_TOGGLE:
            self._action_toggle_record()
            return True
        if note == PAD_STOP_SESSION:
            self._action_stop_session()
            return True
        return False

    def on_pad_release(self, note: int) -> bool:
        return self._active and note in {
            PAD_START_SESSION,
            PAD_RECORD_TOGGLE,
            PAD_STOP_SESSION,
        }

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
                if self.session_state == "running":
                    self.recording = obs.is_recording
        elif obs and not obs.connected and self.connected:
            self.connected = False
            self._connection_detail = "Connection lost"
            log.warning("OBS Session: connection lost")

        self._refresh_ui()

    # -- status + pad labels ------------------------------------------------

    def get_pad_labels(self) -> dict[int, str]:
        if not self._active:
            return {}
        return {
            PAD_START_SESSION: "Start Session",
            PAD_RECORD_TOGGLE: "Record Segment",
            PAD_STOP_SESSION: "Stop Session",
        }

    def get_status(self) -> tuple[str, tuple[int, int, int]] | None:
        if not self._active:
            return None
        connection = "OK" if self.connected else "Off"
        recording = "REC" if self.recording else "Standby"
        text = (
            f"OBS Session | {connection} | Session: {self._ui_session_state().title()} | "
            f"{recording} | Segments: {self.segments_count}"
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

        dpg.add_text("OBS Session Settings", parent=parent_tag, color=(110, 190, 255))
        dpg.add_text(
            "Configure the names OBS should use, where session folders are saved, "
            "and which postprocessing steps should appear in the workflow.",
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
        dpg.add_text("Capture Sources", parent=parent_tag, color=(150, 150, 165))
        dpg.add_input_text(
            tag="obs_session_working_scene",
            parent=parent_tag,
            default_value=self.working_scene,
            hint="Prefixed scene name (e.g. MM_Session)",
            width=-1,
            callback=lambda sender, app_data: self._save_text("working_scene", app_data),
        )
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
        dpg.add_text("Output And Postprocessing", parent=parent_tag, color=(150, 150, 165))
        dpg.add_input_text(
            tag="obs_session_output_dir",
            parent=parent_tag,
            default_value=self.output_dir,
            hint=str(Path.home() / "Videos" / "OBS-Sessions"),
            width=-1,
            callback=lambda sender, app_data: self._save_text("output_dir", app_data),
        )
        dpg.add_checkbox(
            tag="obs_session_auto_setup",
            label="Auto-setup scene",
            parent=parent_tag,
            default_value=self.auto_setup_scene,
            callback=lambda sender, app_data: self._save_bool("auto_setup_scene", app_data),
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
        dpg.add_text("Pad 1  Start session", parent=parent_tag)
        dpg.add_text("Pad 2  Toggle record segment", parent=parent_tag)
        dpg.add_text("Pad 3  Stop session", parent=parent_tag)
        dpg.add_text(
            f"MIDI notes {PAD_START_SESSION}/{PAD_RECORD_TOGGLE}/{PAD_STOP_SESSION} "
            "are currently assigned to pads 1-3 in the session workflow.",
            parent=parent_tag,
            wrap=260,
            color=(120, 120, 140),
        )

        dpg.add_spacer(height=10, parent=parent_tag)
        dpg.add_text(
            "",
            tag="obs_session_last_action",
            parent=parent_tag,
            wrap=260,
            color=(120, 120, 140),
        )
        dpg.add_text(
            "",
            tag="obs_session_postprocess_hint",
            parent=parent_tag,
            wrap=260,
            color=(255, 200, 120),
        )

        self._refresh_ui()

    def build_ui(self, parent_tag: str) -> None:
        import dearpygui.dearpygui as dpg

        dpg.add_text("OBS Session Workflow", parent=parent_tag, color=(110, 190, 255))
        dpg.add_text(
            "Run the session from left to right: connect OBS, start the session on Pad 1, "
            "toggle segments on Pad 2, then finish on Pad 3.",
            parent=parent_tag,
            wrap=720,
            color=(120, 120, 140),
        )
        dpg.add_spacer(height=6, parent=parent_tag)

        with dpg.child_window(parent=parent_tag, height=136, border=True):
            with dpg.group(horizontal=True):
                with dpg.group():
                    dpg.add_text("Session Overview", color=(150, 150, 165))
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

        dpg.add_spacer(height=6, parent=parent_tag)
        with dpg.group(horizontal=True, parent=parent_tag):
            with dpg.child_window(width=220, height=144, border=True):
                dpg.add_text("PAD 1", color=(100, 180, 255))
                dpg.add_text("Start session", color=(230, 232, 238))
                dpg.add_text(
                    "Create the session folder, switch OBS to the working scene, and prepare capture sources.",
                    wrap=200,
                    color=(120, 120, 140),
                )
                dpg.add_spacer(height=6)
                dpg.add_button(
                    tag="obs_session_btn_start",
                    label="Start Session",
                    width=-1,
                    callback=self._action_start_session,
                )
            with dpg.child_window(width=220, height=144, border=True):
                dpg.add_text("PAD 2", color=(255, 200, 90))
                dpg.add_text("Record segment", color=(230, 232, 238))
                dpg.add_text(
                    "Use this once to begin a take, then press again to close and save that segment into the session folder.",
                    wrap=200,
                    color=(120, 120, 140),
                )
                dpg.add_spacer(height=6)
                dpg.add_button(
                    tag="obs_session_btn_record",
                    label="Start Segment",
                    width=-1,
                    callback=self._action_toggle_record,
                )
            with dpg.child_window(width=-1, height=144, border=True):
                dpg.add_text("PAD 3", color=(255, 120, 120))
                dpg.add_text("Stop session", color=(230, 232, 238))
                dpg.add_text(
                    "Finalize the session, close any open recording, and hand off to the postprocessing stage.",
                    wrap=240,
                    color=(120, 120, 140),
                )
                dpg.add_spacer(height=6)
                dpg.add_button(
                    tag="obs_session_btn_stop",
                    label="Stop Session",
                    width=-1,
                    callback=self._action_stop_session,
                )

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

    def _on_record_state_changed(self, message) -> None:
        try:
            active = None
            if hasattr(message, "getOutputActive"):
                active = bool(message.getOutputActive())
            if active is not None:
                self.recording = active
        except Exception as exc:
            log.debug("RecordStateChanged parse: %s", exc)

    def _on_record_file_changed(self, message) -> None:
        try:
            path = message.getNewOutputPath() if hasattr(message, "getNewOutputPath") else None
            if not path and hasattr(message, "getOutputPath"):
                path = message.getOutputPath()
            if path:
                p = str(path)
                self._latest_record_path = p
                self._record_paths.put(p)
                if self.session_state == "running" and self._pending_segment_start is not None:
                    self._segment_output_path = p
        except Exception as exc:
            log.debug("RecordFileChanged parse: %s", exc)

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
        self._record_cb_registered = False

    # -- actions -----------------------------------------------------------

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

    def _auto_setup_workspace(self, obs) -> tuple[bool, list[str]]:
        notes: list[str] = []
        if not self.auto_setup_scene:
            return True, ["auto-setup disabled"]
        if not obs.ensure_scene_exists(self.working_scene):
            msg = f"Could not create or find scene '{self.working_scene}'"
            log.error(msg)
            return False, [msg]

        name_map = {
            "__right__": self.right_screen_source,
            "__camera__": self.camera_source,
            "__mic__": self.mic_source,
        }
        plan = _session_source_plan()
        if not plan:
            notes.append(
                "Auto-setup: scene only on this OS; add monitor, camera, and mic sources manually."
            )
            return True, notes

        for key, kind in plan:
            label = name_map[key]
            defaults = obs.get_default_input_settings(kind)
            ok, hint = obs.add_source_to_scene(
                self.working_scene, label, kind, defaults
            )
            detail = f"{label} ({kind}): {hint}"
            notes.append(detail)
            if not ok:
                log.error("OBS Session setup step failed: %s", detail)
                notes.append(
                    "You can create or rename sources in OBS to match the configured names."
                )
        crop_ok, crop_hint = obs.crop_source_to_right_half(
            self.working_scene, self.right_screen_source
        )
        if crop_ok:
            notes.append(f"{self.right_screen_source}: right-half crop applied ({crop_hint})")
        else:
            notes.append(
                f"{self.right_screen_source}: crop skipped ({crop_hint}); set crop manually if needed"
            )
        pip_ok, pip_hint = obs.position_camera_pip(
            self.working_scene, self.camera_source, scale=0.28, margin=24.0
        )
        if pip_ok:
            notes.append(f"{self.camera_source}: PiP layout ({pip_hint})")
        else:
            notes.append(
                f"{self.camera_source}: PiP skipped ({pip_hint}); position camera manually if needed"
            )
        return True, notes

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
                "OBS is already recording. Stop recording in OBS (or finish the current "
                "take) before starting a session so Pad 2 start/stop stays one segment at a time."
            )
            self._refresh_ui()
            return

        ok, setup_notes = self._auto_setup_workspace(obs)
        if not ok:
            self._last_action = setup_notes[0] if setup_notes else "Setup failed"
            self._refresh_ui()
            return

        if not obs.switch_scene(self.working_scene):
            self._last_action = f"Could not switch to scene '{self.working_scene}'"
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
        self._refresh_ui()

    def _drain_record_path(self, timeout: float = 1.0) -> str | None:
        try:
            return self._record_paths.get(timeout=timeout)
        except queue.Empty:
            return self._latest_record_path

    def _copy_segment_into_session(self, source_path: str | None) -> str | None:
        if not source_path or not self._session_folder:
            return None
        src = Path(source_path)
        if not src.exists():
            return None
        try:
            dst = self._session_folder / src.name
            if dst.resolve() == src.resolve():
                return str(dst)
            shutil.copy2(src, dst)
            return str(dst)
        except OSError as exc:
            log.warning("OBS Session copy segment failed: %s", exc)
            return None

    def _action_toggle_record(self) -> None:
        if self.session_state != "running":
            self._last_action = "Start a session first with Pad 1"
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
            copied = self._copy_segment_into_session(path)
            seg = SegmentEntry(
                index=self.segments_count,
                started_utc=self._pending_segment_start or stopped,
                stopped_utc=stopped,
                file_path=path,
                session_file_path=copied,
                note="" if path else "path not reported by OBS yet; file callback may arrive shortly",
            )
            self._segments.append(seg)
            self._pending_segment_start = None
            self.recording = obs.is_recording
            short = copied or path or "(path unknown)"
            self._last_action = f"Segment {self.segments_count} saved: {short}"
            log.info("OBS Session segment stop file=%s", path)
        self._refresh_ui()

    def _write_manifest(self, post_notes: dict) -> Path | None:
        if not self._session_folder:
            return None
        manifest = self._session_folder / "session_manifest.json"
        payload = {
            "schema": "midi-macropad.obs_session.v1",
            "created_utc": _utc_now_iso(),
            "working_scene": self.working_scene,
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
            self._last_action = "Start a session first with Pad 1"
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
                "working_scene": self.working_scene,
                "right_screen_source": self.right_screen_source,
                "camera_source": self.camera_source,
                "mic_source": self.mic_source,
                "output_dir": self.output_dir,
                "auto_setup_scene": self.auto_setup_scene,
                "postprocess_stitch": self.postprocess_stitch,
                "postprocess_transcript": self.postprocess_transcript,
                "postprocess_open_folder": self.postprocess_open_folder,
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
                "No segments yet. Start the session on Pad 1, then use Pad 2 to create the first recorded segment.",
                parent=panel_tag,
                wrap=700,
                color=(120, 120, 140),
            )
            return

        if self.recording and self._pending_segment_start:
            dpg.add_text(
                f"Recording now since {_format_local_clock(self._pending_segment_start)}. Press Pad 2 again to close this segment.",
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

    def _refresh_ui(self) -> None:
        try:
            import dearpygui.dearpygui as dpg
        except Exception:
            return

        obs = self._get_obs()
        live = obs.connected if obs else False
        ui_connected = live
        ui_state = self._ui_session_state()
        connection_text = (
            f"Connected · {obs.obs_version}"
            if live and obs
            else (f"Disconnected · {self._connection_detail}" if self._connection_detail else "Disconnected")
        )
        connection_color = _CONNECTED_OK if ui_connected else _CONNECTED_BAD
        session_color = self._ui_session_state_color()
        recording_text = "Recording" if self.recording else "Standby"
        recording_color = _STATUS_RECORDING if self.recording else _STATUS_IDLE
        record_button_label = "Stop Segment" if self.recording else "Start Segment"
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
            dpg,
            "obs_session_overview_state",
            ui_state.title(),
            session_color,
        )
        self._set_text_if_exists(
            dpg,
            "obs_session_overview_recording",
            recording_text,
            recording_color,
        )
        self._set_text_if_exists(
            dpg,
            "obs_session_overview_segments",
            str(self.segments_count),
            _STATUS_ACTIVE if self.segments_count else _STATUS_IDLE,
        )
        self._set_text_if_exists(
            dpg,
            "obs_session_overview_folder",
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
            dpg,
            "obs_session_last_action",
            self._last_action,
            (120, 120, 140),
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
            dpg,
            "obs_session_postprocess_hint",
            hint,
            hint_color,
        )
        self._render_segments_panel(dpg)
        if dpg.does_item_exist("obs_session_btn_record"):
            dpg.configure_item("obs_session_btn_record", label=record_button_label)
        self._set_button_enabled_if_exists(dpg, "obs_session_btn_connect", not ui_connected)
        self._set_button_enabled_if_exists(dpg, "obs_session_btn_disconnect", ui_connected)
        self._set_button_enabled_if_exists(
            dpg,
            "obs_session_btn_start",
            ui_connected
            and self.session_state != "running"
            and not self._postprocess_running,
        )
        self._set_button_enabled_if_exists(
            dpg,
            "obs_session_btn_record",
            ui_connected
            and self.session_state == "running"
            and not self._postprocess_running,
        )
        self._set_button_enabled_if_exists(
            dpg,
            "obs_session_btn_stop",
            self.session_state == "running",
        )

    @staticmethod
    def _set_text_if_exists(dpg, tag: str, value: str, color: tuple[int, int, int]) -> None:
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, value)
            dpg.configure_item(tag, color=color)

    @staticmethod
    def _set_button_enabled_if_exists(dpg, tag: str, enabled: bool) -> None:
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, enabled=enabled)
