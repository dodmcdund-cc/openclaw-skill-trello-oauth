"""OAuth 1.0a 3-step flow pour Trello.

Step 1: GET https://trello.com/1/OAuthGetRequestToken
Step 2: user autorise via https://trello.com/1/OAuthAuthorizeToken
Step 3: POST https://trello.com/1/OAuthGetAccessToken avec oauth_verifier

Le serveur HTTP local (port configurable) écoute la callback.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlencode, urlparse, parse_qs

import requests
from requests_oauthlib import OAuth1


TRELLO_BASE = "https://trello.com/1"
TRELLO_API_BASE = "https://api.trello.com/1"


@dataclass
class OAuthRequestToken:
    oauth_token: str
    oauth_token_secret: str
    callback_confirmed: str


@dataclass
class OAuthAccessToken:
    oauth_token: str
    oauth_token_secret: str


class TrelloOAuthError(Exception):
    pass


class CallbackTimeoutError(TrelloOAuthError):
    pass


# ---------------------------------------------------------------------------
# OAuth 1.0a steps
# ---------------------------------------------------------------------------

def get_request_token(
    api_key: str,
    oauth_secret: str,
    callback_url: str,
    timeout: int = 30,
) -> OAuthRequestToken:
    """Step 1: obtain a request token.

    Trello renvoie `oauth_token`, `oauth_token_secret`, `oauth_callback_confirmed`.
    """
    url = f"{TRELLO_BASE}/OAuthGetRequestToken"
    auth = OAuth1(
        client_key=api_key,
        client_secret=oauth_secret,
        callback_uri=callback_url,
    )
    resp = requests.post(url, auth=auth, timeout=timeout)
    if resp.status_code != 200:
        raise TrelloOAuthError(
            f"OAuthGetRequestToken failed ({resp.status_code}): {resp.text}"
        )
    data = dict(parse_qs(resp.text))
    for key in ("oauth_token", "oauth_token_secret", "oauth_callback_confirmed"):
        if key not in data:
            raise TrelloOAuthError(
                f"Réponse OAuthGetRequestToken invalide: clé '{key}' manquante. "
                f"Body: {resp.text}"
            )
    return OAuthRequestToken(
        oauth_token=data["oauth_token"][0],
        oauth_token_secret=data["oauth_token_secret"][0],
        callback_confirmed=data["oauth_callback_confirmed"][0],
    )


def build_authorize_url(
    request_token: OAuthRequestToken,
    scope: str = "read,write",
    expiration: str = "30days",
    name: str = "OpenClaw",
) -> str:
    """Construit l'URL d'autorisation que l'utilisateur ouvre dans son navigateur."""
    params = {
        "oauth_token": request_token.oauth_token,
        "scope": scope,
        "expiration": expiration,
        "name": name,
    }
    return f"{TRELLO_BASE}/OAuthAuthorizeToken?{urlencode(params)}"


def get_access_token(
    api_key: str,
    oauth_secret: str,
    request_token: OAuthRequestToken,
    oauth_verifier: str,
    timeout: int = 30,
) -> OAuthAccessToken:
    """Step 3: échange le request token + oauth_verifier contre l'access token final."""
    url = f"{TRELLO_BASE}/OAuthGetAccessToken"
    auth = OAuth1(
        client_key=api_key,
        client_secret=oauth_secret,
        resource_owner_key=request_token.oauth_token,
        resource_owner_secret=request_token.oauth_token_secret,
        verifier=oauth_verifier,
    )
    resp = requests.post(url, auth=auth, timeout=timeout)
    if resp.status_code != 200:
        raise TrelloOAuthError(
            f"OAuthGetAccessToken failed ({resp.status_code}): {resp.text}"
        )
    data = dict(parse_qs(resp.text))
    if "oauth_token" not in data or "oauth_token_secret" not in data:
        raise TrelloOAuthError(
            f"Réponse OAuthGetAccessToken invalide: {resp.text}"
        )
    return OAuthAccessToken(
        oauth_token=data["oauth_token"][0],
        oauth_token_secret=data["oauth_token_secret"][0],
    )


# ---------------------------------------------------------------------------
# Serveur HTTP local pour la callback
# ---------------------------------------------------------------------------

class _CallbackState:
    """État partagé entre le handler HTTP et le thread principal."""

    def __init__(self) -> None:
        self.oauth_token: Optional[str] = None
        self.oauth_verifier: Optional[str] = None
        self.event = threading.Event()
        self.error: Optional[str] = None


def _make_handler(state: _CallbackState):
    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._respond(200, "OK")
                return
            if parsed.path != "/callback":
                self._respond(404, "Not Found")
                return
            qs = parse_qs(parsed.query)
            state.oauth_token = qs.get("oauth_token", [None])[0]
            state.oauth_verifier = qs.get("oauth_verifier", [None])[0]
            if not state.oauth_verifier:
                err = qs.get("error", ["unknown error"])[0]
                state.error = f"Trello a renvoyé une erreur: {err}"
                self._respond(400, f"Error: {err}")
                state.event.set()
                return
            self._respond(
                200,
                "<h1>OpenClaw — Trello OAuth</h1>"
                "<p>Tu peux fermer cette fenêtre.</p>"
                "<p>Authorization captured successfully.</p>",
            )
            state.event.set()

        def log_message(self, format, *args):  # silence default access log
            sys.stderr.write(
                f"[oauth_flow] {self.address_string()} - {format % args}\n"
            )

        def _respond(self, code: int, body: str) -> None:
            if code == 200 and not body.startswith("<"):
                self.send_response(code)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(body.encode())
            else:
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode())

    return CallbackHandler


def wait_for_callback(
    host: str,
    port: int,
    timeout_seconds: int,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Lance le serveur HTTP local et bloque jusqu'à recevoir la callback ou timeout.

    Retourne (oauth_token, oauth_verifier, error).
    """
    state = _CallbackState()
    handler_cls = _make_handler(state)
    try:
        server = ThreadingHTTPServer((host, port), handler_cls)
    except OSError as e:
        raise TrelloOAuthError(
            f"Impossible de lier le port {port}: {e}. "
            f"Change TRELLO_CALLBACK_PORT ou tue le process qui l'occupe."
        )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[oauth_flow] callback server listening on {host}:{port}")
    try:
        if not state.event.wait(timeout=timeout_seconds):
            raise CallbackTimeoutError(
                f"Timeout ({timeout_seconds}s) en attendant la callback. "
                f"As-tu cliqué Allow dans le navigateur ?"
            )
        return state.oauth_token, state.oauth_verifier, state.error
    finally:
        server.shutdown()
        server.server_close()
        print("[oauth_flow] callback server stopped")


def open_browser(url: str) -> bool:
    """Tente d'ouvrir l'URL dans le navigateur par défaut. Retourne False si échec."""
    try:
        return webbrowser.open(url)
    except Exception:
        return False