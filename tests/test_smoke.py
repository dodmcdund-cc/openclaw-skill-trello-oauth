"""Tests smoke pour trello-oauth.

Couvre :
- token_store : roundtrip chiffré et en clair
- oauth_flow : construction URL authorize + parsing
- trello_auth : load_env

Les tests n'appellent PAS l'API Trello réelle (pas de credentials CI).
Pour un test end-to-end : lancer manuellement trello_auth.py --setup.
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts" / "lib"))

from token_store import (  # noqa: E402
    StoredToken, save_token, load_token, delete_token,
    encrypt, decrypt, ENCRYPTED_MAGIC,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def make_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def make_token() -> StoredToken:
    return StoredToken(
        oauth_token="AT-" + "a" * 64,
        oauth_token_secret="AS-" + "b" * 64,
        scope="read,write",
        expiration="30days",
        expires_at="2026-12-31T00:00:00+00:00",
        member_id="abc123",
        member_username="fred_claw",
        created_at="2026-09-01T00:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# token_store
# ---------------------------------------------------------------------------

def test_save_load_plaintext(tmp_path):
    token = make_token()
    path = str(tmp_path / "tok.json")
    save_token(token, path)
    loaded = load_token(path)
    assert loaded is not None
    assert loaded.oauth_token == token.oauth_token
    assert loaded.member_username == "fred_claw"
    assert loaded.scope == "read,write"


def test_save_load_encrypted_roundtrip(tmp_path):
    token = make_token()
    path = str(tmp_path / "tok.json")
    key = make_key()
    save_token(token, path, encryption_key=key)
    loaded = load_token(path, encryption_key=key)
    assert loaded is not None
    assert loaded.oauth_token_secret == token.oauth_token_secret
    assert loaded.expires_at == token.expires_at


def test_encrypted_file_has_magic(tmp_path):
    token = make_token()
    path = str(tmp_path / "tok.json")
    key = make_key()
    save_token(token, path, encryption_key=key)
    raw = Path(path).read_bytes()
    assert raw.startswith(ENCRYPTED_MAGIC)


def test_load_encrypted_without_key_raises(tmp_path):
    token = make_token()
    path = str(tmp_path / "tok.json")
    key = make_key()
    save_token(token, path, encryption_key=key)
    with pytest.raises(Exception):
        load_token(path)  # pas de clé


def test_load_missing_returns_none(tmp_path):
    assert load_token(str(tmp_path / "nope.json")) is None


def test_delete_token(tmp_path):
    path = str(tmp_path / "tok.json")
    save_token(make_token(), path)
    assert Path(path).exists()
    assert delete_token(path) is True
    assert not Path(path).exists()
    assert delete_token(path) is False  # idempotent


def test_encrypt_decrypt_roundtrip():
    key = make_key()
    plaintext = b"hello world"
    blob = encrypt(plaintext, key)
    assert decrypt(blob, key) == plaintext


def test_wrong_key_size_rejected():
    with pytest.raises(ValueError):
        encrypt(b"x", base64.b64encode(b"short").decode())


# ---------------------------------------------------------------------------
# oauth_flow — parsing + URL construction
# ---------------------------------------------------------------------------

def test_parse_oauth_response():
    body = "oauth_token=ATxxx&oauth_token_secret=ASyyy&oauth_callback_confirmed=true"
    parsed = dict(parse_qs(body))
    assert parsed["oauth_token"] == ["ATxxx"]
    assert parsed["oauth_token_secret"] == ["ASyyy"]
    assert parsed["oauth_callback_confirmed"] == ["true"]


def test_build_authorize_url():
    from oauth_flow import build_authorize_url, OAuthRequestToken
    req = OAuthRequestToken(
        oauth_token="REQ123",
        oauth_token_secret="REQ456",
        callback_confirmed="true",
    )
    url = build_authorize_url(req, scope="read,write", expiration="30days", name="TestApp")
    assert "OAuthAuthorizeToken" in url
    assert "oauth_token=REQ123" in url
    assert "expiration=30days" in url
    assert "name=TestApp" in url


# ---------------------------------------------------------------------------
# trello_auth.load_env
# ---------------------------------------------------------------------------

def test_load_env(tmp_path):
    (tmp_path / ".env").write_text(
        "TRELLO_API_KEY=abc123\n"
        "# comment\n"
        "TRELLO_OAUTH_SECRET='quoted value'\n"
        "TRELLO_TOKEN_FILE_KEY=\"doublequoted\"\n"
        "\n"
        "EMPTY_VALUE=\n"
    )
    scripts_path = str(SKILL_DIR / "scripts")
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    sys.modules.pop("trello_auth", None)
    from trello_auth import load_env  # type: ignore  # noqa: E402
    env = load_env(tmp_path / ".env")
    assert env["TRELLO_API_KEY"] == "abc123"
    assert env["TRELLO_OAUTH_SECRET"] == "quoted value"
    assert env["TRELLO_TOKEN_FILE_KEY"] == "doublequoted"
    assert env.get("EMPTY_VALUE") == ""