from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import insert

from .config import RuntimeConfig, load_runtime_config
from .db_compat import connect_engine
from .db_engine import create_database_engine
from .db_schema import audit_log, companies, metadata, sessions, users
from .security import hash_password, new_token


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


class OnlineDatabase:
    """Database-neutral DBS runtime.

    SQLite remains the default for local Windows development. PostgreSQL uses
    the same runtime API in hosted environments. Schema changes belong to
    Alembic migrations rather than feature-page startup code.
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
        self.engine = create_database_engine(self.config)
        self.path = self._sqlite_path()

    def _sqlite_path(self) -> Path | None:
        prefix = "sqlite:///"
        if self.config.database_url.startswith(prefix):
            return Path(self.config.database_url[len(prefix):])
        return None

    def connect(self):
        return connect_engine(self.engine)

    def initialise(self, *, seed_demo: bool = True) -> None:
        # Local/test SQLite remains self-contained. Hosted PostgreSQL is created
        # and upgraded only by Alembic before the application starts.
        if self.engine.dialect.name == "sqlite":
            metadata.create_all(self.engine)
        if seed_demo:
            self.seed_demo_data()

    def seed_demo_data(self) -> None:
        with self.connect() as connection:
            existing = connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
            if existing:
                return
            now = iso_now()
            forest = connection._connection.execute(
                insert(companies).values(
                    name="Forest View Campsite",
                    contact_email="bookings@forestview.test",
                    phone="+33 2 00 00 00 01",
                    active=1,
                    created_at=now,
                ).returning(companies.c.id)
            ).scalar_one()
            riverside = connection._connection.execute(
                insert(companies).values(
                    name="Riverside Cabins",
                    contact_email="hello@riverside.test",
                    phone="+33 2 00 00 00 02",
                    active=1,
                    created_at=now,
                ).returning(companies.c.id)
            ).scalar_one()
            demo_users = [
                (None, "supervisor", "Peter", "Supervisor", "supervisor@directbooking.test", "Supervisor013!"),
                (forest, "operator", "Forest", "Operator", "operator@forestview.test", "Operator013!"),
                (riverside, "operator", "Riverside", "Operator", "operator@riverside.test", "Operator013!"),
                (forest, "customer", "Test", "Customer", "customer@forestview.test", "Customer013!"),
            ]
            for company_id, role, first_name, last_name, email, password in demo_users:
                connection._connection.execute(
                    insert(users).values(
                        company_id=company_id,
                        role=role,
                        first_name=first_name,
                        last_name=last_name,
                        email=email,
                        password_hash=hash_password(password),
                        active=1,
                        created_at=now,
                    )
                )

    def user_by_email(self, email: str):
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM users WHERE lower(email)=lower(?) AND active=1", (email.strip(),)
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
            return connection.execute("SELECT * FROM companies WHERE active=1 ORDER BY lower(name)").fetchall()

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
            clauses.append("CAST(a.created_at AS DATE) >= CAST(? AS DATE)")
            params.append(date_from)
        if date_to:
            clauses.append("CAST(a.created_at AS DATE) <= CAST(? AS DATE)")
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
