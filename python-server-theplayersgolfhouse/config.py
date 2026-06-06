"""
config.py — loads and validates config.json, resolves env vars for secrets.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ServerConfig:
    host: str
    port: int
    mode: str  # "debug" | "production"
    secret_key: str

    @property
    def is_debug(self) -> bool:
        return self.mode == "debug"


@dataclass
class LoggingConfig:
    file_path: str
    max_bytes: int
    backup_count: int


@dataclass
class DeepSeekConfig:
    api_key: str
    endpoint: str
    model: str
    temperature: float
    timeout_seconds: int
    max_retries: int
    system_prompt: str


@dataclass
class GrokConfig:
    api_key: str
    endpoint: str
    model: str
    image_count: int
    timeout_seconds: int


@dataclass
class StoreConfig:
    id: str
    name: str
    myshopify_domain: str
    client_id: str
    client_secret: str
    default_blog_handle: str
    default_author: str
    custom_domain: str = ""

    @classmethod
    def from_row(cls, row: dict) -> "StoreConfig":
        return cls(
            id=row["id"],
            name=row["name"],
            myshopify_domain=row["myshopify_domain"],
            custom_domain=row.get("custom_domain", ""),
            client_id=row.get("client_id", ""),
            client_secret=row.get("client_secret", ""),
            default_blog_handle=row.get("default_blog_handle", "news"),
            default_author=row.get("default_author", "Store Team"),
        )


@dataclass
class PromptConfig:
    id: str
    name: str
    text: str


@dataclass
class AppConfig:
    server: ServerConfig
    logging: LoggingConfig
    deepseek: DeepSeekConfig
    grok: GrokConfig
    stores: list[StoreConfig]
    prompts: list[PromptConfig]
    default_prompt_id: str = ""

    def get_store(self, store_id: str) -> Optional[StoreConfig]:
        return next((s for s in self.stores if s.id == store_id), None)

    def get_prompt(self, prompt_id: str) -> Optional[PromptConfig]:
        return next((p for p in self.prompts if p.id == prompt_id), None)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _require_env(env_var: str, field_name: str) -> str:
    value = os.environ.get(env_var, "").strip()
    if not value:
        raise EnvironmentError(
            f"Config field '{field_name}' references env var '{env_var}' which is not set."
        )
    return value


def load_config(path: str = "config.json") -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path.resolve()}")

    with config_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    server_raw = raw["server"]
    server = ServerConfig(
        host=server_raw["host"],
        port=int(server_raw["port"]),
        mode=server_raw.get("mode", "production"),
        secret_key=server_raw["secret_key"],
    )

    log_raw = raw["logging"]
    logging_cfg = LoggingConfig(
        file_path=log_raw["file_path"],
        max_bytes=int(log_raw["max_bytes"]),
        backup_count=int(log_raw["backup_count"]),
    )

    ds_raw = raw["deepseek"]
    deepseek = DeepSeekConfig(
        api_key=_require_env(ds_raw["api_key_env"], "deepseek.api_key_env"),
        endpoint=ds_raw["endpoint"],
        model=ds_raw["model"],
        temperature=float(ds_raw["temperature"]),
        timeout_seconds=int(ds_raw["timeout_seconds"]),
        max_retries=int(ds_raw["max_retries"]),
        system_prompt=ds_raw["system_prompt"],
    )

    grok_raw = raw["grok"]
    grok = GrokConfig(
        api_key=_require_env(grok_raw["api_key_env"], "grok.api_key_env"),
        endpoint=grok_raw["endpoint"],
        model=grok_raw["model"],
        image_count=int(grok_raw["image_count"]),
        timeout_seconds=int(grok_raw["timeout_seconds"]),
    )

    stores = []
    for s in raw["stores"]:
        stores.append(StoreConfig(
            id=s["id"],
            name=s["name"],
            myshopify_domain=s["myshopify_domain"],
            client_id=_require_env(s["client_id_env"], f"stores[{s['id']}].client_id_env"),
            client_secret=_require_env(s["client_secret_env"], f"stores[{s['id']}].client_secret_env"),
            default_blog_handle=s.get("default_blog_handle", "news"),
            default_author=s.get("default_author", "Store Team"),
        ))

    if not stores:
        raise ValueError("Config must define at least one store.")

    prompts = []
    for p in raw["prompts"]:
        prompts.append(PromptConfig(
            id=p["id"],
            name=p["name"],
            text=p.get("text", ""),
        ))

    return AppConfig(
        server=server,
        logging=logging_cfg,
        deepseek=deepseek,
        grok=grok,
        stores=stores,
        prompts=prompts,
    )
