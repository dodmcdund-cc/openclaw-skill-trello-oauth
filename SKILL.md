# trello-oauth

Trello via OAuth 1.0a (flow 3-step) + wrapper REST Python. Token à expiration `30days`, scopes `read,write`. Renouvellement manuel via `--setup` tous les 30 jours. Pas de cron.

## Prérequis

- Python 3.10+ avec `requests`, `requests-oauthlib`, `cryptography`
- App Trello enregistrée sur https://trello.com/apps/admin (récupérer **API key** et **OAuth secret**)
- Un hostname joignable depuis ton navigateur (Tailscale MagicDNS, VPN, etc.) pour la callback OAuth
- Port `8080` (par défaut) libre sur la machine qui lance `--setup`

## Installation

```bash
cd ~/.openclaw/workspace/skills/trello-oauth
uv venv .venv
uv pip install -r requirements.txt

cp .env.example .env
chmod 600 .env
# Éditer .env : TRELLO_API_KEY, TRELLO_OAUTH_SECRET, TRELLO_CALLBACK_BASE_URL
```

### Variables `.env`

| Variable | Description | Exemple |
|---|---|---|
| `TRELLO_API_KEY` | API key Trello (publique) | `abcdef1234…` |
| `TRELLO_OAUTH_SECRET` | OAuth secret (= application secret) | `xyz…` |
| `TRELLO_CALLBACK_PORT` | Port du serveur callback | `8080` |
| `TRELLO_CALLBACK_BASE_URL` | URL publique joignable depuis ton navigateur | `http://fred-ghilini-thinkcentre-m920q.tail0d634b.ts.net:8080` |
| `TRELLO_TOKEN_FILE` | Chemin du fichier token | `~/.openclaw/trello_tokens.json` |
| `TRELLO_TOKEN_FILE_KEY` | Clé AES-GCM optionnelle (base64 32 bytes) | `…` |
| `TRELLO_TOKEN_SCOPE` | Scopes OAuth | `read,write` |
| `TRELLO_TOKEN_EXPIRATION` | Durée du token | `30days` |
| `TRELLO_TOKEN_NAME` | Nom affiché à l'utilisateur | `OpenClaw-fred_claw` |

## Authentification

```bash
.venv/bin/python scripts/trello_auth.py --setup
```

Le flow :
1. Lance un serveur HTTP local sur `TRELLO_CALLBACK_PORT`
2. `GET /1/OAuthGetRequestToken` → reçoit request token
3. Construit l'URL authorize : `scope=read,write&expiration=30days`
4. Affiche l'URL : **tu cliques Allow dans ton navigateur**
5. Trello redirige vers `TRELLO_CALLBACK_BASE_URL/callback?oauth_verifier=…`
6. `POST /1/OAuthGetAccessToken` → reçoit le token final
7. Sauvegarde dans `TRELLO_TOKEN_FILE` (chiffré si `TRELLO_TOKEN_FILE_KEY` défini)
8. `GET /1/members/me` pour valider

```bash
# Vérifier le statut du token actuel (inclut un live check)
.venv/bin/python scripts/trello_auth.py --status

# Supprimer le token local (n'invalide pas le token côté Trello)
.venv/bin/python scripts/trello_auth.py --revoke
```

**Note :** si `TRELLO_TOKEN_FILE_KEY` n'est pas défini, le token est stocké en clair sur disque (chmod 600 du home suffit). Pour chiffrer plus tard :

```bash
openssl rand -base64 32   # mettre la sortie dans TRELLO_TOKEN_FILE_KEY
```

## Commandes API

Toutes ces commandes chargent le token sauvegardé et font l'appel REST automatiquement.

```bash
# Profil
.venv/bin/python scripts/trello_api.py whoami

# Boards
.venv/bin/python scripts/trello_api.py boards

# Lists d'un board
.venv/bin/python scripts/trello_api.py lists --board <boardId>

# Cards d'une list
.venv/bin/python scripts/trello_api.py cards --list <listId>

# Détail d'une carte
.venv/bin/python scripts/trello_api.py card --card <cardId>

# Créer une carte
.venv/bin/python scripts/trello_api.py card-create --list <listId> --name "…" [--desc "…"] [--due 2026-12-01]

# Modifier une carte
.venv/bin/python scripts/trello_api.py card-update --card <cardId> [--name …] [--desc …] [--due …] [--closed true]

# Déplacer une carte
.venv/bin/python scripts/trello_api.py card-move --card <cardId> --list <listId>

# Archiver une carte
.venv/bin/python scripts/trello_api.py card-close --card <cardId>

# Créer une list
.venv/bin/python scripts/trello_api.py list-create --board <boardId> --name "…"
```

## Lancement via l'agent (depuis OpenClaw)

L'agent peut initier le flow :

1. Exécute `.venv/bin/python scripts/trello_auth.py --setup`
2. Reçoit l'URL authorize et te l'affiche
3. Tu cliques Allow dans ton navigateur (depuis n'importe quel device du tailnet)
4. Le serveur local capture la callback et finit le flow
5. L'agent confirme dans le chat que le token est valide

Timeout `exec` recommandé : **5 minutes** (`timeout=300`). Le serveur s'arrête automatiquement après la callback (ou expire après timeout).

## Gestion des erreurs

| Erreur | Action |
|---|---|
| `401 unauthorized` | Token expiré → relancer `--setup` |
| `invalid key` / `invalid token` | Mauvaise `TRELLO_API_KEY` ou `TRELLO_OAUTH_SECRET` |
| Port 8080 occupé | Changer `TRELLO_CALLBACK_PORT` ou tuer le process (`lsof -i :8080`) |
| DNS Tailscale injoignable | Vérifier que le tailnet est actif |
| Fichier chiffré mais pas de clé | Définir `TRELLO_TOKEN_FILE_KEY` dans `.env` |
| Timeout pendant le `--setup` | Tu n'as pas cliqué Allow dans les 5 minutes → relancer |

## Structure

```
trello-oauth/
├── SKILL.md
├── README.md
├── .env.example
├── .gitignore
├── requirements.txt
├── scripts/
│   ├── trello_auth.py
│   ├── trello_api.py
│   └── lib/
│       ├── oauth_flow.py
│       └── token_store.py
└── tests/
    └── test_smoke.py
```

## Sécurité

- `.env` est en `chmod 600` (chiffrement par perms OS)
- Token final en clair OU chiffré AES-GCM selon `TRELLO_TOKEN_FILE_KEY`
- `TRELLO_OAUTH_SECRET` ne quitte jamais la machine — utilisé uniquement pour signer les requêtes OAuth 1.0a côté serveur local
- Le serveur callback ne sert qu'à capturer l'`oauth_verifier`, il répond immédiatement et se ferme

## Tests

```bash
.venv/bin/python -m pytest tests/ -v
```

Couvre : `token_store` (chiffré / clair / clé manquante), `oauth_flow` (parsing, construction URL), `trello_auth.load_env`. Aucun appel réseau.

## Ressources

- [Trello REST API — Authorization](https://developer.atlassian.com/cloud/trello/guides/rest-api/authorization/)
- [OAuth 1.0a RFC 5849](https://datatracker.ietf.org/doc/html/rfc5849)