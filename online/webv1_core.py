from __future__ import annotations

from .database import iso_now


def initialise_web_v1(database) -> None:
    """Record the Web V1 lifecycle version; Alembic owns all table creation."""
    with database.connect() as connection:
        connection.execute(
            "INSERT INTO web_schema_meta(key,value) VALUES ('schema_version','web-v1-foundation') ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
        connection.execute(
            "INSERT INTO web_schema_meta(key,value) VALUES ('created_at',?) ON CONFLICT(key) DO NOTHING",
            (iso_now(),),
        )


def lifecycle_counts(database, company_id: int) -> dict[str, int]:
    tables = ('customer_records', 'enquiries', 'offers', 'bookings', 'arrivals')
    result: dict[str, int] = {}
    with database.connect() as connection:
        for table in tables:
            row = connection.execute(f'SELECT COUNT(*) AS n FROM {table} WHERE company_id=?', (int(company_id),)).fetchone()
            result[table] = int(row['n'])
    return result
