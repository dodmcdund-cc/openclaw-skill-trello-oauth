"""Token storage with optional AES-GCM encryption.

Le token OAuth 1.0a final est stocké dans un fichier JSON. Si la variable
d'environnement TRELLO_TOKEN_FILE_KEY est définie (base64, 32 bytes),
le contenu est chiffré avec AES-GCM. Sinon, il est stocké en clair.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


TOKEN_VERSION = 1
# Header magic pour identifier un fichier chiffré vs un fichier en clair
ENCRYPTED_MAGIC = b"TRELO1\0"


@dataclass
class StoredToken:
    """Représentation sérialisée du token OAuth 1.0a final."""

    oauth_token: str
    oauth_token_secret: str
    scope: str
    expiration: str  # "1hour", "1day", "30days", "never"
    expires_at: Optional[str]  # ISO 8601 UTC, ou None si "never"
    member_id: Optional[str] = None
    member_username: Optional[str] = None
    created_at: Optional[str] = None  # ISO 8601 UTC

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "StoredToken":
        # Filtrer les clés inconnues pour forward-compat
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})


def _derive_key(key_b64: str) -> bytes:
    """Décode la clé base64 et vérifie qu'elle fait 32 bytes."""
    raw = base64.b64decode(key_b64)
    if len(raw) != 32:
        raise ValueError(
            f"TRELLO_TOKEN_FILE_KEY doit faire 32 bytes une fois décodée "
            f"(actuellement: {len(raw)} bytes). "
            f"Génère avec: openssl rand -base64 32"
        )
    return raw


def encrypt(plaintext: bytes, key_b64: str) -> bytes:
    """Chiffre plaintext avec AES-GCM, retourne bytes avec magic + nonce + ciphertext."""
    key = _derive_key(key_b64)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, plaintext, associated_data=ENCRYPTED_MAGIC)
    return ENCRYPTED_MAGIC + nonce + ciphertext


def decrypt(blob: bytes, key_b64: str) -> bytes:
    """Déchiffre un blob produit par encrypt()."""
    if not blob.startswith(ENCRYPTED_MAGIC):
        raise ValueError("Le fichier ne commence pas par le magic header (pas chiffré ?)")
    key = _derive_key(key_b64)
    aesgcm = AESGCM(key)
    nonce = blob[len(ENCRYPTED_MAGIC):len(ENCRYPTED_MAGIC) + 12]
    ciphertext = blob[len(ENCRYPTED_MAGIC) + 12:]
    return aesgcm.decrypt(nonce, ciphertext, associated_data=ENCRYPTED_MAGIC)


def save_token(
    token: StoredToken,
    path: str,
    encryption_key: Optional[str] = None,
) -> None:
    """Sauvegarde le token, chiffré si encryption_key fourni."""
    p = Path(os.path.expanduser(path))
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(token.to_dict(), indent=2).encode("utf-8")
    if encryption_key:
        data = encrypt(payload, encryption_key)
        mode = "encrypted"
    else:
        data = payload
        mode = "plaintext"
    # chmod 600 sur le fichier final
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    print(f"[token_store] saved ({mode}) -> {p}")


def load_token(
    path: str,
    encryption_key: Optional[str] = None,
) -> Optional[StoredToken]:
    """Charge le token. Retourne None si le fichier n'existe pas."""
    p = Path(os.path.expanduser(path))
    if not p.exists():
        return None
    raw = p.read_bytes()
    if raw.startswith(ENCRYPTED_MAGIC):
        if not encryption_key:
            raise ValueError(
                f"Le fichier {p} est chiffré mais TRELLO_TOKEN_FILE_KEY "
                f"n'est pas défini dans .env"
            )
        raw = decrypt(raw, encryption_key)
    data = json.loads(raw.decode("utf-8"))
    return StoredToken.from_dict(data)


def delete_token(path: str) -> bool:
    """Supprime le fichier token s'il existe. Retourne True si supprimé."""
    p = Path(os.path.expanduser(path))
    if p.exists():
        p.unlink()
        return True
    return False