from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

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


class OnlineDatabase:
    """Build 013 local data layer.

    SQLite is deliberately used only for local development. The rest of the
    application talks through this class so the storage layer can be replaced
    by PostgreSQL when the VPS is introduced.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
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

    def initialise(self, *, seed_demo: bool = True) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
        if seed_demo:
            self.seed_demo_data()

    def seed_demo_data(self) -> None:
        with self.connect() as connection:
            existing = connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
            if existing:
                return
            now = iso_now()
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

    def user_by_email(self, email: str):
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM users WHERE email=? COLLATE NOCASE AND active=1", (email.strip(),)
            ).fetchone()

    def create_session(self, user_id: int) -> dict[str, str]:
        token = new_token()
        csrf = new_token()
        created = utc_now()
        expires = created + timedelta(hours=12)
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO sessions(token,user_id,acting_company_id,csrf_token,created_at,expires_at) VALUES (?,?,?,?,?,?)",
                (token, int(user_id), None, csrf, created.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds")),
            )
        return {"token": token, "csrf_token": csrf}

    def session_context(self, token: str | None):
        if not token:
            return None
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT s.token,s.csrf_token,s.acting_company_id,s.expires_at,
                       u.id AS user_id,u.company_id,u.role,u.first_name,u.last_name,u.email,
                       c.name AS company_name,
                       ac.name AS acting_company_name
                FROM sessions s
                JOIN users u ON u.id=s.user_id
                LEFT JOIN companies c ON c.id=u.company_id
                LEFT JOIN companies ac ON ac.id=s.acting_company_id
                WHERE s.token=? AND u.active=1
                """,
                (token,),
            ).fetchone()
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

    def delete_session(self, token: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token=?", (token,))

    def set_acting_company(self, token: str, company_id: int | None) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE sessions SET acting_company_id=? WHERE token=?", (company_id, token))

    def companies(self):
        with self.connect() as connection:
            return connection.execute("SELECT * FROM companies WHERE active=1 ORDER BY name COLLATE NOCASE").fetchall()

    def company(self, company_id: int):
        with self.connect() as connection:
            return connection.execute("SELECT * FROM companies WHERE id=? AND active=1", (int(company_id),)).fetchone()

    def update_company_contact(self, company_id: int, *, contact_email: str, phone: str) -> tuple[dict[str, str], dict[str, str]]:
        with self.connect() as connection:
            row = connection.execute("SELECT contact_email,phone FROM companies WHERE id=?", (int(company_id),)).fetchone()
            if row is None:
                raise ValueError("Client not found")
            before = {"contact_email": row["contact_email"], "phone": row["phone"]}
            after = {"contact_email": contact_email.strip(), "phone": phone.strip()}
            connection.execute(
                "UPDATE companies SET contact_email=?, phone=? WHERE id=?",
                (after["contact_email"], after["phone"], int(company_id)),
            )
            return before, after

    def write_audit(
        self,
        *,
        action: str,
        entity_type: str,
        entity_id: str | int | None,
        actor_user_id: int | None,
        actor_role: str | None,
        company_id: int | None,
        acting_company_id: int | None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_log(company_id,actor_user_id,actor_role,acting_company_id,action,entity_type,entity_id,before_json,after_json,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    company_id,
                    actor_user_id,
                    actor_role,
                    acting_company_id,
                    action,
                    entity_type,
                    None if entity_id is None else str(entity_id),
                    None if before is None else json.dumps(before, sort_keys=True),
                    None if after is None else json.dumps(after, sort_keys=True),
                    iso_now(),
                ),
            )

    def audit_rows(self, *, company_id: int | None = None, date_from: str = "", date_to: str = ""):
        clauses: list[str] = []
        params: list[Any] = []
        if company_id is not None:
            clauses.append("a.company_id=?")
            params.append(int(company_id))
        if date_from:
            clauses.append("date(a.created_at) >= date(?)")
            params.append(date_from)
        if date_to:
            clauses.append("date(a.created_at) <= date(?)")
            params.append(date_to)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            return connection.execute(
                f"""
                SELECT a.*, u.first_name,u.last_name,u.email,
                       c.name AS company_name, ac.name AS acting_company_name
                FROM audit_log a
                LEFT JOIN users u ON u.id=a.actor_user_id
                LEFT JOIN companies c ON c.id=a.company_id
                LEFT JOIN companies ac ON ac.id=a.acting_company_id
                {where}
                ORDER BY a.id DESC
                LIMIT 500
                """,
                params,
            ).fetchall()
