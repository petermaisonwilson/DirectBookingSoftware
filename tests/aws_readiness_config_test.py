from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import inspect

from online.config import load_runtime_config
from online.db_engine import create_database_engine
from online.db_schema import metadata


@contextmanager
def environment(**values):
    old = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "portable.db"
        with environment(
            DIRECTBOOKING_ENV="test",
            DIRECTBOOKING_DATABASE_URL=f"sqlite:///{db_path.as_posix()}",
            DIRECTBOOKING_SEED_DEMO=None,
        ):
            config = load_runtime_config()
            assert config.environment == "test"
            assert config.seed_demo is False
            assert config.secure_cookies is False
            engine = create_database_engine(config)
            metadata.create_all(engine)
            tables = set(inspect(engine).get_table_names())
            assert {"companies", "users", "sessions", "audit_log"}.issubset(tables)
            engine.dispose()

    with environment(
        DIRECTBOOKING_ENV="production",
        DIRECTBOOKING_DATABASE_URL="postgresql+psycopg://dbs:secret@example.invalid/dbs",
        DIRECTBOOKING_SEED_DEMO="1",
    ):
        try:
            load_runtime_config()
        except RuntimeError as exc:
            assert "Demo data is forbidden in production" in str(exc)
        else:
            raise AssertionError("Production must reject demo data")

    with environment(
        DIRECTBOOKING_ENV="production",
        DIRECTBOOKING_DATABASE_URL="postgresql+psycopg://dbs:secret@example.invalid/dbs",
        DIRECTBOOKING_SEED_DEMO="0",
    ):
        config = load_runtime_config()
        assert config.production is True
        assert config.seed_demo is False
        assert config.secure_cookies is True

    print("AWS readiness configuration regression: passed")


if __name__ == "__main__":
    main()
