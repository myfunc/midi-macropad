"""Spotify Web API client wrapper."""

import time

import requests

from logger import get_logger

log = get_logger("spotify_api")


class TokenExpiredError(Exception):
    """Raised when the access token has expired and needs refresh."""

    pass


class SpotifyAPI:
    def __init__(self, access_token: str = ""):
        self.access_token = access_token
        self.base_url = "https://api.spotify.com/v1"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.access_token}"}

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = f"{self.base_url}{path}"
        resp = getattr(requests, method)(url, headers=self._headers(), timeout=10, **kwargs)
        if resp.status_code == 401:
            raise TokenExpiredError("Access token expired")
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 1))
            log.warning("Spotify rate limited, waiting %ds", retry_after)
            time.sleep(retry_after)
            return self._request(method, path, **kwargs)
        return resp

    def get_current_playback(self) -> dict | None:
        resp = self._request("get", "/me/player")
        if resp.status_code == 204 or resp.status_code == 200 and not resp.content:
            return None
        if resp.status_code == 200:
            return resp.json()
        log.warning("get_current_playback: %d %s", resp.status_code, resp.text[:200])
        return None

    def toggle_playback(self) -> None:
        state = self.get_current_playback()
        if state and state.get("is_playing"):
            self._request("put", "/me/player/pause")
        else:
            self._request("put", "/me/player/play")

    def next_track(self) -> None:
        self._request("post", "/me/player/next")

    def previous_track(self) -> None:
        self._request("post", "/me/player/previous")

    def toggle_like(self, track_id: str) -> bool:
        if not track_id:
            return False
        liked = self.is_track_liked(track_id)
        if liked:
            resp = self._request("delete", f"/me/tracks?ids={track_id}")
            if resp.status_code in (200, 204):
                return False
            log.warning("toggle_like (unlike): %d %s", resp.status_code, resp.text[:200])
            return liked
        else:
            resp = self._request("put", "/me/tracks", json={"ids": [track_id]})
            if resp.status_code in (200, 204):
                return True
            log.warning("toggle_like (like): %d %s", resp.status_code, resp.text[:200])
            return liked

    def toggle_shuffle(self, current_state: bool) -> bool:
        """Returns new shuffle state, or current on failure."""
        new_state = not current_state
        resp = self._request("put", f"/me/player/shuffle?state={str(new_state).lower()}")
        if resp.status_code in (200, 204):
            return new_state
        log.warning("toggle_shuffle: %d %s", resp.status_code, resp.text[:200])
        return current_state

    def cycle_repeat(self, current_state: str) -> str:
        """Returns new repeat mode, or current on failure."""
        cycle = {"off": "context", "context": "track", "track": "off"}
        new_state = cycle.get(current_state, "off")
        resp = self._request("put", f"/me/player/repeat?state={new_state}")
        if resp.status_code in (200, 204):
            return new_state
        log.warning("cycle_repeat: %d %s", resp.status_code, resp.text[:200])
        return current_state

    def get_current_track_id(self) -> str | None:
        state = self.get_current_playback()
        if not state:
            return None
        item = state.get("item")
        if item:
            return item.get("id")
        return None

    def is_track_liked(self, track_id: str) -> bool:
        if not track_id:
            return False
        resp = self._request("get", f"/me/tracks/contains?ids={track_id}")
        if resp.status_code == 200:
            result = resp.json()
            return bool(result and result[0])
        return False

    def get_current_playlist_id(self) -> str | None:
        """Extract playlist ID from current playback context, if any."""
        state = self.get_current_playback()
        if not state:
            return None
        ctx = state.get("context")
        if ctx and ctx.get("type") == "playlist":
            uri = ctx.get("uri", "")
            parts = uri.split(":")
            if len(parts) == 3:
                return parts[2]
        return None

    def add_to_playlist(self, playlist_id: str, track_uri: str) -> bool:
        resp = self._request(
            "post",
            f"/playlists/{playlist_id}/tracks",
            json={"uris": [track_uri]},
        )
        if resp.status_code in (200, 201):
            return True
        log.warning("add_to_playlist: %d %s", resp.status_code, resp.text[:200])
        return False

    def remove_from_playlist(self, playlist_id: str, track_uri: str) -> bool:
        resp = self._request(
            "delete",
            f"/playlists/{playlist_id}/tracks",
            json={"tracks": [{"uri": track_uri}]},
        )
        if resp.status_code in (200, 204):
            return True
        log.warning("remove_from_playlist: %d %s", resp.status_code, resp.text[:200])
        return False

    def get_user_profile(self) -> dict | None:
        resp = self._request("get", "/me")
        if resp.status_code == 200:
            return resp.json()
        return None
