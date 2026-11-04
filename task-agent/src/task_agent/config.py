"""Settings, read from task-agent/.env (see .env.example). Secrets live only there."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from task_agent.local_model import local_model

# Folder that holds pyproject.toml, .env, data/, logs/ and profiles/.
ROOT = Path(__file__).resolve().parents[2]

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    # Local model (oMLX, OpenAI-compatible). One model only, no cloud fallback. The server, the model and the
    # answer cap come from the shared file ~/.config/local-ai/model.json (also read by OpenClaw and Social
    # Autopilot), whatever .env says: two different models can't fit in memory together.
    llm_base_url: str = Field(default_factory=lambda: local_model().server_url)
    llm_model: str = Field(default_factory=lambda: local_model().text_model)
    llm_api_key: SecretStr = SecretStr("")  # oMLX doesn't need one for /v1 on this machine
    llm_temperature: float = Field(0.1, ge=0, le=0.2)  # low temperature for reliable tool use
    llm_timeout_seconds: int = 600
    llm_max_tokens: int = Field(  # longest answer per model call (thinking included)
        default_factory=lambda: local_model().max_output_tokens, ge=1024, le=32768
    )
    llm_thinking: bool | None = True  # sent with every request; None = the model's own default

    # Storage (relative paths are inside task-agent/)
    db_path: Path = Path("data/task_agent.db")
    log_dir: Path = Path("logs")
    profiles_dir: Path = Path("profiles")

    # Worker
    poll_seconds: int = 10
    max_steps: int = 60  # model requests per job
    max_minutes: int = 20  # wall-clock budget per job
    max_attempts: int = 3  # retries on network/model errors, with backoff
    output_retries: int = 3  # re-asks when the model's result fails validation

    # Tools
    fetch_max_chars: int = 12_000  # page text handed to the model per fetch
    brave_api_key: SecretStr = SecretStr("")  # env BRAVE_API_KEY: Brave Search for web_search (else DuckDuckGo)

    # Browser for agents: "chrome" = Joe's everyday Chrome (his logins) through the OpenClaw extension;
    # "own" = a separate Chrome profile the agents keep in profiles/shared.
    browser: str = "chrome"
    openclaw_dir: Path = Path.home() / "openclaw"
    openclaw_browser_profile: str = "chrome"  # the OpenClaw browser profile that is the extension relay

    # Private API for the Shopify admin app (this Mac only)
    api_host: str = "127.0.0.1"
    api_port: int = 4100
    task_agent_api_key: SecretStr = SecretStr("")  # env TASK_AGENT_API_KEY; the app's .env has the same value
    login_window_minutes: int = 30  # a login window closes itself after this long

    # Social Engine (the live Python server) — read data, approve posts, pause campaigns
    social_api_url: str = "http://127.0.0.1:4000/api/social-engine"
    social_api_key: SecretStr = SecretStr("")  # env SOCIAL_API_KEY (the server's AI_BLOG_BACKEND_API_KEY)
    social_admin_url: str = ""  # optional link to the admin app, shown under Telegram cards

    # Telegram (token: Keychain "telegram:bot_token", set on the Settings page; env fallback)
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_owner_username: str = ""  # only this account may use the bot; locked to its numeric id on first message
    telegram_summary_hour: int = 8  # morning summary, store time
    store_timezone: str = "Europe/London"
    telegram_notify_seconds: int = 60

    @model_validator(mode="after")
    def _shared_model(self) -> Settings:
        """.env can't choose another model or server: the shared file does."""
        shared = local_model()
        self.llm_base_url, self.llm_model = shared.server_url, shared.text_model
        return self

    def path(self, value: Path) -> Path:
        return value if value.is_absolute() else ROOT / value


@lru_cache
def get_settings() -> Settings:
    return Settings()
