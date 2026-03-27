"""Spotify OAuth PKCE flow — handles authorization without a backend server."""

import base64
import hashlib
import http.server
import secrets
import threading
import urllib.parse
import webbrowser
from typing import Optional

import requests

SCOPES = [
    "user-modify-playback-state",
    "user-read-playback-state",
    "user-read-currently-playing",
    "user-library-modify",
    "user-library-read",
]

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"


def _generate_verifier(length: int = 64) -> str:
    """Generate a code verifier (43-128 unreserved URI chars)."""
    return secrets.token_urlsafe(length)[:length]


def _generate_challenge(verifier: str) -> str:
    """Derive the S256 code challenge from the verifier."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """Captures the OAuth redirect and extracts the authorization code."""

    auth_code: Optional[str] = None
    error: Optional[str] = None
    expected_state: Optional[str] = None

    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        returned_state = params.get("state", [None])[0]
        if _OAuthCallbackHandler.expected_state and returned_state != _OAuthCallbackHandler.expected_state:
            _OAuthCallbackHandler.error = "state_mismatch"
            body = b"<html><body><h2>Security error</h2><p>State parameter mismatch. Try again.</p></body></html>"
        elif "code" in params:
            _OAuthCallbackHandler.auth_code = params["code"][0]
            body = b"<html><body><h2>Success!</h2><p>You can close this tab and return to MIDI Macropad.</p></body></html>"
        else:
            _OAuthCallbackHandler.error = params.get("error", ["unknown"])[0]
            body = b"<html><body><h2>Authorization failed</h2><p>Check MIDI Macropad for details.</p></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # suppress console output


def start_auth_flow(client_id: str, redirect_port: int, timeout: float = 120.0) -> dict:
    """Start OAuth PKCE flow. Opens browser, waits for redirect, exchanges code for tokens.

    Returns dict with keys: access_token, refresh_token, expires_in, scope, token_type.
    Raises RuntimeError on failure.
    """
    if not client_id:
        raise RuntimeError("Client ID is required")

    verifier = _generate_verifier()
    challenge = _generate_challenge(verifier)
    state = secrets.token_urlsafe(16)
    redirect_uri = f"http://127.0.0.1:{redirect_port}"

    _OAuthCallbackHandler.auth_code = None
    _OAuthCallbackHandler.error = None
    _OAuthCallbackHandler.expected_state = state

    server = http.server.HTTPServer(("127.0.0.1", redirect_port), _OAuthCallbackHandler)
    server.timeout = timeout

    auth_params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPES),
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(auth_params)}"
    webbrowser.open(auth_url)

    # Wait for one request (the redirect)
    server.handle_request()
    server.server_close()

    if _OAuthCallbackHandler.error:
        raise RuntimeError(f"Authorization denied: {_OAuthCallbackHandler.error}")
    if not _OAuthCallbackHandler.auth_code:
        raise RuntimeError("No authorization code received (timeout or user closed browser)")

    # Exchange code for tokens
    token_data = {
        "grant_type": "authorization_code",
        "code": _OAuthCallbackHandler.auth_code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    resp = requests.post(TOKEN_URL, data=token_data, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Token exchange failed ({resp.status_code}): {resp.text}")

    return resp.json()


def refresh_access_token(client_id: str, refresh_token: str) -> dict:
    """Refresh an expired access token. Returns new token dict."""
    if not client_id or not refresh_token:
        raise RuntimeError("Client ID and refresh token are required")

    token_data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    resp = requests.post(TOKEN_URL, data=token_data, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"Token refresh failed ({resp.status_code}): {resp.text}")

    return resp.json()
