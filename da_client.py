"""DeviantArt API client — OAuth2 PKCE auth, Sta.sh upload, gallery publish."""

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

import requests

_DIR = os.path.dirname(os.path.abspath(__file__))
_SETTINGS_PATH = os.path.join(_DIR, "da_settings.json")
_TOKENS_PATH = os.path.join(_DIR, "da_tokens.json")

_DA_AUTH_URL = "https://www.deviantart.com/oauth2/authorize"
_DA_TOKEN_URL = "https://www.deviantart.com/oauth2/token"
_DA_SUBMIT_URL = "https://www.deviantart.com/api/v1/oauth2/stash/submit"
_DA_PUBLISH_URL = "https://www.deviantart.com/api/v1/oauth2/stash/publish"

_UA = "CaptionCreator/1.0"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def load_client_id() -> Optional[str]:
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f).get("client_id") or None
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def save_client_id(client_id: str) -> None:
    with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump({"client_id": client_id.strip()}, f)


# ---------------------------------------------------------------------------
# Token storage
# ---------------------------------------------------------------------------

def _load_tokens() -> Optional[dict]:
    try:
        with open(_TOKENS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_tokens(tokens: dict) -> None:
    with open(_TOKENS_PATH, "w", encoding="utf-8") as f:
        json.dump(tokens, f)


# ---------------------------------------------------------------------------
# OAuth2 PKCE helpers
# ---------------------------------------------------------------------------

def _pkce_pair() -> tuple:
    """Return (code_verifier, code_challenge) strings."""
    verifier_bytes = secrets.token_bytes(64)
    verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    code: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            _CallbackHandler.code = params["code"][0]
            body = b"<h2>Authorized! You can close this tab.</h2>"
        else:
            _CallbackHandler.error = params.get("error", ["unknown"])[0]
            body = b"<h2>Authorization failed. You can close this tab.</h2>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, *_):
        pass  # silence access log


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def da_authorize(client_id: str) -> dict:
    """Run the PKCE authorization flow. Opens the browser; blocks until done."""
    _CallbackHandler.code = None
    _CallbackHandler.error = None

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)

    server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "stash publish",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = _DA_AUTH_URL + "?" + urllib.parse.urlencode(params)
    webbrowser.open(auth_url)

    server.serve_forever()  # blocks until handler calls server.shutdown()

    if _CallbackHandler.error:
        raise RuntimeError(f"DeviantArt authorization failed: {_CallbackHandler.error}")
    if not _CallbackHandler.code:
        raise RuntimeError("No authorization code received from DeviantArt.")

    resp = requests.post(_DA_TOKEN_URL, data={
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code": _CallbackHandler.code,
        "code_verifier": verifier,
    }, headers={"User-Agent": _UA}, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    tokens = {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "expires_at": time.time() + int(data.get("expires_in", 3600)) - 30,
    }
    _save_tokens(tokens)
    return tokens


def da_ensure_token(client_id: str) -> str:
    """Return a valid access token, refreshing or re-authorizing as needed."""
    tokens = _load_tokens()

    if tokens and time.time() < tokens.get("expires_at", 0):
        return tokens["access_token"]

    # Try silent refresh
    if tokens and tokens.get("refresh_token"):
        try:
            resp = requests.post(_DA_TOKEN_URL, data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": tokens["refresh_token"],
            }, headers={"User-Agent": _UA}, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            tokens = {
                "access_token": data["access_token"],
                "refresh_token": data.get("refresh_token", tokens["refresh_token"]),
                "expires_at": time.time() + int(data.get("expires_in", 3600)) - 30,
            }
            _save_tokens(tokens)
            return tokens["access_token"]
        except Exception:
            pass  # fall through to full re-auth

    # Full PKCE flow
    tokens = da_authorize(client_id)
    return tokens["access_token"]


def da_stash_submit(access_token: str, file_path: str, title: str) -> str:
    """Upload file to Sta.sh. Returns stackid string."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": _UA,
        "Accept-Encoding": "gzip",
    }
    delay = 1.0
    for attempt in range(3):
        with open(file_path, "rb") as fh:
            fname = os.path.basename(file_path)
            resp = requests.post(
                _DA_SUBMIT_URL,
                headers=headers,
                data={"title": title or "Caption Creator Upload", "is_mature": "false"},
                files={"file": (fname, fh)},
                timeout=60,
            )
        if resp.status_code == 429:
            time.sleep(delay)
            delay *= 2
            continue
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "success":
            raise RuntimeError(f"Sta.sh submit error: {data}")
        return str(data["stackid"])
    raise RuntimeError("DeviantArt upload failed after 3 attempts (rate limited).")


def da_stash_publish(access_token: str, stackid: str) -> str:
    """Publish a Sta.sh item to the gallery. Returns deviation URL."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": _UA,
        "Accept-Encoding": "gzip",
    }
    resp = requests.post(
        _DA_PUBLISH_URL,
        headers=headers,
        data={
            "stackid": stackid,
            "is_mature": "false",
            "agree_tos": "true",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(f"Sta.sh publish error: {data}")
    return data.get("url", "https://www.deviantart.com/notifications/feedback")
