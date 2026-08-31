from __future__ import annotations

from sqlalchemy import Engine, create_engine

from .config import RuntimeConfig, load_runtime_config


def create_database_engine(config: RuntimeConfig | None = None) -> Engine:
    settings = config or load_runtime_config()
    options: dict[str, object] = {"future": True, "pool_pre_ping": True}
    if settings.database_url.startswith("sqlite:"):
        options["connect_args"] = {"check_same_thread": False}
    return create_engine(settings.database_url, **options)
