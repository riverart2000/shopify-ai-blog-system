from __future__ import annotations

import pytest

from task_agent.config import Settings
from task_agent.db import JobStore


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        db_path=tmp_path / "jobs.db",
        log_dir=tmp_path / "logs",
        profiles_dir=tmp_path / "profiles",
        max_steps=10,
        max_minutes=1,
        max_attempts=3,
    )


@pytest.fixture
def store(settings) -> JobStore:
    return JobStore(settings.path(settings.db_path))


class MemoryKeyring:
    """Stand-in for the macOS Keychain so tests never touch the real one."""

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}


@pytest.fixture(autouse=True)
def memory_keyring(monkeypatch) -> MemoryKeyring:
    import keyring
    from keyring.errors import PasswordDeleteError

    kr = MemoryKeyring()

    def delete(service: str, name: str) -> None:
        if (service, name) not in kr.data:
            raise PasswordDeleteError(name)
        del kr.data[(service, name)]

    monkeypatch.setattr(keyring, "set_password", lambda s, n, v: kr.data.__setitem__((s, n), v))
    monkeypatch.setattr(keyring, "get_password", lambda s, n: kr.data.get((s, n)))
    monkeypatch.setattr(keyring, "delete_password", delete)
    return kr


@pytest.fixture(autouse=True)
def no_brave(monkeypatch) -> None:
    """Tests never call the Brave Search API (a real key may be in task-agent/.env)."""
    from task_agent.tools import web

    monkeypatch.setattr(web, "brave_key", lambda: "")
