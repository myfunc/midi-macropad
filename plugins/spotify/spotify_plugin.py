"""Spotify plugin — playback control via Web API (OAuth PKCE)."""

from __future__ import annotations

import importlib.util
import os
import threading
import time

from base import Plugin
from logger import get_logger
import settings

_plugin_dir = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name: str):
    """Import a module from this plugin directory without polluting sys.path."""
    path = os.path.join(_plugin_dir, f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"spotify_plugin.{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_auth_mod = _load_sibling("auth")
_api_mod = _load_sibling("api")
start_auth_flow = _auth_mod.start_auth_flow
refresh_access_token = _auth_mod.refresh_access_token
SpotifyAPI = _api_mod.SpotifyAPI
TokenExpiredError = _api_mod.TokenExpiredError

log = get_logger("spotify")

SETTINGS_KEY = "spotify_plugin"
MODE_NAME = "Spotify"

SPOTIFY_GREEN = (30, 185, 84)
_TEXT_MUTED = (120, 120, 140)
_TEXT_SECTION = (150, 150, 165)
_CONNECTED_OK = (100, 255, 150)
_CONNECTED_BAD = (255, 90, 90)
_STATUS_IDLE = (150, 150, 165)

_ACTION_IDS: tuple[str, ...] = (
    "play_pause",
    "next",
    "prev",
    "like",
    "shuffle",
    "dj_mix",
    "add_to_pl",
    "remove_pl",
)
_DEFAULT_SPOTIFY_NOTES: tuple[int, ...] = tuple(range(16, 24))

_SPOTIFY_CATALOG = [
    {"id": "play_pause", "label": "Play / Pause", "description": "Toggle playback"},
    {"id": "next", "label": "Next Track", "description": "Skip to next track"},
    {"id": "prev", "label": "Previous Track", "description": "Go to previous track"},
    {"id": "like", "label": "Like / Unlike", "description": "Toggle saved state for current track"},
    {"id": "shuffle", "label": "Shuffle", "description": "Toggle shuffle mode"},
    {"id": "dj_mix", "label": "DJ Mix", "description": "DJ Mix keystroke (external)"},
    {"id": "add_to_pl", "label": "Add to Playlist", "description": "Add current track to playlist"},
    {"id": "remove_pl", "label": "Remove from Playlist", "description": "Remove current track from playlist"},
]


class SpotifyPlugin(Plugin):
    name = "Spotify"
    version = "0.1.0"
    description = "Spotify playback control via Web API"
    mode_name = MODE_NAME

    def __init__(self) -> None:
        self._active = False

        self.client_id = ""
        self.redirect_port = 8765
        self._access_token = ""
        self._refresh_token = ""
        self._token_expires_at = 0.0

        self._display_name = ""
        self._api: SpotifyAPI | None = None
        self._token_lock = threading.Lock()
        self._last_error = ""
        self._dpg_ready = False

        self._track_name = ""
        self._track_artist = ""
        self._track_album = ""
        self._shuffle_on = False
        self._repeat_mode = "off"  # off | context | track
        self._liked = False
        self._progress_fraction = 0.0
        self._position_label = "0:00"
        self._duration_label = "0:00"
        self._is_playing = False
        self._current_track_uri = ""
        self._current_playlist_id = ""
        self._poll_running = False

        self._last_poll = 0.0
        self._owned_notes: list[int] = []
        self._note_to_action: dict[int, str] = {}

    # -- lifecycle ---------------------------------------------------------

    def on_load(self, config: dict) -> None:
        saved = settings.get(SETTINGS_KEY, {})
        merged = {
            "client_id": saved.get("client_id", config.get("client_id", self.client_id)),
            "redirect_port": saved.get(
                "redirect_port", config.get("redirect_port", self.redirect_port)
            ),
            "access_token": saved.get("access_token", self._access_token),
            "refresh_token": saved.get("refresh_token", self._refresh_token),
            "token_expires_at": saved.get("token_expires_at", self._token_expires_at),
        }
        self.client_id = str(merged["client_id"]).strip()
        self.redirect_port = int(merged["redirect_port"])
        self._access_token = str(merged.get("access_token") or "")
        self._refresh_token = str(merged.get("refresh_token") or "")
        try:
            self._token_expires_at = float(merged.get("token_expires_at") or 0.0)
        except (TypeError, ValueError):
            self._token_expires_at = 0.0

        self._display_name = ""
        if self._access_token:
            self._api = SpotifyAPI(self._access_token)
            threading.Thread(target=self._validate_token_on_load, daemon=True).start()

        self._persist_settings()

    def _validate_token_on_load(self) -> None:
        try:
            if self._token_expires_at and time.time() > self._token_expires_at - 60:
                self._do_token_refresh()
            if self._api:
                profile = self._api.get_user_profile()
                if profile:
                    self._display_name = profile.get("display_name", "")
        except TokenExpiredError:
            if self._do_token_refresh() and self._api:
                try:
                    profile = self._api.get_user_profile()
                    if profile:
                        self._display_name = profile.get("display_name", "")
                except Exception as exc:
                    log.warning("Spotify: profile fetch after refresh failed: %s", exc)
        except Exception as exc:
            log.warning("Spotify: could not validate token on load: %s", exc)

    def on_unload(self) -> None:
        self._persist_settings()

    def on_mode_changed(self, mode_name: str) -> None:
        self._active = mode_name == self.mode_name
        self._refresh_ui()

    def set_owned_notes(self, notes: set[int]) -> None:
        self._active = bool(notes)
        ordered = sorted(notes)
        self._owned_notes = ordered
        self._note_to_action.clear()
        for i, n in enumerate(ordered):
            if i < len(_ACTION_IDS):
                self._note_to_action[n] = _ACTION_IDS[i]

    def _ensure_action_mapping(self) -> None:
        if self._note_to_action:
            return
        for i, n in enumerate(_DEFAULT_SPOTIFY_NOTES):
            if i < len(_ACTION_IDS):
                self._note_to_action[n] = _ACTION_IDS[i]
        self._owned_notes = list(_DEFAULT_SPOTIFY_NOTES)

    # -- MIDI hooks --------------------------------------------------------

    def on_pad_press(self, note: int, velocity: int) -> bool:
        if not self._active:
            return False
        self._ensure_action_mapping()
        aid = self._note_to_action.get(note)
        if aid is None:
            return False
        if aid == "play_pause":
            threading.Thread(
                target=lambda: self._api_call(lambda: self._api.toggle_playback() if self._api else None),
                daemon=True,
            ).start()
            return True
        if aid == "next":
            threading.Thread(
                target=lambda: self._api_call(lambda: self._api.next_track() if self._api else None),
                daemon=True,
            ).start()
            return True
        if aid == "prev":
            threading.Thread(
                target=lambda: self._api_call(lambda: self._api.previous_track() if self._api else None),
                daemon=True,
            ).start()
            return True
        if aid == "like":

            def _toggle_like() -> None:
                track_id = self._api_call(lambda: self._api.get_current_track_id() if self._api else None)
                if track_id:
                    liked = self._api_call(lambda: self._api.toggle_like(track_id) if self._api else None)
                    if liked is not None:
                        self._liked = liked

            threading.Thread(target=_toggle_like, daemon=True).start()
            return True
        if aid == "shuffle":

            def _toggle_shuffle() -> None:
                result = self._api_call(
                    lambda: self._api.toggle_shuffle(self._shuffle_on) if self._api else None
                )
                if result is not None:
                    self._shuffle_on = result

            threading.Thread(target=_toggle_shuffle, daemon=True).start()
            return True
        if aid == "dj_mix":
            return False
        if aid == "add_to_pl":

            def _add() -> None:
                if not self._current_track_uri or not self._current_playlist_id:
                    log.warning("Spotify: no active playlist context to add to")
                    return
                ok = self._api_call(
                    lambda: self._api.add_to_playlist(
                        self._current_playlist_id, self._current_track_uri
                    ) if self._api else None
                )
                if ok:
                    log.info("Spotify: added to playlist")

            threading.Thread(target=_add, daemon=True).start()
            return True
        if aid == "remove_pl":

            def _remove() -> None:
                if not self._current_track_uri or not self._current_playlist_id:
                    log.warning("Spotify: no active playlist context to remove from")
                    return
                ok = self._api_call(
                    lambda: self._api.remove_from_playlist(
                        self._current_playlist_id, self._current_track_uri
                    ) if self._api else None
                )
                if ok:
                    log.info("Spotify: removed from playlist")

            threading.Thread(target=_remove, daemon=True).start()
            return True
        return False

    def on_pad_release(self, note: int) -> bool:
        self._ensure_action_mapping()
        return self._active and note in self._note_to_action

    def poll(self) -> None:
        now = time.monotonic()
        if now - self._last_poll < 3.0:
            self._refresh_ui()
            return
        self._last_poll = now
        if not self._connected or not self._api:
            self._refresh_ui()
            return
        if not getattr(self, "_poll_running", False):
            self._poll_running = True
            threading.Thread(target=self._poll_playback, daemon=True).start()
        self._refresh_ui()

    # -- status + pad labels ------------------------------------------------

    def get_action_catalog(self) -> list[dict]:
        return list(_SPOTIFY_CATALOG)

    def get_pad_labels(self) -> dict[int, str]:
        if not self._active:
            return {}
        self._ensure_action_mapping()
        out: dict[int, str] = {}
        for note, aid in self._note_to_action.items():
            if aid == "play_pause":
                out[note] = "Pause" if self._is_playing else "Play"
            elif aid == "next":
                out[note] = "Next"
            elif aid == "prev":
                out[note] = "Previous"
            elif aid == "like":
                out[note] = "Like" if not self._liked else "Unlike"
            elif aid == "shuffle":
                out[note] = "Shuffle: On" if self._shuffle_on else "Shuffle: Off"
            elif aid == "dj_mix":
                out[note] = "DJ Mix"
            elif aid == "add_to_pl":
                out[note] = "+ Playlist"
            elif aid == "remove_pl":
                out[note] = "- Playlist"
        return out

    def get_status(self) -> tuple[str, tuple[int, int, int]] | None:
        if not self._active:
            return None
        if self._connected:
            text = f"Spotify | {self._track_artist} — {self._track_name}"
            color = SPOTIFY_GREEN
        else:
            text = "Spotify | Not connected — add a Client ID and connect"
            color = _CONNECTED_BAD
        return text, color

    @property
    def _connected(self) -> bool:
        return bool(self._access_token)

    # -- right sidebar ------------------------------------------------------

    def build_properties(self, parent_tag: str) -> None:
        import dearpygui.dearpygui as dpg

        dpg.add_text("Spotify", parent=parent_tag, color=SPOTIFY_GREEN)
        dpg.add_text(
            "Control Spotify from the macropad via Web API. Tokens are stored locally in settings.json "
            "(not encrypted). Transport controls work while the Spotify desktop app is open.",
            parent=parent_tag,
            wrap=260,
            color=_TEXT_MUTED,
        )
        dpg.add_separator(parent=parent_tag)
        dpg.add_spacer(height=6, parent=parent_tag)

        dpg.add_text("Spotify Connection", parent=parent_tag, color=SPOTIFY_GREEN)
        dpg.add_text(
            "Setup (one-time):\n"
            "1. Go to developer.spotify.com/dashboard\n"
            "2. Create App > paste any name, description\n"
            "3. Add Redirect URI: http://127.0.0.1:8765\n"
            "4. Copy Client ID > paste below\n"
            "5. Click Connect > authorize in browser",
            parent=parent_tag,
            wrap=260,
            color=_TEXT_MUTED,
        )
        dpg.add_text(
            "",
            tag="sp_connection_status",
            parent=parent_tag,
            wrap=260,
            color=_CONNECTED_OK if self._connected else _CONNECTED_BAD,
        )
        dpg.add_input_text(
            tag="sp_client_id",
            parent=parent_tag,
            default_value=self.client_id,
            hint="Paste your Client ID from developer.spotify.com",
            width=-1,
            callback=lambda sender, app_data: self._on_client_id_changed(app_data),
        )
        with dpg.tooltip(parent="sp_client_id"):
            dpg.add_text(
                "Create a free app at developer.spotify.com/dashboard. Copy the Client ID here. "
                "No client secret needed — we use PKCE.",
                wrap=260,
            )

        dpg.add_spacer(height=6, parent=parent_tag)
        with dpg.group(horizontal=True, parent=parent_tag):
            dpg.add_button(
                tag="sp_btn_connect",
                label="Connect to Spotify",
                width=200,
                callback=lambda: self._on_connect_clicked(),
            )
            dpg.add_spacer(width=8)
            dpg.add_button(
                tag="sp_btn_disconnect",
                label="Disconnect",
                width=110,
                callback=lambda: self._on_disconnect_clicked(),
            )

        dpg.add_spacer(height=10, parent=parent_tag)
        dpg.add_separator(parent=parent_tag)
        dpg.add_spacer(height=6, parent=parent_tag)

        dpg.add_text("Now Playing", parent=parent_tag, color=_TEXT_SECTION)
        dpg.add_text(
            "Shows what Spotify reports as the active playback so you can confirm the macropad "
            "targets the right session before you hit transport keys.",
            parent=parent_tag,
            wrap=260,
            color=_TEXT_MUTED,
        )
        self._add_labeled_value(parent_tag, "Track", "sp_track_name")
        self._add_labeled_value(parent_tag, "Artist", "sp_track_artist")
        self._add_labeled_value(parent_tag, "Album", "sp_track_album")
        self._add_labeled_value(parent_tag, "Shuffle", "sp_shuffle_state")
        self._add_labeled_value(parent_tag, "Playlist", "sp_playlist_state")
        self._add_labeled_value(parent_tag, "Library", "sp_liked_status")

        dpg.add_spacer(height=10, parent=parent_tag)
        dpg.add_separator(parent=parent_tag)
        dpg.add_spacer(height=6, parent=parent_tag)

        dpg.add_text("Pad Mapping", parent=parent_tag, color=_TEXT_SECTION)
        dpg.add_text("Pad 1: Play/Pause (API)", parent=parent_tag)
        dpg.add_text("Pad 2: Next Track (API)", parent=parent_tag)
        dpg.add_text("Pad 3: Previous Track (API)", parent=parent_tag)
        dpg.add_text("Pad 4: Like/Unlike (API)", parent=parent_tag)
        dpg.add_text("Pad 5: Toggle Shuffle (API)", parent=parent_tag)
        dpg.add_text("Pad 6: DJ Mix (keystroke)", parent=parent_tag)
        dpg.add_text("Pad 7: Add to Playlist (API)", parent=parent_tag)
        dpg.add_text("Pad 8: Remove from Playlist (API)", parent=parent_tag)
        dpg.add_text(
            "Pads 1–5, 7–8 use the Spotify Web API. Pad 6 (DJ Mix) sends a keystroke "
            "and requires Spotify to be in focus. Playlist actions work only when "
            "playing from a playlist context.",
            parent=parent_tag,
            wrap=260,
            color=_TEXT_MUTED,
        )

        dpg.add_spacer(height=10, parent=parent_tag)
        dpg.add_separator(parent=parent_tag)
        dpg.add_spacer(height=6, parent=parent_tag)

        dpg.add_text("Requirements", parent=parent_tag, color=_TEXT_SECTION)
        dpg.add_text(
            "Spotify Premium is required for playback control through the Web API on most devices.",
            parent=parent_tag,
            wrap=260,
            color=_TEXT_MUTED,
        )
        dpg.add_text(
            "You must register an app at developer.spotify.com and paste its Client ID above; "
            "PKCE means no embedded client secret in this app.",
            parent=parent_tag,
            wrap=260,
            color=_TEXT_MUTED,
        )
        dpg.add_text(
            "New Spotify developer apps are limited to 5 users in development mode until you request extension.",
            parent=parent_tag,
            wrap=260,
            color=_TEXT_MUTED,
        )

        self._refresh_ui()

    def _add_labeled_value(self, parent_tag: str, label: str, value_tag: str) -> None:
        import dearpygui.dearpygui as dpg

        with dpg.group(horizontal=True, parent=parent_tag):
            dpg.add_text(f"{label}:", color=_TEXT_MUTED)
            dpg.add_text("—", tag=value_tag, wrap=180)

    def _on_client_id_changed(self, value: str) -> None:
        self.client_id = str(value).strip()
        self._persist_settings()

    def _on_connect_clicked(self) -> None:
        if not self.client_id:
            self._log("SPOTIFY", "Client ID required", color=(255, 180, 80), level="warning")
            return
        threading.Thread(target=self._run_auth_flow, daemon=True).start()

    def _run_auth_flow(self) -> None:
        try:
            tokens = start_auth_flow(self.client_id, self.redirect_port)
            self._access_token = tokens["access_token"]
            self._refresh_token = tokens.get("refresh_token", "")
            self._token_expires_at = time.time() + tokens.get("expires_in", 3600)
            self._api = SpotifyAPI(self._access_token)
            profile = self._api.get_user_profile()
            if profile:
                self._display_name = profile.get("display_name", "")
            self._last_error = ""
            self._persist_settings()
            log.info("Spotify: connected as %s", self._display_name)
        except Exception as exc:
            self._last_error = str(exc)
            self._log("SPOTIFY", f"OAuth failed: {exc}", color=(255, 80, 80), level="error")

    def _do_token_refresh(self) -> bool:
        with self._token_lock:
            if not self._refresh_token or not self.client_id:
                return False
            if self._token_expires_at and time.time() < self._token_expires_at - 120:
                return True
            try:
                tokens = refresh_access_token(self.client_id, self._refresh_token)
                self._access_token = tokens["access_token"]
                if "refresh_token" in tokens:
                    self._refresh_token = tokens["refresh_token"]
                self._token_expires_at = time.time() + tokens.get("expires_in", 3600)
                if self._api:
                    self._api.access_token = self._access_token
                else:
                    self._api = SpotifyAPI(self._access_token)
                self._last_error = ""
                self._persist_settings()
                return True
            except Exception as exc:
                self._log("SPOTIFY", f"Token refresh failed: {exc}", color=(255, 80, 80), level="error")
                self._last_error = f"Token refresh failed: {exc}"
                self._access_token = ""
                self._token_expires_at = 0.0
                self._api = None
                self._persist_settings()
                return False

    def _api_call(self, fn):
        """Call an API thunk (zero-arg callable), handling token refresh transparently."""
        if not self._api or not self._connected:
            return None
        try:
            if self._token_expires_at and time.time() > self._token_expires_at - 60:
                self._do_token_refresh()
            return fn()
        except TokenExpiredError:
            if self._do_token_refresh():
                return fn()
            return None
        except Exception as exc:
            self._log("SPOTIFY", f"API error: {exc}", color=(255, 180, 80), level="warning")
            return None

    def _poll_playback(self) -> None:
        if not self._api or not self._connected:
            self._poll_running = False
            return
        try:
            state = self._api_call(lambda: self._api.get_current_playback() if self._api else None)
            if state:
                item = state.get("item", {})
                self._track_name = item.get("name", "Unknown")
                artists = item.get("artists", [])
                self._track_artist = ", ".join(a.get("name", "") for a in artists) if artists else "Unknown"
                self._track_album = item.get("album", {}).get("name", "")
                self._is_playing = state.get("is_playing", False)
                self._shuffle_on = state.get("shuffle_state", False)
                self._repeat_mode = state.get("repeat_state", "off")
                progress_ms = state.get("progress_ms", 0)
                duration_ms = item.get("duration_ms", 1)
                self._progress_fraction = progress_ms / max(duration_ms, 1)
                self._position_label = f"{progress_ms // 60000}:{(progress_ms // 1000) % 60:02d}"
                self._duration_label = f"{duration_ms // 60000}:{(duration_ms // 1000) % 60:02d}"
                self._current_track_uri = item.get("uri", "")
                ctx = state.get("context")
                if ctx and ctx.get("type") == "playlist":
                    uri = ctx.get("uri", "")
                    parts = uri.split(":")
                    self._current_playlist_id = parts[2] if len(parts) == 3 else ""
                else:
                    self._current_playlist_id = ""
                track_id = item.get("id")
                if track_id:
                    liked = self._api_call(lambda: self._api.is_track_liked(track_id) if self._api else None)
                    if liked is not None:
                        self._liked = liked
            else:
                self._track_name = "Nothing playing"
                self._track_artist = ""
                self._track_album = ""
                self._is_playing = False
                self._shuffle_on = False
                self._repeat_mode = "off"
                self._liked = False
                self._progress_fraction = 0.0
                self._position_label = "0:00"
                self._duration_label = "0:00"
                self._current_track_uri = ""
                self._current_playlist_id = ""
        except Exception as exc:
            self._log("SPOTIFY", f"Poll error: {exc}", color=(255, 180, 80), level="warning")
        finally:
            self._poll_running = False

    def _on_disconnect_clicked(self) -> None:
        self._access_token = ""
        self._refresh_token = ""
        self._token_expires_at = 0.0
        self._display_name = ""
        self._api = None
        self._persist_settings()
        self._refresh_ui()

    # -- center tab ---------------------------------------------------------

    def register_windows(self) -> list[dict]:
        return [{"id": "spotify_player", "title": "Spotify", "default_open": True}]

    def build_window(self, window_id: str, parent_tag: str) -> None:
        if window_id != "spotify_player":
            return
        import dearpygui.dearpygui as dpg

        dpg.add_text("Now Playing", parent=parent_tag, color=SPOTIFY_GREEN)
        dpg.add_text(
            "Large view mirrors the sidebar so you can read track info from a distance while performing.",
            parent=parent_tag,
            wrap=720,
            color=_TEXT_MUTED,
        )
        dpg.add_spacer(height=8, parent=parent_tag)

        dpg.add_text(
            "",
            tag="sp_center_track_name",
            parent=parent_tag,
            color=(240, 242, 248),
        )
        dpg.add_text(
            "",
            tag="sp_center_artist",
            parent=parent_tag,
            color=_TEXT_SECTION,
        )
        dpg.add_spacer(height=12, parent=parent_tag)

        with dpg.group(horizontal=True, parent=parent_tag):
            dpg.add_button(
                tag="sp_center_btn_prev",
                label="Previous",
                width=100,
                callback=lambda: self._center_prev(),
            )
            dpg.add_button(
                tag="sp_center_btn_play",
                label="Play/Pause",
                width=120,
                callback=lambda: self._center_play_pause(),
            )
            dpg.add_button(
                tag="sp_center_btn_next",
                label="Next",
                width=100,
                callback=lambda: self._center_next(),
            )
        dpg.add_spacer(height=8, parent=parent_tag)

        dpg.add_progress_bar(
            tag="sp_center_progress_bar",
            parent=parent_tag,
            default_value=self._progress_fraction,
            width=-1,
        )
        dpg.add_text(
            "",
            tag="sp_center_progress_text",
            parent=parent_tag,
            color=_TEXT_MUTED,
        )

        dpg.add_spacer(height=10, parent=parent_tag)
        with dpg.group(horizontal=True, parent=parent_tag):
            dpg.add_text("Shuffle:", color=_TEXT_MUTED)
            dpg.add_text("", tag="sp_center_shuffle", color=_TEXT_SECTION)
            dpg.add_spacer(width=20)
            dpg.add_text("Playlist:", color=_TEXT_MUTED)
            dpg.add_text("", tag="sp_center_playlist", color=_TEXT_SECTION)
        dpg.add_spacer(height=8, parent=parent_tag)
        dpg.add_button(
            tag="sp_center_btn_like",
            label="Like",
            width=120,
            parent=parent_tag,
            callback=lambda: self._center_like(),
        )

        self._refresh_ui()

    def _center_prev(self) -> None:
        threading.Thread(
            target=lambda: self._api_call(lambda: self._api.previous_track() if self._api else None),
            daemon=True,
        ).start()

    def _center_play_pause(self) -> None:
        threading.Thread(
            target=lambda: self._api_call(lambda: self._api.toggle_playback() if self._api else None),
            daemon=True,
        ).start()

    def _center_next(self) -> None:
        threading.Thread(
            target=lambda: self._api_call(lambda: self._api.next_track() if self._api else None),
            daemon=True,
        ).start()

    def _center_like(self) -> None:
        def _toggle() -> None:
            track_id = self._api_call(lambda: self._api.get_current_track_id() if self._api else None)
            if track_id:
                liked = self._api_call(lambda: self._api.toggle_like(track_id) if self._api else None)
                if liked is not None:
                    self._liked = liked

        threading.Thread(target=_toggle, daemon=True).start()

    # -- persistence + UI refresh -------------------------------------------

    def _persist_settings(self) -> None:
        settings.put(
            SETTINGS_KEY,
            {
                "client_id": self.client_id,
                "redirect_port": self.redirect_port,
                "access_token": self._access_token,
                "refresh_token": self._refresh_token,
                "token_expires_at": self._token_expires_at,
            },
        )

    def _shuffle_label(self) -> str:
        return "On" if self._shuffle_on else "Off"

    def _repeat_label(self) -> str:
        return {"off": "Off", "context": "Playlist", "track": "Track"}.get(
            self._repeat_mode, self._repeat_mode
        )

    def _liked_label(self) -> str:
        return "Liked" if self._liked else "Not liked"

    def _connection_status_text(self) -> str:
        if self._connected and self._display_name:
            return f"Connected as {self._display_name}"
        if self._connected:
            return "Connected"
        return "Not connected"

    def _refresh_ui(self) -> None:
        if os.environ.get("MACROPAD_HEADLESS"):
            return
        try:
            import dearpygui.dearpygui as dpg
            if not self._dpg_ready:
                if not dpg.is_dearpygui_running():
                    return
                self._dpg_ready = True
        except Exception:
            return

        conn_text = self._connection_status_text()
        if self._last_error:
            conn_text += f" — {self._last_error}"
        conn_color = _CONNECTED_OK if self._connected else _CONNECTED_BAD

        track = self._track_name if self._connected else "—"
        artist = self._track_artist if self._connected else "—"
        album = self._track_album if self._connected else "—"
        sh_text = f"Shuffle: {self._shuffle_label()}" if self._connected else "—"
        pl_text = "In playlist" if self._current_playlist_id else "No playlist" if self._connected else "—"
        lk_text = self._liked_label() if self._connected else "—"

        self._set_text_if_exists(dpg, "sp_connection_status", conn_text, conn_color)
        self._set_text_if_exists(dpg, "sp_track_name", track, (230, 232, 238))
        self._set_text_if_exists(dpg, "sp_track_artist", artist, (230, 232, 238))
        self._set_text_if_exists(dpg, "sp_track_album", album, (230, 232, 238))
        self._set_text_if_exists(dpg, "sp_shuffle_state", sh_text, _STATUS_IDLE)
        self._set_text_if_exists(dpg, "sp_playlist_state", pl_text, _STATUS_IDLE)
        self._set_text_if_exists(dpg, "sp_liked_status", lk_text, _STATUS_IDLE)

        center_track = self._track_name if self._connected else "Not connected"
        center_artist = self._track_artist if self._connected else "Open Spotify and connect to stream"
        self._set_text_if_exists(dpg, "sp_center_track_name", center_track, (240, 242, 248))
        self._set_text_if_exists(dpg, "sp_center_artist", center_artist, _TEXT_SECTION)

        prog_label = (
            f"{self._position_label} / {self._duration_label}" if self._connected else "— / —"
        )
        self._set_text_if_exists(dpg, "sp_center_progress_text", prog_label, _TEXT_MUTED)

        if dpg.does_item_exist("sp_center_progress_bar"):
            dpg.set_value("sp_center_progress_bar", self._progress_fraction if self._connected else 0.0)

        self._set_text_if_exists(
            dpg, "sp_center_shuffle", self._shuffle_label() if self._connected else "—", _TEXT_SECTION
        )
        pl_center = "In playlist" if self._current_playlist_id else "No playlist"
        self._set_text_if_exists(
            dpg, "sp_center_playlist", pl_center if self._connected else "—", _TEXT_SECTION
        )

        like_lbl = "Unlike" if self._liked else "Like"
        if dpg.does_item_exist("sp_center_btn_like"):
            dpg.configure_item("sp_center_btn_like", label=like_lbl)

        play_lbl = "Pause" if self._is_playing else "Play"
        if dpg.does_item_exist("sp_center_btn_play"):
            dpg.configure_item("sp_center_btn_play", label=play_lbl)

        self._set_button_enabled_if_exists(dpg, "sp_btn_connect", not self._connected)
        self._set_button_enabled_if_exists(dpg, "sp_btn_disconnect", self._connected)

    @staticmethod
    def _set_text_if_exists(dpg, tag: str, value: str, color: tuple[int, int, int]) -> None:
        if dpg.does_item_exist(tag):
            dpg.set_value(tag, value)
            dpg.configure_item(tag, color=color)

    @staticmethod
    def _set_button_enabled_if_exists(dpg, tag: str, enabled: bool) -> None:
        if dpg.does_item_exist(tag):
            dpg.configure_item(tag, enabled=enabled)
