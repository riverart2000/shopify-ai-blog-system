"""Passwords the worker creates for directory accounts, encrypted at rest.

The key lives in SOCIAL_ENGINE_VAULT_KEY or, failing that, in a 0600 file under
the media root. The owner can reveal a password from the Listings page; the
step log and activity never contain one.
"""
from __future__ import annotations

import os
import secrets
import string
from pathlib import Path

from ..errors import ConfigurationError

_ALPHABET = string.ascii_letters + string.digits + "!#%+-=?@"


def media_root() -> Path:
    root = os.environ.get("SOCIAL_ENGINE_MEDIA_ROOT") or os.path.expanduser("~/.social-engine-media")
    return Path(root)


def _key() -> bytes:
    value = os.environ.get("SOCIAL_ENGINE_VAULT_KEY", "").strip()
    if value:
        return value.encode("utf-8")
    path = media_root() / "listings" / "vault.key"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip().encode("utf-8")
    from cryptography.fernet import Fernet

    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:   # pragma: no cover
        raise ConfigurationError("The 'cryptography' package is needed to store account passwords; pip install cryptography.") from exc
    return Fernet(_key())


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode("utf-8")).decode("ascii") if text else ""


def decrypt(token: str) -> str:
    if not token:
        return ""
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise ConfigurationError("A stored password could not be decrypted (the vault key changed).") from exc


def generate_password(length: int = 20) -> str:
    """Letters, digits and a few symbols every sign-up form accepts; always has one of each."""
    while True:
        pw = "".join(secrets.choice(_ALPHABET) for _ in range(length))
        if any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw) and any(c in "!#%+-=?@" for c in pw):
            return pw
