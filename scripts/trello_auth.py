"""trello_auth.py — CLI principal du skill trello-oauth.

Sous-commandes :
  --setup    Lance le flow OAuth 1.0a 3-step (serveur callback + navigateur)
  --status   Affiche le statut du token sauvegardé
  --revoke   Supprime le fichier token local

Charge la config depuis .env (à côté du script parent du skill).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Permettre l'import depuis scripts/lib/ quel que soit le cwd
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from oauth_flow import (  # noqa: E402
    build_authorize_url,
    get_access_token,
    get_request_token,
    wait_for_callback,
    CallbackTimeoutError,
    TrelloOAuthError,
)
from token_store import StoredToken, load_token, save_token, delete_token  # noqa: E402

# Charger .env minimaliste (pas de dépendance python-dotenv)
SKILL_DIR = SCRIPT_DIR.parent
ENV_PATH = SKILL_DIR / ".env"


def load_env(path: Path) -> dict[str, str]:
    """Parse un fichier .env simple (KEY=VALUE, pas de quoting complexe)."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip optional quotes
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        env[key] = value
    return env


def require_env(env: dict[str, str], *keys: str) -> None:
    missing = [k for k in keys if not env.get(k) or env[k].startswith("__")]
    if missing:
        raise SystemExit(
            f"Variables manquantes dans {ENV_PATH}:\n  - " + "\n  - ".join(missing)
            + f"\n\nÉdite le fichier .env (voir .env.example) et relance."
        )


# ---------------------------------------------------------------------------
# --setup
# ---------------------------------------------------------------------------

def cmd_setup(env: dict[str, str]) -> int:
    import requests as _requests  # local import to keep top clean

    api_key = env["TRELLO_API_KEY"]
    oauth_secret = env["TRELLO_OAUTH_SECRET"]
    port = int(env.get("TRELLO_CALLBACK_PORT", "8080"))
    base_url = env["TRELLO_CALLBACK_BASE_URL"].rstrip("/")
    callback_url = f"{base_url}/callback"
    token_file = env.get("TRELLO_TOKEN_FILE") or "~/.openclaw/trello_tokens.json"
    encryption_key = env.get("TRELLO_TOKEN_FILE_KEY") or None
    scope = env.get("TRELLO_TOKEN_SCOPE", "read,write")
    expiration = env.get("TRELLO_TOKEN_EXPIRATION", "30days")
    token_name = env.get("TRELLO_TOKEN_NAME", "OpenClaw")

    # 1. Request token
    print(f"[setup] OAuth callback URL: {callback_url}")
    print(f"[setup] Requesting token from Trello…")
    try:
        req_token = get_request_token(api_key, oauth_secret, callback_url)
    except TrelloOAuthError as e:
        print(f"[setup] ERREUR: {e}", file=sys.stderr)
        return 1
    print(f"[setup] request token obtained (callback_confirmed={req_token.callback_confirmed})")

    # 2. Build authorize URL
    authorize_url = build_authorize_url(
        req_token, scope=scope, expiration=expiration, name=token_name
    )
    print()
    print("=" * 70)
    print("OUVRE CETTE URL DANS TON NAVIGATEUR ET CLIQUE ALLOW :")
    print()
    print(f"  {authorize_url}")
    print()
    print("=" * 70)
    print(f"(en attente de la callback sur {base_url}/callback …)")

    # 3. Wait for callback
    try:
        _tok, verifier, error = wait_for_callback(host="0.0.0.0", port=port, timeout_seconds=300)
    except CallbackTimeoutError as e:
        print(f"[setup] TIMEOUT: {e}", file=sys.stderr)
        return 2
    except TrelloOAuthError as e:
        print(f"[setup] ERREUR: {e}", file=sys.stderr)
        return 1

    if error or not verifier:
        print(f"[setup] ERREUR côté Trello: {error}", file=sys.stderr)
        return 3

    # 4. Exchange verifier for access token
    print(f"[setup] verifier received, exchanging for access token…")
    try:
        access_token = get_access_token(api_key, oauth_secret, req_token, verifier)
    except TrelloOAuthError as e:
        print(f"[setup] ERREUR: {e}", file=sys.stderr)
        return 1

    # 5. Compute expiration
    now = datetime.now(timezone.utc)
    if expiration == "never":
        expires_at = None
    elif expiration == "1hour":
        expires_at = (now + timedelta(hours=1)).isoformat()
    elif expiration == "1day":
        expires_at = (now + timedelta(days=1)).isoformat()
    elif expiration == "30days":
        expires_at = (now + timedelta(days=30)).isoformat()
    else:
        expires_at = None

    # 6. Validate with /members/me (using key + token as query params)
    print(f"[setup] validating with GET /1/members/me…")
    member = _validate_member(api_key, access_token.oauth_token)
    if not member:
        print(f"[setup] WARN: validation /members/me a échoué, token non sauvegardé",
              file=sys.stderr)
        return 4

    stored = StoredToken(
        oauth_token=access_token.oauth_token,
        oauth_token_secret=access_token.oauth_token_secret,
        scope=scope,
        expiration=expiration,
        expires_at=expires_at,
        member_id=member.get("id"),
        member_username=member.get("username"),
        created_at=now.isoformat(),
    )

    # 7. Persist
    save_token(stored, token_file, encryption_key=encryption_key)

    print()
    print("=" * 70)
    print(f"[setup] OK — token sauvegardé ({'chiffré' if encryption_key else 'en clair'})")
    print(f"[setup] Profil     : {member.get('fullName')} (@{member.get('username')})")
    if expires_at:
        print(f"[setup] Expire le  : {expires_at} (≈ {expiration})")
    else:
        print(f"[setup] Expiration : {expiration}")
    print(f"[setup] Scopes     : {scope}")
    print(f"[setup] Fichier    : {token_file}")
    print("=" * 70)
    return 0


def _validate_member(api_key: str, oauth_token: str) -> dict | None:
    """Vérifie que le token marche en appelant /1/members/me."""
    import requests
    url = "https://api.trello.com/1/members/me"
    params = {"key": api_key, "token": oauth_token, "fields": "id,username,fullName,email"}
    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code != 200:
            print(f"[setup] /members/me HTTP {r.status_code}: {r.text}", file=sys.stderr)
            return None
        return r.json()
    except Exception as e:
        print(f"[setup] /members/me exception: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# --status
# ---------------------------------------------------------------------------

def cmd_status(env: dict[str, str]) -> int:
    token_file = env.get("TRELLO_TOKEN_FILE") or "~/.openclaw/trello_tokens.json"
    encryption_key = env.get("TRELLO_TOKEN_FILE_KEY") or None
    api_key = env.get("TRELLO_API_KEY", "")

    stored = load_token(token_file, encryption_key=encryption_key)
    if not stored:
        print(f"[status] Aucun token trouvé dans {token_file}")
        print(f"[status] Lance: .venv/bin/python scripts/trello_auth.py --setup")
        return 1

    print(f"[status] Fichier      : {token_file}")
    print(f"[status] Chiffrement  : {'AES-GCM' if encryption_key else 'AUCUN (clair)'}")
    print(f"[status] Member       : {stored.member_username or '?'} ({stored.member_id or '?'})")
    print(f"[status] Scopes       : {stored.scope}")
    print(f"[status] Created      : {stored.created_at or '?'}")
    print(f"[status] Expires      : {stored.expires_at or stored.expiration}")

    # Validation live
    if api_key and not api_key.startswith("__"):
        member = _validate_member(api_key, stored.oauth_token)
        if member:
            print(f"[status] Live check  : OK ({member.get('username')})")
        else:
            print(f"[status] Live check  : ÉCHEC — token expiré ou révoqué")
            return 2
    return 0


# ---------------------------------------------------------------------------
# --revoke
# ---------------------------------------------------------------------------

def cmd_revoke(env: dict[str, str]) -> int:
    token_file = env.get("TRELLO_TOKEN_FILE") or "~/.openclaw/trello_tokens.json"
    if delete_token(token_file):
        print(f"[revoke] Token supprimé: {token_file}")
    else:
        print(f"[revoke] Aucun fichier token à supprimer: {token_file}")
    print("[revoke] Note: le token reste actif côté Trello jusqu'à expiration.")
    print("[revoke] Pour le révoquer avant: https://trello.com/u/me/account -> Applications -> Revoke")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="trello_auth.py",
        description="Trello OAuth 1.0a setup / status / revoke",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--setup", action="store_true", help="Lance le flow OAuth 1.0a")
    group.add_argument("--status", action="store_true", help="Affiche le statut du token")
    group.add_argument("--revoke", action="store_true", help="Supprime le token local")
    args = parser.parse_args(argv)

    env = load_env(ENV_PATH)

    if args.setup:
        require_env(env, "TRELLO_API_KEY", "TRELLO_OAUTH_SECRET", "TRELLO_CALLBACK_BASE_URL")
        return cmd_setup(env)
    if args.status:
        return cmd_status(env)
    if args.revoke:
        return cmd_revoke(env)
    return 0


if __name__ == "__main__":
    sys.exit(main())