from __future__ import annotations

from .database import iso_now

WEB_V1_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS customer_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    address1 TEXT NOT NULL DEFAULT '',
    address2 TEXT NOT NULL DEFAULT '',
    postcode TEXT NOT NULL DEFAULT '',
    town TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(company_id) REFERENCES companies(id)
);
CREATE INDEX IF NOT EXISTS idx_customer_records_company ON customer_records(company_id, active, last_name, first_name);
CREATE INDEX IF NOT EXISTS idx_customer_records_email ON customer_records(company_id, email COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS enquiries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    customer_id INTEGER,
    status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','qualified','closed','converted')),
    source TEXT NOT NULL DEFAULT '',
    arrival_date TEXT,
    departure_date TEXT,
    party_size INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(customer_id) REFERENCES customer_records(id)
);
CREATE INDEX IF NOT EXISTS idx_enquiries_company ON enquiries(company_id, status, created_at);

CREATE TABLE IF NOT EXISTS enquiry_requests (
    enquiry_id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL,
    element_type TEXT NOT NULL DEFAULT '',
    element_id INTEGER,
    provisional_total REAL,
    pricing_snapshot_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    FOREIGN KEY(enquiry_id) REFERENCES enquiries(id) ON DELETE CASCADE,
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(element_id) REFERENCES setup_elements(id)
);
CREATE INDEX IF NOT EXISTS idx_enquiry_requests_company ON enquiry_requests(company_id, element_type, element_id);

CREATE TABLE IF NOT EXISTS enquiry_people (
    enquiry_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    person_type_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
    PRIMARY KEY(enquiry_id, person_type_id),
    FOREIGN KEY(enquiry_id) REFERENCES enquiries(id) ON DELETE CASCADE,
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(person_type_id) REFERENCES setup_person_types(id)
);

CREATE TABLE IF NOT EXISTS enquiry_addons (
    enquiry_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    addon_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
    PRIMARY KEY(enquiry_id, addon_id),
    FOREIGN KEY(enquiry_id) REFERENCES enquiries(id) ON DELETE CASCADE,
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(addon_id) REFERENCES setup_addons(id)
);

CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    enquiry_id INTEGER,
    customer_id INTEGER,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','sent','accepted','declined','expired','cancelled')),
    valid_until TEXT,
    currency TEXT NOT NULL DEFAULT 'EUR',
    total_amount REAL NOT NULL DEFAULT 0 CHECK(total_amount >= 0),
    pricing_snapshot_json TEXT NOT NULL DEFAULT '{}',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(enquiry_id) REFERENCES enquiries(id),
    FOREIGN KEY(customer_id) REFERENCES customer_records(id)
);
CREATE INDEX IF NOT EXISTS idx_offers_company ON offers(company_id, status, created_at);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    reference TEXT NOT NULL,
    customer_id INTEGER,
    enquiry_id INTEGER,
    offer_id INTEGER,
    status TEXT NOT NULL DEFAULT 'provisional' CHECK(status IN ('provisional','confirmed','cancelled','completed','no_show')),
    arrival_date TEXT NOT NULL,
    departure_date TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'EUR',
    total_amount REAL NOT NULL DEFAULT 0 CHECK(total_amount >= 0),
    pricing_snapshot_json TEXT NOT NULL DEFAULT '{}',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(company_id, reference),
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(customer_id) REFERENCES customer_records(id),
    FOREIGN KEY(enquiry_id) REFERENCES enquiries(id),
    FOREIGN KEY(offer_id) REFERENCES offers(id)
);
CREATE INDEX IF NOT EXISTS idx_bookings_company_dates ON bookings(company_id, arrival_date, departure_date, status);

CREATE TABLE IF NOT EXISTS booking_elements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    booking_id INTEGER NOT NULL,
    element_id INTEGER NOT NULL,
    arrival_date TEXT NOT NULL,
    departure_date TEXT NOT NULL,
    pricing_method_snapshot TEXT NOT NULL,
    unit_price_snapshot REAL NOT NULL DEFAULT 0 CHECK(unit_price_snapshot >= 0),
    total_amount REAL NOT NULL DEFAULT 0 CHECK(total_amount >= 0),
    pricing_snapshot_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(booking_id) REFERENCES bookings(id) ON DELETE CASCADE,
    FOREIGN KEY(element_id) REFERENCES setup_elements(id)
);
CREATE INDEX IF NOT EXISTS idx_booking_elements_dates ON booking_elements(company_id, element_id, arrival_date, departure_date);

CREATE TABLE IF NOT EXISTS booking_people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    booking_element_id INTEGER NOT NULL,
    person_type_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
    unit_price_snapshot REAL NOT NULL DEFAULT 0 CHECK(unit_price_snapshot >= 0),
    total_amount REAL NOT NULL DEFAULT 0 CHECK(total_amount >= 0),
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(booking_element_id) REFERENCES booking_elements(id) ON DELETE CASCADE,
    FOREIGN KEY(person_type_id) REFERENCES setup_person_types(id)
);

CREATE TABLE IF NOT EXISTS booking_addons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    booking_element_id INTEGER NOT NULL,
    addon_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
    pricing_method_snapshot TEXT NOT NULL,
    unit_price_snapshot REAL NOT NULL DEFAULT 0 CHECK(unit_price_snapshot >= 0),
    total_amount REAL NOT NULL DEFAULT 0 CHECK(total_amount >= 0),
    rule_snapshot_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(booking_element_id) REFERENCES booking_elements(id) ON DELETE CASCADE,
    FOREIGN KEY(addon_id) REFERENCES setup_addons(id)
);

CREATE TABLE IF NOT EXISTS arrivals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    booking_id INTEGER,
    customer_id INTEGER,
    arrival_type TEXT NOT NULL CHECK(arrival_type IN ('booked','walk_in')),
    status TEXT NOT NULL DEFAULT 'expected' CHECK(status IN ('expected','arrived','checked_in','declined','cancelled')),
    checkin_method TEXT CHECK(checkin_method IS NULL OR checkin_method IN ('operator','self')),
    arrived_at TEXT,
    checked_in_at TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(booking_id) REFERENCES bookings(id),
    FOREIGN KEY(customer_id) REFERENCES customer_records(id)
);
CREATE INDEX IF NOT EXISTS idx_arrivals_company ON arrivals(company_id, status, created_at);

CREATE TABLE IF NOT EXISTS self_checkin_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    booking_id INTEGER NOT NULL,
    token TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(booking_id) REFERENCES bookings(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_checkin_booking ON self_checkin_tokens(company_id, booking_id);

CREATE TABLE IF NOT EXISTS web_schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def initialise_web_v1(database) -> None:
    """Create the permanent Web V1 lifecycle schema without changing existing Setup data."""
    with database.connect() as connection:
        connection.executescript(WEB_V1_SCHEMA)
        connection.execute(
            "INSERT OR REPLACE INTO web_schema_meta(key,value) VALUES ('schema_version','web-v1-full-enquiry')"
        )
        connection.execute(
            "INSERT OR IGNORE INTO web_schema_meta(key,value) VALUES ('created_at',?)",
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
