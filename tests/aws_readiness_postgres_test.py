from __future__ import annotations

import os

from sqlalchemy import delete, insert, select

from online.config import load_runtime_config
from online.db_engine import create_database_engine
from online.db_schema import audit_log, companies, metadata, sessions, users
from online.security import hash_password


def main() -> None:
    if not os.environ.get("DIRECTBOOKING_DATABASE_URL"):
        raise RuntimeError("DIRECTBOOKING_DATABASE_URL is required")

    config = load_runtime_config()
    if not config.database_url.startswith("postgresql+"):
        raise RuntimeError("PostgreSQL regression requires a PostgreSQL database URL")

    engine = create_database_engine(config)
    metadata.drop_all(engine)
    metadata.create_all(engine)

    with engine.begin() as connection:
        company_id = connection.execute(
            insert(companies)
            .values(
                name="AWS Test Site",
                contact_email="test@example.com",
                phone="",
                active=1,
                created_at="2026-08-31T00:00:00+00:00",
            )
            .returning(companies.c.id)
        ).scalar_one()
        user_id = connection.execute(
            insert(users)
            .values(
                company_id=company_id,
                role="operator",
                first_name="AWS",
                last_name="Tester",
                email="aws-test@example.com",
                password_hash=hash_password("temporary-test-password"),
                active=1,
                created_at="2026-08-31T00:00:00+00:00",
            )
            .returning(users.c.id)
        ).scalar_one()
        connection.execute(
            insert(sessions).values(
                token="aws-test-token",
                user_id=user_id,
                acting_company_id=None,
                csrf_token="aws-test-csrf",
                created_at="2026-08-31T00:00:00+00:00",
                expires_at="2026-09-01T00:00:00+00:00",
            )
        )
        connection.execute(
            insert(audit_log).values(
                company_id=company_id,
                actor_user_id=user_id,
                actor_role="operator",
                acting_company_id=None,
                action="AWS_PORTABILITY_TEST",
                entity_type="company",
                entity_id=str(company_id),
                before_json=None,
                after_json='{"ok": true}',
                created_at="2026-08-31T00:00:00+00:00",
            )
        )
        row = connection.execute(
            select(companies.c.name, users.c.email)
            .join(users, users.c.company_id == companies.c.id)
            .where(companies.c.id == company_id)
        ).mappings().one()
        assert row["name"] == "AWS Test Site"
        assert row["email"] == "aws-test@example.com"

        # Case-insensitive uniqueness must behave the same on SQLite and PostgreSQL.
        try:
            connection.execute(
                insert(companies).values(
                    name="aws test site",
                    contact_email="duplicate@example.com",
                    phone="",
                    active=1,
                    created_at="2026-08-31T00:00:00+00:00",
                )
            )
        except Exception:
            pass
        else:
            raise AssertionError("Company names must be unique case-insensitively")

    # Recreate after the deliberately failed transaction so cleanup is reliable.
    with engine.begin() as connection:
        connection.execute(delete(sessions))
        connection.execute(delete(audit_log))
        connection.execute(delete(users))
        connection.execute(delete(companies))

    metadata.drop_all(engine)
    engine.dispose()
    print("AWS PostgreSQL portability regression: passed")


if __name__ == "__main__":
    main()
