from __future__ import annotations

from .database import Database


RECORD_FOUNDATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    display_name TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS booking_clients (
    booking_id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    FOREIGN KEY(booking_id) REFERENCES bookings(id),
    FOREIGN KEY(client_id) REFERENCES clients(id),
    FOREIGN KEY(company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS booking_party_snapshot (
    booking_id INTEGER NOT NULL,
    person_type_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    person_type_name TEXT NOT NULL,
    person_type_label TEXT NOT NULL DEFAULT '',
    quantity INTEGER NOT NULL,
    PRIMARY KEY(booking_id, person_type_id),
    FOREIGN KEY(booking_id) REFERENCES bookings(id),
    FOREIGN KEY(company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS booking_pricing_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL,
    booking_line_id INTEGER,
    company_id INTEGER NOT NULL,
    pricing_year INTEGER,
    pricing_type TEXT NOT NULL DEFAULT '',
    calculation_text TEXT NOT NULL DEFAULT '',
    element_amount REAL NOT NULL DEFAULT 0,
    person_amount REAL NOT NULL DEFAULT 0,
    discount_amount REAL NOT NULL DEFAULT 0,
    manual_adjustment REAL NOT NULL DEFAULT 0,
    final_amount REAL NOT NULL DEFAULT 0,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(booking_id) REFERENCES bookings(id),
    FOREIGN KEY(booking_line_id) REFERENCES booking_lines(id),
    FOREIGN KEY(company_id) REFERENCES companies(id)
);
"""


def ensure_record_foundation(database: Database) -> None:
    """Create dormant future-facing record tables without changing current booking UX.

    These tables deliberately separate permanent clients, booking party snapshots and
    frozen pricing snapshots. They are not used to create bookings in Build 008; they
    simply prevent later Client Register / copy-booking work from requiring a destructive
    database redesign.
    """
    database.connection.executescript(RECORD_FOUNDATION_SCHEMA)
    database.connection.commit()
