"""trello_api.py — wrapper REST haut niveau pour Trello.

Charge le token OAuth 1.0a depuis le fichier (chiffré ou non selon .env),
puis expose des sous-commandes pour les opérations courantes :
  whoami / boards / lists / cards / card / card-create / card-update /
  card-move / card-close / list-create

Toutes les requêtes utilisent key + oauth_token en query params
(le mode le plus simple et supporté par Trello).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from token_store import load_token  # noqa: E402

SKILL_DIR = SCRIPT_DIR.parent
ENV_PATH = SKILL_DIR / ".env"
API_BASE = "https://api.trello.com/1"


def load_env(path: Path) -> dict[str, str]:
    """Parse un fichier .env simple (KEY=VALUE)."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        env[key] = value
    return env


def get_auth_params(env: dict[str, str]) -> tuple[dict[str, str], Any]:
    """Retourne (query_params, stored_token) pour authentifier les requêtes Trello."""
    api_key = env.get("TRELLO_API_KEY", "")
    if not api_key or api_key.startswith("__"):
        raise SystemExit("TRELLO_API_KEY manquant dans .env")

    token_file = env.get("TRELLO_TOKEN_FILE") or "~/.openclaw/trello_tokens.json"
    encryption_key = env.get("TRELLO_TOKEN_FILE_KEY") or None

    stored = load_token(token_file, encryption_key=encryption_key)
    if not stored:
        raise SystemExit(
            f"Aucun token dans {token_file}. "
            f"Lance: .venv/bin/python scripts/trello_auth.py --setup"
        )
    return {"key": api_key, "token": stored.oauth_token}, stored


def api(
    method: str,
    path: str,
    env: dict[str, str],
    *,
    params_extra: dict | None = None,
) -> Any:
    """Appel REST générique. Retourne le JSON décodé."""
    params, _ = get_auth_params(env)
    if params_extra:
        params.update(params_extra)
    url = f"{API_BASE}{path}"
    resp = requests.request(method, url, params=params, timeout=30)
    if resp.status_code == 401:
        raise SystemExit(
            "401 Unauthorized — token expiré ou révoqué. "
            "Relance: .venv/bin/python scripts/trello_auth.py --setup"
        )
    if resp.status_code >= 400:
        raise SystemExit(f"HTTP {resp.status_code}: {resp.text}")
    if not resp.text:
        return None
    try:
        return resp.json()
    except ValueError:
        return resp.text


def print_json(obj: Any) -> None:
    if isinstance(obj, (dict, list)):
        print(json.dumps(obj, indent=2, ensure_ascii=False))
    else:
        print(obj)


def cmd_whoami(env, _args):
    data = api("GET", "/members/me", env,
               params_extra={"fields": "id,username,fullName,email,bio"})
    print_json(data)
    return 0


def cmd_boards(env, _args):
    data = api("GET", "/members/me/boards", env,
               params_extra={"fields": "name,url,dateLastActivity,closed,desc"})
    if isinstance(data, list):
        for b in data:
            closed = " (FERMÉ)" if b.get("closed") else ""
            print(f"- {b.get('name')}{closed}  id={b.get('id')}")
    else:
        print_json(data)
    return 0


def cmd_lists(env, args):
    if not args.board:
        raise SystemExit("--board <boardId> requis")
    data = api("GET", f"/boards/{args.board}/lists", env,
               params_extra={"fields": "name,id,closed"})
    if isinstance(data, list):
        for l in data:
            print(f"- {l.get('name')}  id={l.get('id')}")
    else:
        print_json(data)
    return 0


def cmd_cards(env, args):
    if not args.list:
        raise SystemExit("--list <listId> requis")
    data = api("GET", f"/lists/{args.list}/cards", env,
               params_extra={"fields": "name,id,desc,due,labels,closed,url"})
    if isinstance(data, list):
        for c in data:
            due = f"  due={c.get('due')}" if c.get("due") else ""
            print(f"- {c.get('name')}  id={c.get('id')}{due}")
    else:
        print_json(data)
    return 0


def cmd_card(env, args):
    if not args.card:
        raise SystemExit("--card <cardId> requis")
    data = api("GET", f"/cards/{args.card}", env,
               params_extra={"fields": "name,desc,due,labels,idList,idBoard,url,closed"})
    print_json(data)
    return 0


def cmd_card_create(env, args):
    if not args.list or not args.name:
        raise SystemExit("--list <listId> et --name requis")
    body = {"idList": args.list, "name": args.name}
    if args.desc is not None:
        body["desc"] = args.desc
    if args.due is not None:
        body["due"] = args.due
    if args.pos is not None:
        body["pos"] = args.pos
    data = api("POST", "/cards", env, params_extra=body)
    print_json(data)
    if isinstance(data, dict) and data.get("shortUrl"):
        print(f"\n>>> Carte créée: {data['shortUrl']}", file=sys.stderr)
    return 0


def cmd_card_update(env, args):
    if not args.card:
        raise SystemExit("--card <cardId> requis")
    body: dict = {}
    if args.name is not None:
        body["name"] = args.name
    if args.desc is not None:
        body["desc"] = args.desc
    if args.due is not None:
        body["due"] = args.due
    if args.closed is not None:
        body["closed"] = args.closed
    if not body:
        raise SystemExit("Aucun champ à modifier (--name / --desc / --due / --closed)")
    data = api("PUT", f"/cards/{args.card}", env, params_extra=body)
    print_json(data)
    return 0


def cmd_card_move(env, args):
    if not args.card or not args.list:
        raise SystemExit("--card <cardId> et --list <listId> requis")
    body = {"idList": args.list}
    if args.pos is not None:
        body["pos"] = args.pos
    data = api("PUT", f"/cards/{args.card}", env, params_extra=body)
    print_json(data)
    return 0


def cmd_card_close(env, args):
    if not args.card:
        raise SystemExit("--card <cardId> requis")
    data = api("PUT", f"/cards/{args.card}", env, params_extra={"closed": "true"})
    print_json(data)
    return 0


def cmd_list_create(env, args):
    if not args.board or not args.name:
        raise SystemExit("--board <boardId> et --name requis")
    body = {"idBoard": args.board, "name": args.name}
    if args.pos is not None:
        body["pos"] = args.pos
    data = api("POST", "/lists", env, params_extra=body)
    print_json(data)
    return 0


COMMANDS = {
    "whoami": cmd_whoami,
    "boards": cmd_boards,
    "lists": cmd_lists,
    "cards": cmd_cards,
    "card": cmd_card,
    "card-create": cmd_card_create,
    "card-update": cmd_card_update,
    "card-move": cmd_card_move,
    "card-close": cmd_card_close,
    "list-create": cmd_list_create,
}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="trello_api.py", description="Trello REST wrapper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami", help="Profil de l'utilisateur connecté")
    sub.add_parser("boards", help="Liste des boards du membre")

    p_lists = sub.add_parser("lists", help="Lists d'un board")
    p_lists.add_argument("--board", required=True)

    p_cards = sub.add_parser("cards", help="Cards d'une list")
    p_cards.add_argument("--list", required=True)

    p_card = sub.add_parser("card", help="Détail d'une carte")
    p_card.add_argument("--card", required=True)

    p_ccreate = sub.add_parser("card-create", help="Créer une carte")
    p_ccreate.add_argument("--list", required=True)
    p_ccreate.add_argument("--name", required=True)
    p_ccreate.add_argument("--desc", default=None)
    p_ccreate.add_argument("--due", default=None, help="ISO 8601 (e.g. 2026-12-01)")
    p_ccreate.add_argument("--pos", default=None)

    p_cupdate = sub.add_parser("card-update", help="Modifier une carte")
    p_cupdate.add_argument("--card", required=True)
    p_cupdate.add_argument("--name", default=None)
    p_cupdate.add_argument("--desc", default=None)
    p_cupdate.add_argument("--due", default=None)
    p_cupdate.add_argument("--closed", type=lambda v: v.lower() in ("true", "1", "yes"), default=None)

    p_cmove = sub.add_parser("card-move", help="Déplacer une carte")
    p_cmove.add_argument("--card", required=True)
    p_cmove.add_argument("--list", required=True)
    p_cmove.add_argument("--pos", default=None)

    p_cclose = sub.add_parser("card-close", help="Archiver une carte")
    p_cclose.add_argument("--card", required=True)

    p_lcreate = sub.add_parser("list-create", help="Créer une list")
    p_lcreate.add_argument("--board", required=True)
    p_lcreate.add_argument("--name", required=True)
    p_lcreate.add_argument("--pos", default=None)

    args = parser.parse_args(argv)
    env = load_env(ENV_PATH)
    handler = COMMANDS.get(args.cmd)
    if not handler:
        parser.print_help()
        return 1
    return handler(env, args)


if __name__ == "__main__":
    sys.exit(main())