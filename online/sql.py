"""Portable execution of source-owned SQL statements.

SQL is declared with named binds in its owning module. This module never rewrites
SQL or substitutes dialect syntax. Positional Python values are bound to the
statement's declared names in order for the older, public rows()/one() API.
"""
from collections.abc import Mapping
import re
import sqlite3
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

INTEGRITY_ERRORS = (sqlite3.IntegrityError, IntegrityError)
_BIND = re.compile(r'(?<!:):([A-Za-z_]\w*)')


class DataRow(Mapping):
    def __init__(self, row):
        self.data = dict(row._mapping)
        self._values = tuple(row)

    def __getitem__(self, key):
        return self._values[key] if isinstance(key, (int, slice)) else self.data[key]

    def __iter__(self):
        return iter(self.data)

    def __len__(self):
        return len(self.data)


class DataResult:
    def __init__(self, result, inserted_id=None):
        self.result = result
        self.lastrowid = inserted_id

    @property
    def rowcount(self):
        return self.result.rowcount

    def fetchone(self):
        row = self.result.fetchone()
        return None if row is None else DataRow(row)

    def fetchall(self):
        return [DataRow(row) for row in self.result.fetchall()]

    def __iter__(self):
        for row in self.result:
            yield DataRow(row)


def bind_values(statement, values):
    if isinstance(values, Mapping):
        return values
    names = _BIND.findall(statement)
    values = tuple(values or ())
    if len(names) != len(values):
        raise ValueError(f'SQL declares {len(names)} parameters but received {len(values)} values')
    return dict(zip(names, values))


def execute_sql(connection, statement, values=()):
    parameters = bind_values(statement, values)
    if hasattr(connection, 'dialect'):
        return DataResult(connection.execute(text(statement), parameters))
    return connection.execute(statement, parameters)


def insert_sql(connection, statement, values=(), *, id_column='id'):
    """Explicit generated-key insert; callers opt in at the source statement."""
    parameters = bind_values(statement, values)
    if hasattr(connection, 'dialect'):
        if not re.fullmatch(r'[a-z_]+', id_column):
            raise ValueError('Invalid generated-key column')
        result = connection.execute(text(statement + ' RETURNING ' + id_column), parameters)
        inserted_id = result.scalar_one()
        return DataResult(result, inserted_id)
    return connection.execute(statement, parameters)


def table_exists(connection, table):
    if hasattr(connection, 'dialect'):
        return inspect(connection).has_table(table)
    return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name", {'name': table}).fetchone() is not None


def column_rows(connection, table):
    if hasattr(connection, 'dialect'):
        return inspect(connection).get_columns(table)
    # Identifiers are internal constants, never request input.
    if not re.fullmatch(r'[a-z_]+', table):
        raise ValueError('Invalid table identifier')
    return connection.execute(f'PRAGMA table_info({table})').fetchall()


def local_schema(connection, script):
    """Legacy local-file upgrade only; PostgreSQL schema is owned by Alembic."""
    if not hasattr(connection, 'dialect'):
        connection.executescript(script)


def lock_inventory(connection, company_id):
    if hasattr(connection, 'dialect'):
        execute_sql(connection, 'SELECT pg_advisory_xact_lock(:namespace,:tenant)',
                    {'namespace': 302, 'tenant': int(company_id)})
    else:
        connection.execute('BEGIN IMMEDIATE')
