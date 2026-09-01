# trello-oauth

Skill OpenClaw pour interagir avec l'API Trello via OAuth 1.0a.

- Flow OAuth 1.0a 3-step (server callback local)
- Token à expiration `30days`, scopes `read,write`
- Renouvellement manuel via `--setup` (pas de cron)
- Chiffrement AES-GCM optionnel du token
- Wrapper Python : boards, lists, cards (CRUD)

## Quick start

```bash
cd ~/.openclaw/workspace/skills/trello-oauth
uv venv .venv
uv pip install -r requirements.txt

cp .env.example .env
chmod 600 .env
# Remplir TRELLO_API_KEY, TRELLO_OAUTH_SECRET, TRELLO_CALLBACK_BASE_URL

# Lancer le flow OAuth
.venv/bin/python scripts/trello_auth.py --setup

# Tester
.venv/bin/python scripts/trello_api.py whoami
.venv/bin/python scripts/trello_api.py boards
```

## Architecture

```
┌─────────────────┐                  ┌──────────────────┐
│ trello_auth.py   │ ─── OAuth 1.0a ──►│ Trello API       │
│ --setup         │     3-step flow  │ trello.com/1     │
└─────────────────┘                  └──────────────────┘
        │                                    │
        │ 1. GET OAuthGetRequestToken        │
        │ 2. authorize URL → user clicks     │
        │ 3. POST OAuthGetAccessToken        │
        │                                    │
        ▼                                    ▼
┌─────────────────┐                  ┌──────────────────┐
│ token_store.py  │ ◄── save ──────── │ access_token     │
│ (AES-GCM opt.)  │     encrypted    │ +secret+verifier │
└─────────────────┘                  └──────────────────┘
        │
        ▼
~/.openclaw/trello_tokens.json

┌─────────────────┐
│ trello_api.py   │ ─── load token ──► api.trello.com/1/...
│ boards / lists  │     + REST call
│ cards / etc.    │
└─────────────────┘
```

## Auth flow détaillé

1. **Setup** : `trello_auth.py --setup` lance un `ThreadingHTTPServer` local sur le port configuré
2. **Request token** : POST signé OAuth 1.0a vers `https://trello.com/1/OAuthGetRequestToken` avec `oauth_callback=<return_url>`
3. **Authorize** : le script affiche l'URL `https://trello.com/1/OAuthAuthorizeToken?oauth_token=…&scope=read,write&expiration=30days`. L'utilisateur clique Allow dans son navigateur
4. **Callback** : Trello redirige vers `TRELLO_CALLBACK_BASE_URL/callback?oauth_token=…&oauth_verifier=…`. Le serveur HTTP local capture le verifier et répond 200 OK
5. **Access token** : POST signé OAuth 1.0a vers `https://trello.com/1/OAuthGetAccessToken` avec l'`oauth_verifier` reçu
6. **Storage** : `{oauth_token, oauth_token_secret, scope, expires_at, member_id, …}` est sauvegardé (chiffré si `TRELLO_TOKEN_FILE_KEY` est défini)
7. **Validation** : `GET /1/members/me` confirme que le token marche
8. **Cleanup** : le serveur HTTP s'arrête

## Renouvellement (tous les 30 jours)

Le token Trello expire après 30 jours. Quand ça arrive :
- Toutes les commandes `trello_api.py` retournent `401 Unauthorized`
- Relancer `trello_auth.py --setup` (écrase le token existant)

## Variables d'environnement

Voir `.env.example`. Les seules obligatoires :
- `TRELLO_API_KEY`
- `TRELLO_OAUTH_SECRET`
- `TRELLO_CALLBACK_BASE_URL`

Les autres ont des défauts raisonnables.

## Sécurité

- `.env` chmod 600 (chiffrement par perms OS)
- Token final chiffré AES-GCM si `TRELLO_TOKEN_FILE_KEY` défini
- `TRELLO_OAUTH_SECRET` reste local (signature OAuth seulement)

## Différence avec `twg trello`

En attendant que Trello supporte pleinement OAuth 2.1 ainsi que le CLI `twg` (ce qui ne manquera pas d'arriver!), voici un skill intermédiaire fonctionnant avec le pattern OAuth 1.0a de Trello.

## Tests

```bash
.venv/bin/python -m pytest tests/ -v
```

## Ressources

- [Trello REST API — Authorization](https://developer.atlassian.com/cloud/trello/guides/rest-api/authorization/)
- [OAuth 1.0a RFC 5849](https://datatracker.ietf.org/doc/html/rfc5849)
