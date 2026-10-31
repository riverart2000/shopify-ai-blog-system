"""Passwords live in the macOS Keychain (service "task-agent"), never in the DB, logs or prompts."""

from __future__ import annotations

from contextlib import suppress

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "task-agent"


def set_secret(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)


def get_secret(name: str) -> str | None:
    return keyring.get_password(SERVICE, name)


def has_secret(name: str) -> bool:
    try:
        return bool(get_secret(name))
    except Exception:  # a locked or unavailable Keychain reads as "not saved"
        return False


def delete_secret(name: str) -> None:
    with suppress(PasswordDeleteError):  # already gone is fine
        keyring.delete_password(SERVICE, name)
