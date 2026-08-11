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

    def counts(self) -> dict[str, int]:
        tables = ["enquiries", "bookings", "elements", "transactions"]
        return {table: self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}

    def close(self) -> None:
        self.connection.close()
