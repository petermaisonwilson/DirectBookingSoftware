from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    username TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'Operator',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS elements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    group_name TEXT NOT NULL DEFAULT '',
    pricing_type TEXT NOT NULL DEFAULT 'Per night',
    base_price REAL NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS seasons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS enquiries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    customer_name TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    arrival_date TEXT,
    departure_date TEXT,
    guests INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'Enquiry',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    enquiry_id INTEGER,
    total_amount REAL NOT NULL DEFAULT 0,
    deposit_required REAL NOT NULL DEFAULT 0,
    expiry_date TEXT,
    balance_due_date TEXT,
    status TEXT NOT NULL DEFAULT 'Draft',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(enquiry_id) REFERENCES enquiries(id)
);

CREATE TABLE IF NOT EXISTS offer_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id INTEGER NOT NULL,
    element_id INTEGER,
    description TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    amount REAL NOT NULL DEFAULT 0,
    FOREIGN KEY(offer_id) REFERENCES offers(id),
    FOREIGN KEY(element_id) REFERENCES elements(id)
);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    enquiry_id INTEGER,
    booking_ref TEXT NOT NULL UNIQUE,
    customer_name TEXT NOT NULL,
    arrival_date TEXT,
    departure_date TEXT,
    status TEXT NOT NULL DEFAULT 'Confirmed',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(enquiry_id) REFERENCES enquiries(id)
);

CREATE TABLE IF NOT EXISTS booking_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL,
    element_id INTEGER,
    description TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    amount REAL NOT NULL DEFAULT 0,
    FOREIGN KEY(booking_id) REFERENCES bookings(id),
    FOREIGN KEY(element_id) REFERENCES elements(id)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    booking_id INTEGER NOT NULL,
    transaction_type TEXT NOT NULL,
    amount REAL NOT NULL,
    transaction_date TEXT NOT NULL,
    payment_method TEXT NOT NULL DEFAULT '',
    receipt_reference TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(booking_id) REFERENCES bookings(id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    user_id INTEGER,
    enquiry_id INTEGER,
    booking_id INTEGER,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(enquiry_id) REFERENCES enquiries(id),
    FOREIGN KEY(booking_id) REFERENCES bookings(id)
);

CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    setting_key TEXT NOT NULL,
    setting_value TEXT NOT NULL DEFAULT '',
    UNIQUE(company_id, setting_key),
    FOREIGN KEY(company_id) REFERENCES companies(id)
);
"""


DEFAULT_SETTINGS = {
    "operator_name": "Demo Operator",
    "operator_address": "",
    "operator_email": "",
    "operator_phone": "",
    "offer_expiry_days": "7",
    "balance_due_weeks": "6",
    "deposit_mode": "Percentage",
    "deposit_percentage": "25",
    "deposit_fixed_amount": "100",
    "small_booking_threshold": "150",
    "balance_payment_weeks": "6",
}


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")

    def initialise(self) -> None:
        self.connection.executescript(SCHEMA)
        self.connection.commit()
        self._seed_first_run()
        self._seed_settings()

    def company_id(self) -> int:
        row = self.connection.execute("SELECT id FROM companies ORDER BY id LIMIT 1").fetchone()
        if row is None:
            raise RuntimeError("No company record exists")
        return int(row["id"])

    def _seed_first_run(self) -> None:
        company_count = self.connection.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        if company_count:
            return
        cursor = self.connection.execute("INSERT INTO companies(name) VALUES (?)", ("Demo Operator",))
        company_id = cursor.lastrowid
        self.connection.execute(
            "INSERT INTO users(company_id, name, username, role) VALUES (?, ?, ?, ?)",
            (company_id, "Demo Operator", "demo", "Administrator"),
        )
        self.connection.executemany(
            "INSERT INTO elements(company_id, name, group_name, pricing_type, base_price) VALUES (?, ?, ?, ?, ?)",
            [
                (company_id, "Gite Rose", "Accommodation", "Per night", 80.0),
                (company_id, "Cabin 2", "Accommodation", "Per night", 65.0),
                (company_id, "Peg 9", "Fishing", "Per day", 20.0),
            ],
        )
        self.connection.commit()

    def _seed_settings(self) -> None:
        company_id = self.company_id()
        for key, value in DEFAULT_SETTINGS.items():
            self.connection.execute(
                "INSERT OR IGNORE INTO settings(company_id, setting_key, setting_value) VALUES (?, ?, ?)",
                (company_id, key, value),
            )
        self.connection.commit()

    def get_settings(self) -> dict[str, str]:
        values = dict(DEFAULT_SETTINGS)
        rows = self.connection.execute(
            "SELECT setting_key, setting_value FROM settings WHERE company_id = ?",
            (self.company_id(),),
        ).fetchall()
        values.update({row["setting_key"]: row["setting_value"] for row in rows})
        return values

    def save_settings(self, values: dict[str, str]) -> None:
        company_id = self.company_id()
        for key, value in values.items():
            self.connection.execute(
                """
                INSERT INTO settings(company_id, setting_key, setting_value)
                VALUES (?, ?, ?)
                ON CONFLICT(company_id, setting_key)
                DO UPDATE SET setting_value = excluded.setting_value
                """,
                (company_id, key, str(value)),
            )
        if "operator_name" in values and values["operator_name"].strip():
            self.connection.execute(
                "UPDATE companies SET name = ? WHERE id = ?",
                (values["operator_name"].strip(), company_id),
            )
        self.connection.commit()

    def list_elements(self, include_inactive: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM elements WHERE company_id = ?"
        params: list[object] = [self.company_id()]
        if not include_inactive:
            sql += " AND active = 1"
        sql += " ORDER BY active DESC, group_name COLLATE NOCASE, name COLLATE NOCASE"
        return self.connection.execute(sql, params).fetchall()

    def save_element(
        self,
        element_id: int | None,
        name: str,
        group_name: str,
        pricing_type: str,
        base_price: float,
        active: bool = True,
    ) -> int:
        if not name.strip():
            raise ValueError("Element name is required")
        if base_price < 0:
            raise ValueError("Base price cannot be negative")
        if element_id is None:
            cursor = self.connection.execute(
                """
                INSERT INTO elements(company_id, name, group_name, pricing_type, base_price, active)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (self.company_id(), name.strip(), group_name.strip(), pricing_type, float(base_price), int(active)),
            )
            element_id = int(cursor.lastrowid)
        else:
            self.connection.execute(
                """
                UPDATE elements
                SET name = ?, group_name = ?, pricing_type = ?, base_price = ?, active = ?
                WHERE id = ? AND company_id = ?
                """,
                (name.strip(), group_name.strip(), pricing_type, float(base_price), int(active), element_id, self.company_id()),
            )
        self.connection.commit()
        return element_id

    def set_element_active(self, element_id: int, active: bool) -> None:
        self.connection.execute(
            "UPDATE elements SET active = ? WHERE id = ? AND company_id = ?",
            (int(active), element_id, self.company_id()),
        )
        self.connection.commit()

    def list_seasons(self, include_inactive: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM seasons WHERE company_id = ?"
        params: list[object] = [self.company_id()]
        if not include_inactive:
            sql += " AND active = 1"
        sql += " ORDER BY active DESC, priority DESC, start_date, name COLLATE NOCASE"
        return self.connection.execute(sql, params).fetchall()

    def save_season(
        self,
        season_id: int | None,
        name: str,
        start_date: str,
        end_date: str,
        priority: int,
        active: bool = True,
    ) -> int:
        if not name.strip():
            raise ValueError("Season name is required")
        if end_date < start_date:
            raise ValueError("Season end date cannot be before its start date")
        if season_id is None:
            cursor = self.connection.execute(
                """
                INSERT INTO seasons(company_id, name, start_date, end_date, priority, active)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (self.company_id(), name.strip(), start_date, end_date, int(priority), int(active)),
            )
            season_id = int(cursor.lastrowid)
        else:
            self.connection.execute(
                """
                UPDATE seasons
                SET name = ?, start_date = ?, end_date = ?, priority = ?, active = ?
                WHERE id = ? AND company_id = ?
                """,
                (name.strip(), start_date, end_date, int(priority), int(active), season_id, self.company_id()),
            )
        self.connection.commit()
        return season_id

    def set_season_active(self, season_id: int, active: bool) -> None:
        self.connection.execute(
            "UPDATE seasons SET active = ? WHERE id = ? AND company_id = ?",
            (int(active), season_id, self.company_id()),
        )
        self.connection.commit()

    def counts(self) -> dict[str, int]:
        return {
            "enquiries": self.connection.execute("SELECT COUNT(*) FROM enquiries").fetchone()[0],
            "bookings": self.connection.execute("SELECT COUNT(*) FROM bookings").fetchone()[0],
            "elements": self.connection.execute("SELECT COUNT(*) FROM elements WHERE active = 1").fetchone()[0],
            "transactions": self.connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
        }

    def close(self) -> None:
        self.connection.close()
