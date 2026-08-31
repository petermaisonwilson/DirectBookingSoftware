from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine, Result
from sqlalchemy.exc import IntegrityError


class DatabaseRow(Mapping[str, Any]):
    """Small row wrapper preserving the sqlite3.Row behaviour DBS already uses."""

    def __init__(self, mapping: Mapping[str, Any]):
        self._data = dict(mapping)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def keys(self):
        return self._data.keys()


class DatabaseResult:
    def __init__(self, result: Result[Any], lastrowid: Any = None):
        self._result = result
        self._lastrowid = lastrowid

    @property
    def lastrowid(self):
        return self._lastrowid if self._lastrowid is not None else getattr(self._result, "lastrowid", None)

    def fetchone(self):
        row = self._result.mappings().fetchone()
        return DatabaseRow(row) if row is not None else None

    def fetchall(self):
        return [DatabaseRow(row) for row in self._result.mappings().fetchall()]

    def __iter__(self):
        for row in self._result.mappings():
            yield DatabaseRow(row)


_QMARK = re.compile(r"\?")
_INSERT_TABLE = re.compile(r"^\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", re.I)


def _named_sql(sql: str, params: Sequence[Any]) -> tuple[str, dict[str, Any]]:
    expected = sql.count("?")
    if expected != len(params):
        raise ValueError(f"SQL parameter count mismatch: expected {expected}, got {len(params)}")
    index = 0

    def replace(_: re.Match[str]) -> str:
        nonlocal index
        name = f"p{index}"
        index += 1
        return f":{name}"

    converted = _QMARK.sub(replace, sql)
    return converted, {f"p{i}": value for i, value in enumerate(params)}


class DatabaseConnection:
    """DBS connection API backed by SQLAlchemy on SQLite or PostgreSQL.

    Existing feature SQL can be converted deliberately without changing every
    call site at once. SQLite-only DDL and metadata inspection are not emulated;
    schema ownership belongs to Alembic migrations.
    """

    def __init__(self, connection: Connection):
        self._connection = connection

    @property
    def dialect_name(self) -> str:
        return self._connection.dialect.name

    def execute(self, sql: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> DatabaseResult:
        if isinstance(params, Mapping):
            statement_sql = sql
            bound = dict(params)
        else:
            statement_sql, bound = _named_sql(sql, tuple(params))

        lastrowid = None
        insert_match = _INSERT_TABLE.match(statement_sql)
        if self.dialect_name == "postgresql" and insert_match and " returning " not in statement_sql.lower():
            table = insert_match.group(1)
            if "id" in self.column_names(table):
                statement_sql = statement_sql.rstrip().rstrip(";") + " RETURNING id"
                raw = self._connection.execute(text(statement_sql), bound)
                lastrowid = raw.scalar_one()
                return DatabaseResult(raw, lastrowid)

        raw = self._connection.execute(text(statement_sql), bound)
        return DatabaseResult(raw, lastrowid)

    def table_exists(self, table: str) -> bool:
        return inspect(self._connection).has_table(table)

    def column_names(self, table: str) -> set[str]:
        if not self.table_exists(table):
            return set()
        return {str(column["name"]) for column in inspect(self._connection).get_columns(table)}


@contextmanager
def connect_engine(engine: Engine) -> Iterator[DatabaseConnection]:
    with engine.begin() as connection:
        yield DatabaseConnection(connection)


__all__ = ["DatabaseConnection", "DatabaseResult", "DatabaseRow", "IntegrityError", "connect_engine"]
