from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


VALID_ENVIRONMENTS = {"development", "test", "production"}


@dataclass(frozen=True)
class RuntimeConfig:
    environment: str
    database_url: str
    seed_demo: bool
    secure_cookies: bool
    host: str
    port: int

    @property
    def production(self) -> bool:
        return self.environment == "production"


def _sqlite_url_from_path(path: str | Path) -> str:
    value = Path(path).expanduser()
    return f"sqlite:///{value.as_posix()}"


def load_runtime_config() -> RuntimeConfig:
    environment = os.environ.get("DIRECTBOOKING_ENV", "development").strip().lower()
    if environment not in VALID_ENVIRONMENTS:
        raise RuntimeError(
            "DIRECTBOOKING_ENV must be development, test, or production"
        )

    database_url = os.environ.get("DIRECTBOOKING_DATABASE_URL", "").strip()
    if not database_url:
        legacy_path = os.environ.get(
            "DIRECTBOOKING_DB", "online_data/direct_booking_online_dev.db"
        )
        database_url = _sqlite_url_from_path(legacy_path)

    explicit_seed = os.environ.get("DIRECTBOOKING_SEED_DEMO")
    if explicit_seed is None:
        seed_demo = environment == "development"
    else:
        seed_demo = explicit_seed.strip() == "1"

    if environment == "production" and seed_demo:
        raise RuntimeError("Demo data is forbidden in production")

    secure_cookies = environment == "production" or os.environ.get(
        "DIRECTBOOKING_SECURE_COOKIES", "0"
    ).strip() == "1"

    host = os.environ.get("DIRECTBOOKING_HOST", "127.0.0.1").strip()
    port = int(os.environ.get("DIRECTBOOKING_PORT", "8000"))

    return RuntimeConfig(
        environment=environment,
        database_url=database_url,
        seed_demo=seed_demo,
        secure_cookies=secure_cookies,
        host=host,
        port=port,
    )
