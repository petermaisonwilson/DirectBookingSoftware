from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine, Result
from sqlalchemy.exc import IntegrityError


class DatabaseRow(Mapping[str, Any]):
    def __init__(self, mapping: Mapping[str, Any]):
        self._data = dict(mapping)
    def __getitem__(self, key: str) -> Any: return self._data[key]
    def __iter__(self): return iter(self._data)
    def __len__(self) -> int: return len(self._data)
    def keys(self): return self._data.keys()


class DatabaseResult:
    def __init__(self, result: Result[Any], lastrowid: Any = None):
        self._result = result; self._lastrowid = lastrowid
    @property
    def lastrowid(self): return self._lastrowid if self._lastrowid is not None else getattr(self._result, "lastrowid", None)
    def fetchone(self):
        row = self._result.mappings().fetchone(); return DatabaseRow(row) if row is not None else None
    def fetchall(self): return [DatabaseRow(row) for row in self._result.mappings().fetchall()]
    def __iter__(self):
        for row in self._result.mappings(): yield DatabaseRow(row)


_QMARK = re.compile(r"\?")
_INSERT_TABLE = re.compile(r"^\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)


def _named_sql(sql: str, params: Sequence[Any]) -> tuple[str, dict[str, Any]]:
    expected = sql.count("?")
    if expected != len(params): raise ValueError(f"SQL parameter count mismatch: expected {expected}, got {len(params)}")
    index = 0
    def replace(_: re.Match[str]) -> str:
        nonlocal index
        name = f"p{index}"; index += 1; return f":{name}"
    converted = _QMARK.sub(replace, sql)
    return converted, {f"p{i}": value for i, value in enumerate(params)}


class DatabaseConnection:
    """DBS SQL execution on SQLite or PostgreSQL; schema ownership stays in migrations."""
    def __init__(self, connection: Connection): self._connection = connection
    @property
    def dialect_name(self) -> str: return self._connection.dialect.name
    def execute(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> DatabaseResult:
        if isinstance(params, Mapping): statement_sql, bound = sql, dict(params)
        else: statement_sql, bound = _named_sql(sql, tuple(params))
        insert_match = _INSERT_TABLE.match(statement_sql)
        if self.dialect_name == "postgresql" and insert_match and " returning " not in statement_sql.lower():
            table = insert_match.group(1)
            if "id" in self.column_names(table):
                statement_sql = statement_sql.rstrip().rstrip(";") + " RETURNING id"
                raw = self._connection.execute(text(statement_sql), bound)
                lastrowid = raw.scalar_one()
                return DatabaseResult(raw, lastrowid)
        raw = self._connection.execute(text(statement_sql), bound)
        return DatabaseResult(raw)
    def table_exists(self, table: str) -> bool: return inspect(self._connection).has_table(table)
    def column_names(self, table: str) -> set[str]:
        if not self.table_exists(table): return set()
        return {str(column["name"]) for column in inspect(self._connection).get_columns(table)}


@contextmanager
def connect_engine(engine: Engine) -> Iterator[DatabaseConnection]:
    with engine.begin() as connection: yield DatabaseConnection(connection)


def ensure_portable_schema(database) -> None:
    """Apply the same numbered migrations to local SQLite and hosted PostgreSQL."""
    from alembic import command
    from alembic.config import Config
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database.config.database_url.replace("%", "%%"))
    command.upgrade(config, "head")


def seed_runtime_defaults(database) -> None:
    """Seed rows that depend on Client data; safe and idempotent on both engines."""
    from .database import iso_now
    now = iso_now()
    default_statuses = (
        ("Enquiry / Held", "Held", "#FFE39A", 10, "HELD", 1, 10),
        ("Deposit Paid", "Deposit", "#F3C5C9", 20, "CONFIRMED", 1, None),
        ("Balance Paid", "Paid", "#CDECCF", 30, "CONFIRMED", 1, None),
        ("On Site", "On site", "#CFE2FF", 40, "ON_SITE", 1, None),
        ("Released / Cancelled", "Released", "#E5E7EB", 90, "RELEASED", 0, None),
    )
    with database.connect() as c:
        for company in c.execute("SELECT id FROM companies").fetchall():
            cid = int(company["id"])
            c.execute("INSERT INTO company_hold_settings(company_id,hold_seconds,grace_seconds,updated_at) VALUES (?,?,?,?) ON CONFLICT(company_id) DO NOTHING", (cid,600,60,now))
            for name, short, colour, order_no, state, blocks, expiry in default_statuses:
                c.execute("""INSERT INTO booking_status_definitions(company_id,name,short_name,colour,display_order,internal_state,blocks_availability,expiry_minutes,automation_config_json,active,created_at,updated_at)
                             VALUES (?,?,?,?,?,?,?,?,?,1,?,?) ON CONFLICT DO NOTHING""", (cid,name,short,colour,order_no,state,blocks,expiry,"{}",now,now))
        c.execute("INSERT INTO setup_element_types(company_id,name,active) SELECT DISTINCT company_id,TRIM(element_type),1 FROM setup_elements WHERE TRIM(element_type)<>'' ON CONFLICT DO NOTHING")
        c.execute("INSERT INTO setup_addon_person_pricing(company_id,addon_id,pricing_mode) SELECT company_id,id,'single' FROM setup_addons ON CONFLICT DO NOTHING")
        c.execute("INSERT INTO setup_addon_when_options(company_id,addon_id,option_code,label,active,sort_order) SELECT company_id,id,'every_day','Every day',1,1 FROM setup_addons ON CONFLICT DO NOTHING")
        c.execute("INSERT INTO setup_addon_when_options(company_id,addon_id,option_code,label,active,sort_order) SELECT company_id,id,'selected_days','Selected days',0,2 FROM setup_addons ON CONFLICT DO NOTHING")


__all__ = ["DatabaseConnection", "DatabaseResult", "DatabaseRow", "IntegrityError", "connect_engine", "ensure_portable_schema", "seed_runtime_defaults"]
