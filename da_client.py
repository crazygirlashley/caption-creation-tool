"""DeviantArt API client — OAuth2 PKCE auth, draft upload via Sta.sh, gallery publish."""

import base64
import hashlib
import json
import logging
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

# DeviantArt requires redirect_uri to match the registered value EXACTLY, including
# port — it has no loopback/wildcard-port exception (unlike e.g. Google's RFC 8252
# support). So the callback listener must bind a fixed port, and this exact URI
# (scheme + host + port, no trailing slash) must be registered at
# https://www.deviantart.com/studio/apps.
REDIRECT_PORT = 24858

log = logging.getLogger(__name__)


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


def da_logout() -> None:
    """Delete cached tokens so the next login triggers a fresh OAuth flow."""
    try:
        os.unlink(_TOKENS_PATH)
        log.info("Logged out — cached tokens removed.")
    except FileNotFoundError:
        log.info("No cached tokens found — already logged out.")


def da_ensure_silent_token(client_id: str) -> str:
    """Return a valid access token using only the cache or a silent refresh.
    Never opens a browser. Raises RuntimeError if interactive login is required."""
    tokens = _load_tokens()

    if tokens and time.time() < tokens.get("expires_at", 0):
        log.debug("Cached token is still valid.")
        return tokens["access_token"]

    if tokens and tokens.get("refresh_token"):
        log.info("Token expired — attempting silent refresh...")
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
            log.info("Token refreshed silently.")
            return tokens["access_token"]
        except Exception as exc:
            log.warning("Silent refresh failed: %s", exc)

    raise RuntimeError(
        "Not logged in to DeviantArt. Use the DA Login button to authenticate."
    )


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
            log.info("Authorization code received from DeviantArt.")
            body = b"<h2>Authorized! You can close this tab.</h2>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        elif "error" in params:
            _CallbackHandler.error = params["error"][0]
            log.warning("DeviantArt returned an error in the callback: %s", _CallbackHandler.error)
            body = b"<h2>Authorization failed. You can close this tab.</h2>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            # Ignore browser noise (favicon, prefetch, etc.) — only act on the real callback
            self.send_response(204)
            self.end_headers()

    def log_message(self, *_):
        pass  # silence access log


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def da_authorize(client_id: str, open_browser=None) -> dict:
    """Run the PKCE authorization flow. Calls open_browser(url) or webbrowser.open(url)."""
    _CallbackHandler.code = None
    _CallbackHandler.error = None

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)

    try:
        server = HTTPServer(("127.0.0.1", REDIRECT_PORT), _CallbackHandler)
    except OSError as exc:
        log.error("Could not bind callback listener on port %d: %s", REDIRECT_PORT, exc)
        raise RuntimeError(
            f"Could not start the local OAuth callback listener on port "
            f"{REDIRECT_PORT} (it may already be in use by another program, "
            f"or another copy of Caption Creator): {exc}"
        ) from exc
    redirect_uri = f"http://127.0.0.1:{REDIRECT_PORT}"
    log.info("OAuth2 PKCE flow starting — callback listener on port %d", REDIRECT_PORT)

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
    log.info("Auth URL: %s", auth_url)

    log.info("Opening browser for authorization...")
    log.info(
        "Tip: if DeviantArt shows a sign-up page, look for 'Sign In' or "
        "'Already a member?' to log into your existing account — "
        "the authorization will continue automatically."
    )
    if open_browser:
        open_browser(auth_url)
    else:
        webbrowser.open(auth_url)

    log.info("Waiting for authorization callback (5-minute timeout)...")
    # Watchdog: shut down the server after 5 minutes so the thread doesn't hang forever
    def _watchdog():
        log.error("Authorization timed out after 5 minutes — no callback received.")
        server.shutdown()
    watchdog = threading.Timer(300, _watchdog)
    watchdog.daemon = True
    watchdog.start()
    server.serve_forever()
    watchdog.cancel()
    server.server_close()

    if _CallbackHandler.error:
        log.error("Authorization failed — DeviantArt returned error: %s", _CallbackHandler.error)
        raise RuntimeError(f"DeviantArt authorization failed: {_CallbackHandler.error}")
    if not _CallbackHandler.code:
        raise RuntimeError("DeviantArt authorization timed out or was cancelled.")

    log.info("Callback received — exchanging code for tokens...")
    try:
        resp = requests.post(_DA_TOKEN_URL, data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code": _CallbackHandler.code,
            "code_verifier": verifier,
        }, headers={"User-Agent": _UA}, timeout=15)
        resp.raise_for_status()
    except requests.HTTPError as exc:
        log.error("Token exchange failed (HTTP %s): %s", exc.response.status_code, exc.response.text)
        raise
    except Exception as exc:
        log.error("Token exchange request failed: %s", exc)
        raise
    data = resp.json()

    tokens = {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", ""),
        "expires_at": time.time() + int(data.get("expires_in", 3600)) - 30,
    }
    _save_tokens(tokens)
    log.info("Login successful — token saved, expires in %ds.", int(data.get("expires_in", 3600)))
    return tokens


def da_ensure_token(client_id: str, open_browser=None) -> str:
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
    tokens = da_authorize(client_id, open_browser=open_browser)
    return tokens["access_token"]


def da_has_cached_token() -> bool:
    """Return True if a non-expired token exists in the cache (no network call)."""
    tokens = _load_tokens()
    return bool(tokens and time.time() < tokens.get("expires_at", 0))


def da_stash_submit(access_token: str, file_path: str, title: str) -> str:
    """Upload file as a private draft on DeviantArt (via Sta.sh). Returns stackid string."""
    file_size = os.path.getsize(file_path)
    log.info("Uploading %s (%.1f KB) to Sta.sh as '%s'...",
             os.path.basename(file_path), file_size / 1024, title or "Caption Creator Upload")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": _UA,
        "Accept-Encoding": "gzip",
    }
    delay = 1.0
    for attempt in range(3):
        log.info("Sta.sh submit attempt %d/3...", attempt + 1)
        with open(file_path, "rb") as fh:
            fname = os.path.basename(file_path)
            resp = requests.post(
                _DA_SUBMIT_URL,
                headers=headers,
                # /stash/submit's schema is additionalProperties:false and has no
                # is_mature field — that's a /stash/publish-time attribute, not submit-time.
                data={"title": title or "Caption Creator Upload"},
                files={"file": (fname, fh)},
                timeout=60,
            )
        log.debug("Sta.sh response: HTTP %d", resp.status_code)
        if resp.status_code == 401:
            log.error("HTTP 401 Unauthorized — token may be expired or revoked. Try DA Login again.")
            resp.raise_for_status()
        if resp.status_code == 403:
            log.error("HTTP 403 Forbidden — check that your app has 'stash' scope. Body: %s", resp.text[:300])
            resp.raise_for_status()
        if resp.status_code == 429:
            log.warning("Rate limited — retrying in %.0fs...", delay)
            time.sleep(delay)
            delay *= 2
            continue
        if resp.status_code >= 400:
            log.error("HTTP %d error from Sta.sh: %s", resp.status_code, resp.text[:500])
            resp.raise_for_status()
        try:
            data = resp.json()
        except Exception:
            log.error("Non-JSON response from Sta.sh: %s", resp.text[:300])
            raise RuntimeError(f"Unexpected response from Sta.sh (HTTP {resp.status_code})")
        if data.get("status") != "success":
            log.error("Sta.sh returned non-success: %s", data)
            raise RuntimeError(f"Sta.sh submit error: {data}")
        # Only "status" and "itemid" are guaranteed by the API — "stackid" is optional,
        # but we need it to publish, so fail clearly rather than KeyError-ing.
        if "stackid" not in data:
            log.error("Sta.sh response missing stackid: %s", data)
            raise RuntimeError(
                f"Sta.sh submit succeeded (itemid={data.get('itemid')}) but returned no "
                "stackid, so it can't be published."
            )
        log.info("Upload successful — itemid=%s  stackid=%s", data.get("itemid"), data["stackid"])
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
