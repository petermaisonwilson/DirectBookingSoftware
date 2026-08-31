from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from .config import RuntimeConfig, load_runtime_config
from .db_engine import create_database_engine
from .security import hash_password, new_token

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    contact_email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER,
    role TEXT NOT NULL CHECK(role IN ('supervisor','operator','customer')),
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    FOREIGN KEY(company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    acting_company_id INTEGER,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(acting_company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER,
    actor_user_id INTEGER,
    actor_role TEXT,
    acting_company_id INTEGER,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    before_json TEXT,
    after_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(actor_user_id) REFERENCES users(id),
    FOREIGN KEY(acting_company_id) REFERENCES companies(id)
);

CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'Audit log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'Audit log is append-only');
END;
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


def _mapping(row):
    if row is None:
        return None
    mapping = getattr(row, "_mapping", None)
    return dict(mapping) if mapping is not None else row


class OnlineDatabase:
    """Direct Booking database owner.

    SQLite remains the local development/test backend. PostgreSQL is accessed
    explicitly through SQLAlchemy for the central company, user, session and
    audit operations. Feature modules are migrated separately rather than being
    hidden behind SQL rewriting.
    """

    def __init__(self, source: str | Path | RuntimeConfig | None = None):
        if isinstance(source, RuntimeConfig):
            self.config = source
        elif source is None:
            self.config = load_runtime_config()
        else:
            path = Path(source)
            path.parent.mkdir(parents=True, exist_ok=True)
            base = load_runtime_config()
            self.config = RuntimeConfig(
                environment=base.environment,
                database_url=f"sqlite:///{path.as_posix()}",
                seed_demo=base.seed_demo,
                secure_cookies=base.secure_cookies,
                host=base.host,
                port=base.port,
            )
        self.engine: Engine | None = None if self.is_sqlite else create_database_engine(self.config)
        self.path = self._sqlite_path()

    @property
    def is_sqlite(self) -> bool:
        return self.config.database_url.startswith("sqlite:")

    @property
    def dialect_name(self) -> str:
        return "sqlite" if self.is_sqlite else str(self.engine.dialect.name)

    def _sqlite_path(self) -> Path | None:
        if not self.is_sqlite:
            return None
        prefix = "sqlite:///"
        value = self.config.database_url
        if not value.startswith(prefix):
            raise RuntimeError("Only sqlite:/// URLs are supported for local SQLite")
        return Path(value[len(prefix):])

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open the existing SQLite feature connection.

        PostgreSQL feature modules are converted source-by-source; callers that
        still depend on SQLite-only SQL fail clearly rather than being silently
        translated.
        """
        if not self.is_sqlite:
            raise RuntimeError("This feature still requires source-owned PostgreSQL conversion")
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def sql_connection(self) -> Iterator[Connection]:
        if self.is_sqlite:
            raise RuntimeError("SQLAlchemy connection is reserved for the PostgreSQL runtime")
        assert self.engine is not None
        with self.engine.begin() as connection:
            yield connection

    def initialise(self, *, seed_demo: bool = True) -> None:
        if self.is_sqlite:
            with self.connect() as connection:
                connection.executescript(SCHEMA)
        else:
            assert self.engine is not None
            required = {"companies", "users", "sessions", "audit_log"}
            present = set(inspect(self.engine).get_table_names())
            missing = required - present
            if missing:
                raise RuntimeError(
                    "PostgreSQL schema is not migrated; missing tables: " + ", ".join(sorted(missing))
                )
        if seed_demo:
            self.seed_demo_data()

    def seed_demo_data(self) -> None:
        now = iso_now()
        if self.is_sqlite:
            with self.connect() as connection:
                existing = connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
                if existing:
                    return
                forest = connection.execute(
                    "INSERT INTO companies(name,contact_email,phone,created_at) VALUES (?,?,?,?)",
                    ("Forest View Campsite", "bookings@forestview.test", "+33 2 00 00 00 01", now),
                ).lastrowid
                riverside = connection.execute(
                    "INSERT INTO companies(name,contact_email,phone,created_at) VALUES (?,?,?,?)",
                    ("Riverside Cabins", "hello@riverside.test", "+33 2 00 00 00 02", now),
                ).lastrowid
                users = [
                    (None, "supervisor", "Peter", "Supervisor", "supervisor@directbooking.test", "Supervisor013!"),
                    (forest, "operator", "Forest", "Operator", "operator@forestview.test", "Operator013!"),
                    (riverside, "operator", "Riverside", "Operator", "operator@riverside.test", "Operator013!"),
                    (forest, "customer", "Test", "Customer", "customer@forestview.test", "Customer013!"),
                ]
                for company_id, role, first_name, last_name, email, password in users:
                    connection.execute(
                        """
                        INSERT INTO users(company_id,role,first_name,last_name,email,password_hash,created_at)
                        VALUES (?,?,?,?,?,?,?)
                        """,
                        (company_id, role, first_name, last_name, email, hash_password(password), now),
                    )
            return

        with self.sql_connection() as connection:
            if connection.execute(text("SELECT COUNT(*) FROM users")).scalar_one():
                return
            forest = connection.execute(
                text("""
                    INSERT INTO companies(name,contact_email,phone,active,created_at)
                    VALUES (:name,:email,:phone,1,:created_at) RETURNING id
                """),
                {"name": "Forest View Campsite", "email": "bookings@forestview.test", "phone": "+33 2 00 00 00 01", "created_at": now},
            ).scalar_one()
            riverside = connection.execute(
                text("""
                    INSERT INTO companies(name,contact_email,phone,active,created_at)
                    VALUES (:name,:email,:phone,1,:created_at) RETURNING id
                """),
                {"name": "Riverside Cabins", "email": "hello@riverside.test", "phone": "+33 2 00 00 00 02", "created_at": now},
            ).scalar_one()
            users = [
                (None, "supervisor", "Peter", "Supervisor", "supervisor@directbooking.test", "Supervisor013!"),
                (forest, "operator", "Forest", "Operator", "operator@forestview.test", "Operator013!"),
                (riverside, "operator", "Riverside", "Operator", "operator@riverside.test", "Operator013!"),
                (forest, "customer", "Test", "Customer", "customer@forestview.test", "Customer013!"),
            ]
            statement = text("""
                INSERT INTO users(company_id,role,first_name,last_name,email,password_hash,active,created_at)
                VALUES (:company_id,:role,:first_name,:last_name,:email,:password_hash,1,:created_at)
            """)
            for company_id, role, first_name, last_name, email, password in users:
                connection.execute(statement, {
                    "company_id": company_id, "role": role, "first_name": first_name,
                    "last_name": last_name, "email": email,
                    "password_hash": hash_password(password), "created_at": now,
                })

    def user_by_email(self, email: str):
        if self.is_sqlite:
            with self.connect() as connection:
                return connection.execute(
                    "SELECT * FROM users WHERE email=? COLLATE NOCASE AND active=1", (email.strip(),)
                ).fetchone()
        with self.sql_connection() as connection:
            row = connection.execute(
                text("SELECT * FROM users WHERE lower(email)=lower(:email) AND active=1"),
                {"email": email.strip()},
            ).fetchone()
            return _mapping(row)

    def create_session(self, user_id: int) -> dict[str, str]:
        token = new_token()
        csrf = new_token()
        created = utc_now()
        expires = created + timedelta(hours=12)
        values = {
            "token": token, "user_id": int(user_id), "acting_company_id": None, "csrf_token": csrf,
            "created_at": created.isoformat(timespec="seconds"), "expires_at": expires.isoformat(timespec="seconds"),
        }
        if self.is_sqlite:
            with self.connect() as connection:
                connection.execute(
                    "INSERT INTO sessions(token,user_id,acting_company_id,csrf_token,created_at,expires_at) VALUES (?,?,?,?,?,?)",
                    tuple(values[key] for key in ("token", "user_id", "acting_company_id", "csrf_token", "created_at", "expires_at")),
                )
        else:
            with self.sql_connection() as connection:
                connection.execute(text("""
                    INSERT INTO sessions(token,user_id,acting_company_id,csrf_token,created_at,expires_at)
                    VALUES (:token,:user_id,:acting_company_id,:csrf_token,:created_at,:expires_at)
                """), values)
        return {"token": token, "csrf_token": csrf}

    def session_context(self, token: str | None):
        if not token:
            return None
        query = """
            SELECT s.token,s.csrf_token,s.acting_company_id,s.expires_at,
                   u.id AS user_id,u.company_id,u.role,u.first_name,u.last_name,u.email,
                   c.name AS company_name, ac.name AS acting_company_name
            FROM sessions s
            JOIN users u ON u.id=s.user_id
            LEFT JOIN companies c ON c.id=u.company_id
            LEFT JOIN companies ac ON ac.id=s.acting_company_id
            WHERE s.token={token_expr} AND u.active=1
        """
        if self.is_sqlite:
            with self.connect() as connection:
                row = connection.execute(query.format(token_expr="?"), (token,)).fetchone()
                if row is None:
                    return None
                try:
                    expires = datetime.fromisoformat(row["expires_at"])
                except ValueError:
                    return None
                if expires <= utc_now():
                    connection.execute("DELETE FROM sessions WHERE token=?", (token,))
                    return None
                return row
        with self.sql_connection() as connection:
            row = connection.execute(text(query.format(token_expr=":token")), {"token": token}).fetchone()
            if row is None:
                return None
            mapped = _mapping(row)
            try:
                expires = datetime.fromisoformat(mapped["expires_at"])
            except ValueError:
                return None
            if expires <= utc_now():
                connection.execute(text("DELETE FROM sessions WHERE token=:token"), {"token": token})
                return None
            return mapped

    def delete_session(self, token: str) -> None:
        if self.is_sqlite:
            with self.connect() as connection:
                connection.execute("DELETE FROM sessions WHERE token=?", (token,))
        else:
            with self.sql_connection() as connection:
                connection.execute(text("DELETE FROM sessions WHERE token=:token"), {"token": token})

    def set_acting_company(self, token: str, company_id: int | None) -> None:
        if self.is_sqlite:
            with self.connect() as connection:
                connection.execute("UPDATE sessions SET acting_company_id=? WHERE token=?", (company_id, token))
        else:
            with self.sql_connection() as connection:
                connection.execute(
                    text("UPDATE sessions SET acting_company_id=:company_id WHERE token=:token"),
                    {"company_id": company_id, "token": token},
                )

    def companies(self):
        if self.is_sqlite:
            with self.connect() as connection:
                return connection.execute("SELECT * FROM companies WHERE active=1 ORDER BY name COLLATE NOCASE").fetchall()
        with self.sql_connection() as connection:
            return [dict(row._mapping) for row in connection.execute(text("SELECT * FROM companies WHERE active=1 ORDER BY lower(name)")).fetchall()]

    def company(self, company_id: int):
        if self.is_sqlite:
            with self.connect() as connection:
                return connection.execute("SELECT * FROM companies WHERE id=? AND active=1", (int(company_id),)).fetchone()
        with self.sql_connection() as connection:
            return _mapping(connection.execute(
                text("SELECT * FROM companies WHERE id=:company_id AND active=1"), {"company_id": int(company_id)}
            ).fetchone())

    def update_company_contact(self, company_id: int, *, contact_email: str, phone: str) -> tuple[dict[str, str], dict[str, str]]:
        if self.is_sqlite:
            with self.connect() as connection:
                row = connection.execute("SELECT contact_email,phone FROM companies WHERE id=?", (int(company_id),)).fetchone()
                if row is None:
                    raise ValueError("Client not found")
                before = {"contact_email": row["contact_email"], "phone": row["phone"]}
                after = {"contact_email": contact_email.strip(), "phone": phone.strip()}
                connection.execute("UPDATE companies SET contact_email=?, phone=? WHERE id=?", (after["contact_email"], after["phone"], int(company_id)))
                return before, after
        with self.sql_connection() as connection:
            row = connection.execute(
                text("SELECT contact_email,phone FROM companies WHERE id=:company_id"), {"company_id": int(company_id)}
            ).fetchone()
            if row is None:
                raise ValueError("Client not found")
            before = dict(row._mapping)
            after = {"contact_email": contact_email.strip(), "phone": phone.strip()}
            connection.execute(text("""
                UPDATE companies SET contact_email=:contact_email, phone=:phone WHERE id=:company_id
            """), {**after, "company_id": int(company_id)})
            return before, after

    def write_audit(
        self, *, action: str, entity_type: str, entity_id: str | int | None,
        actor_user_id: int | None, actor_role: str | None, company_id: int | None,
        acting_company_id: int | None, before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        values = {
            "company_id": company_id, "actor_user_id": actor_user_id, "actor_role": actor_role,
            "acting_company_id": acting_company_id, "action": action, "entity_type": entity_type,
            "entity_id": None if entity_id is None else str(entity_id),
            "before_json": None if before is None else json.dumps(before, sort_keys=True),
            "after_json": None if after is None else json.dumps(after, sort_keys=True), "created_at": iso_now(),
        }
        if self.is_sqlite:
            with self.connect() as connection:
                connection.execute("""
                    INSERT INTO audit_log(company_id,actor_user_id,actor_role,acting_company_id,action,entity_type,entity_id,before_json,after_json,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, tuple(values[key] for key in ("company_id","actor_user_id","actor_role","acting_company_id","action","entity_type","entity_id","before_json","after_json","created_at")))
        else:
            with self.sql_connection() as connection:
                connection.execute(text("""
                    INSERT INTO audit_log(company_id,actor_user_id,actor_role,acting_company_id,action,entity_type,entity_id,before_json,after_json,created_at)
                    VALUES (:company_id,:actor_user_id,:actor_role,:acting_company_id,:action,:entity_type,:entity_id,:before_json,:after_json,:created_at)
                """), values)

    def audit_rows(self, *, company_id: int | None = None, date_from: str = "", date_to: str = ""):
        if self.is_sqlite:
            clauses: list[str] = []
            params: list[Any] = []
            if company_id is not None:
                clauses.append("a.company_id=?"); params.append(int(company_id))
            if date_from:
                clauses.append("date(a.created_at) >= date(?)"); params.append(date_from)
            if date_to:
                clauses.append("date(a.created_at) <= date(?)"); params.append(date_to)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            with self.connect() as connection:
                return connection.execute(f"""
                    SELECT a.*, u.first_name,u.last_name,u.email,
                           c.name AS company_name, ac.name AS acting_company_name
                    FROM audit_log a
                    LEFT JOIN users u ON u.id=a.actor_user_id
                    LEFT JOIN companies c ON c.id=a.company_id
                    LEFT JOIN companies ac ON ac.id=a.acting_company_id
                    {where}
                    ORDER BY a.id DESC LIMIT 500
                """, params).fetchall()

        clauses = []
        params: dict[str, Any] = {}
        if company_id is not None:
            clauses.append("a.company_id=:company_id"); params["company_id"] = int(company_id)
        if date_from:
            clauses.append("CAST(a.created_at AS DATE) >= CAST(:date_from AS DATE)"); params["date_from"] = date_from
        if date_to:
            clauses.append("CAST(a.created_at AS DATE) <= CAST(:date_to AS DATE)"); params["date_to"] = date_to
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.sql_connection() as connection:
            result = connection.execute(text(f"""
                SELECT a.*, u.first_name,u.last_name,u.email,
                       c.name AS company_name, ac.name AS acting_company_name
                FROM audit_log a
                LEFT JOIN users u ON u.id=a.actor_user_id
                LEFT JOIN companies c ON c.id=a.company_id
                LEFT JOIN companies ac ON ac.id=a.acting_company_id
                {where}
                ORDER BY a.id DESC LIMIT 500
            """), params).fetchall()
            return [dict(row._mapping) for row in result]
