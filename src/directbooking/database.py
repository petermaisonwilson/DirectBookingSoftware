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

CREATE TABLE IF NOT EXISTS discount_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    min_nights INTEGER NOT NULL DEFAULT 1,
    discount_type TEXT NOT NULL,
    discount_value REAL NOT NULL DEFAULT 0,
    scope_type TEXT NOT NULL DEFAULT 'All elements',
    element_id INTEGER,
    group_name TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(element_id) REFERENCES elements(id)
);

CREATE TABLE IF NOT EXISTS person_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    short_label TEXT NOT NULL COLLATE NOCASE,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company_id, name),
    UNIQUE(company_id, short_label),
    FOREIGN KEY(company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS element_capacity (
    element_id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL,
    max_total INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(element_id) REFERENCES elements(id)
);

CREATE TABLE IF NOT EXISTS element_person_limits (
    element_id INTEGER NOT NULL,
    person_type_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    max_count INTEGER NOT NULL,
    PRIMARY KEY(element_id, person_type_id),
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(element_id) REFERENCES elements(id),
    FOREIGN KEY(person_type_id) REFERENCES person_types(id)
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

DISCOUNT_TYPES = {"Percentage", "Fixed amount", "Free nights"}
DISCOUNT_SCOPES = {"All elements", "Group", "Element"}


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

    def save_element(self, element_id: int | None, name: str, group_name: str, pricing_type: str, base_price: float, active: bool = True) -> int:
        if not name.strip():
            raise ValueError("Element name is required")
        if base_price < 0:
            raise ValueError("Base price cannot be negative")
        if element_id is None:
            cursor = self.connection.execute(
                "INSERT INTO elements(company_id, name, group_name, pricing_type, base_price, active) VALUES (?, ?, ?, ?, ?, ?)",
                (self.company_id(), name.strip(), group_name.strip(), pricing_type, float(base_price), int(active)),
            )
            element_id = int(cursor.lastrowid)
        else:
            self.connection.execute(
                "UPDATE elements SET name = ?, group_name = ?, pricing_type = ?, base_price = ?, active = ? WHERE id = ? AND company_id = ?",
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

    def element_usage_count(self, element_id: int) -> int:
        offer_count = self.connection.execute("SELECT COUNT(*) FROM offer_lines WHERE element_id = ?", (element_id,)).fetchone()[0]
        booking_count = self.connection.execute("SELECT COUNT(*) FROM booking_lines WHERE element_id = ?", (element_id,)).fetchone()[0]
        return int(offer_count) + int(booking_count)

    def delete_element(self, element_id: int) -> None:
        row = self.connection.execute(
            "SELECT id, name FROM elements WHERE id = ? AND company_id = ?",
            (element_id, self.company_id()),
        ).fetchone()
        if row is None:
            raise ValueError("Element no longer exists")
        if self.element_usage_count(element_id):
            raise ValueError(
                f"{row['name']} cannot be deleted because it has already been used in an offer or booking. "
                "Make it inactive instead so historical records remain intact."
            )
        self.connection.execute("DELETE FROM discount_rules WHERE company_id = ? AND element_id = ?", (self.company_id(), element_id))
        self.connection.execute("DELETE FROM element_person_limits WHERE company_id = ? AND element_id = ?", (self.company_id(), element_id))
        self.connection.execute("DELETE FROM element_capacity WHERE company_id = ? AND element_id = ?", (self.company_id(), element_id))
        self.connection.execute("DELETE FROM elements WHERE id = ? AND company_id = ?", (element_id, self.company_id()))
        self.connection.commit()

    def list_seasons(self, include_inactive: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM seasons WHERE company_id = ?"
        params: list[object] = [self.company_id()]
        if not include_inactive:
            sql += " AND active = 1"
        sql += " ORDER BY active DESC, priority DESC, start_date, name COLLATE NOCASE"
        return self.connection.execute(sql, params).fetchall()

    def save_season(self, season_id: int | None, name: str, start_date: str, end_date: str, priority: int, active: bool = True) -> int:
        if not name.strip():
            raise ValueError("Season name is required")
        if end_date < start_date:
            raise ValueError("Season end date cannot be before its start date")
        if season_id is None:
            cursor = self.connection.execute(
                "INSERT INTO seasons(company_id, name, start_date, end_date, priority, active) VALUES (?, ?, ?, ?, ?, ?)",
                (self.company_id(), name.strip(), start_date, end_date, int(priority), int(active)),
            )
            season_id = int(cursor.lastrowid)
        else:
            self.connection.execute(
                "UPDATE seasons SET name = ?, start_date = ?, end_date = ?, priority = ?, active = ? WHERE id = ? AND company_id = ?",
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

    def list_discount_rules(self, include_inactive: bool = True) -> list[sqlite3.Row]:
        sql = """
            SELECT discount_rules.*, elements.name AS element_name
            FROM discount_rules
            LEFT JOIN elements ON elements.id = discount_rules.element_id
            WHERE discount_rules.company_id = ?
        """
        params: list[object] = [self.company_id()]
        if not include_inactive:
            sql += " AND discount_rules.active = 1"
        sql += " ORDER BY discount_rules.active DESC, discount_rules.min_nights, discount_rules.name COLLATE NOCASE"
        return self.connection.execute(sql, params).fetchall()

    def save_discount_rule(
        self,
        rule_id: int | None,
        name: str,
        min_nights: int,
        discount_type: str,
        discount_value: float,
        scope_type: str,
        element_id: int | None = None,
        group_name: str = "",
        active: bool = True,
    ) -> int:
        name = name.strip()
        group_name = group_name.strip()
        if not name:
            raise ValueError("Discount rule name is required")
        if int(min_nights) < 1:
            raise ValueError("Minimum stay must be at least one night")
        if discount_type not in DISCOUNT_TYPES:
            raise ValueError("Unknown discount type")
        if scope_type not in DISCOUNT_SCOPES:
            raise ValueError("Unknown discount scope")
        if float(discount_value) <= 0:
            raise ValueError("Discount value must be greater than zero")
        if discount_type == "Percentage" and float(discount_value) > 100:
            raise ValueError("Percentage discount cannot exceed 100%")
        if discount_type == "Free nights":
            if not float(discount_value).is_integer():
                raise ValueError("Free nights must be a whole number")
            if int(discount_value) >= int(min_nights):
                raise ValueError("Free nights must be fewer than the minimum qualifying stay")

        if scope_type == "Element":
            if element_id is None:
                raise ValueError("Select an element for this rule")
            element = self.connection.execute(
                "SELECT id FROM elements WHERE id = ? AND company_id = ?",
                (element_id, self.company_id()),
            ).fetchone()
            if element is None:
                raise ValueError("Selected element does not exist")
            group_name = ""
        elif scope_type == "Group":
            if not group_name:
                raise ValueError("Select a group for this rule")
            element_id = None
        else:
            element_id = None
            group_name = ""

        values = (name, int(min_nights), discount_type, float(discount_value), scope_type, element_id, group_name, int(active))
        if rule_id is None:
            cursor = self.connection.execute(
                """
                INSERT INTO discount_rules(company_id, name, min_nights, discount_type, discount_value, scope_type, element_id, group_name, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (self.company_id(), *values),
            )
            rule_id = int(cursor.lastrowid)
        else:
            self.connection.execute(
                """
                UPDATE discount_rules
                SET name = ?, min_nights = ?, discount_type = ?, discount_value = ?, scope_type = ?, element_id = ?, group_name = ?, active = ?
                WHERE id = ? AND company_id = ?
                """,
                (*values, rule_id, self.company_id()),
            )
        self.connection.commit()
        return rule_id

    def set_discount_rule_active(self, rule_id: int, active: bool) -> None:
        self.connection.execute(
            "UPDATE discount_rules SET active = ? WHERE id = ? AND company_id = ?",
            (int(active), rule_id, self.company_id()),
        )
        self.connection.commit()

    def calculate_duration_discount(self, element_id: int, nights: int, base_amount: float) -> dict[str, object]:
        if base_amount < 0:
            raise ValueError("Base amount cannot be negative")
        if nights < 1:
            return {"base_amount": float(base_amount), "discount_amount": 0.0, "final_amount": float(base_amount), "rule_id": None, "rule_name": ""}

        element = self.connection.execute(
            "SELECT * FROM elements WHERE id = ? AND company_id = ?",
            (element_id, self.company_id()),
        ).fetchone()
        if element is None:
            raise ValueError("Element does not exist")

        rules = self.connection.execute(
            """
            SELECT * FROM discount_rules
            WHERE company_id = ? AND active = 1 AND min_nights <= ?
              AND (
                    scope_type = 'All elements'
                    OR (scope_type = 'Element' AND element_id = ?)
                    OR (scope_type = 'Group' AND group_name = ?)
              )
            """,
            (self.company_id(), int(nights), element_id, element["group_name"]),
        ).fetchall()

        amount = float(base_amount)
        best_rule = None
        best_discount = 0.0
        for rule in rules:
            value = float(rule["discount_value"])
            if rule["discount_type"] == "Percentage":
                discount = amount * value / 100.0
            elif rule["discount_type"] == "Fixed amount":
                discount = value
            else:
                if "night" not in element["pricing_type"].lower():
                    continue
                discount = amount * min(value, float(nights)) / float(nights)
            discount = max(0.0, min(amount, discount))
            if discount > best_discount:
                best_discount = discount
                best_rule = rule

        return {
            "base_amount": round(amount, 2),
            "discount_amount": round(best_discount, 2),
            "final_amount": round(amount - best_discount, 2),
            "rule_id": int(best_rule["id"]) if best_rule else None,
            "rule_name": best_rule["name"] if best_rule else "",
        }

    def list_person_types(self, include_inactive: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM person_types WHERE company_id = ?"
        params: list[object] = [self.company_id()]
        if not include_inactive:
            sql += " AND active = 1"
        sql += " ORDER BY active DESC, name COLLATE NOCASE"
        return self.connection.execute(sql, params).fetchall()

    def save_person_type(self, person_type_id: int | None, name: str, short_label: str, active: bool = True) -> int:
        name = name.strip()
        short_label = short_label.strip()
        if not name:
            raise ValueError("Person type name is required")
        if not short_label:
            raise ValueError("Short label is required")
        if len(short_label) > 12:
            raise ValueError("Short label must be 12 characters or fewer")
        try:
            if person_type_id is None:
                cursor = self.connection.execute(
                    "INSERT INTO person_types(company_id, name, short_label, active) VALUES (?, ?, ?, ?)",
                    (self.company_id(), name, short_label, int(active)),
                )
                person_type_id = int(cursor.lastrowid)
            else:
                self.connection.execute(
                    "UPDATE person_types SET name = ?, short_label = ?, active = ? WHERE id = ? AND company_id = ?",
                    (name, short_label, int(active), person_type_id, self.company_id()),
                )
            self.connection.commit()
        except sqlite3.IntegrityError as exc:
            self.connection.rollback()
            raise ValueError("Person type name and short label must each be unique") from exc
        return person_type_id

    def set_person_type_active(self, person_type_id: int, active: bool) -> None:
        self.connection.execute(
            "UPDATE person_types SET active = ? WHERE id = ? AND company_id = ?",
            (int(active), person_type_id, self.company_id()),
        )
        self.connection.commit()

    def get_element_capacity(self, element_id: int) -> dict[str, object]:
        element = self.connection.execute(
            "SELECT id FROM elements WHERE id = ? AND company_id = ?",
            (element_id, self.company_id()),
        ).fetchone()
        if element is None:
            raise ValueError("Element does not exist")
        capacity = self.connection.execute(
            "SELECT max_total FROM element_capacity WHERE element_id = ? AND company_id = ?",
            (element_id, self.company_id()),
        ).fetchone()
        limit_rows = self.connection.execute(
            """
            SELECT epl.person_type_id, epl.max_count, pt.name, pt.short_label, pt.active
            FROM element_person_limits epl
            JOIN person_types pt ON pt.id = epl.person_type_id
            WHERE epl.element_id = ? AND epl.company_id = ?
            ORDER BY pt.name COLLATE NOCASE
            """,
            (element_id, self.company_id()),
        ).fetchall()
        return {
            "max_total": int(capacity["max_total"]) if capacity else 0,
            "limits": {int(row["person_type_id"]): int(row["max_count"]) for row in limit_rows},
            "limit_rows": limit_rows,
        }

    def save_element_capacity(self, element_id: int, max_total: int, limits: dict[int, int | None]) -> None:
        element = self.connection.execute(
            "SELECT id FROM elements WHERE id = ? AND company_id = ?",
            (element_id, self.company_id()),
        ).fetchone()
        if element is None:
            raise ValueError("Element does not exist")
        if int(max_total) < 0:
            raise ValueError("Maximum total persons cannot be negative")
        self.connection.execute(
            """
            INSERT INTO element_capacity(element_id, company_id, max_total)
            VALUES (?, ?, ?)
            ON CONFLICT(element_id) DO UPDATE SET max_total = excluded.max_total, company_id = excluded.company_id
            """,
            (element_id, self.company_id(), int(max_total)),
        )
        self.connection.execute(
            "DELETE FROM element_person_limits WHERE element_id = ? AND company_id = ?",
            (element_id, self.company_id()),
        )
        for person_type_id, max_count in limits.items():
            if max_count is None:
                continue
            max_count = int(max_count)
            if max_count < 0:
                continue
            person_type = self.connection.execute(
                "SELECT id FROM person_types WHERE id = ? AND company_id = ?",
                (int(person_type_id), self.company_id()),
            ).fetchone()
            if person_type is None:
                raise ValueError("A selected person type no longer exists")
            self.connection.execute(
                "INSERT INTO element_person_limits(element_id, person_type_id, company_id, max_count) VALUES (?, ?, ?, ?)",
                (element_id, int(person_type_id), self.company_id(), max_count),
            )
        self.connection.commit()

    def validate_occupancy(self, element_id: int, person_counts: dict[int, int]) -> list[str]:
        capacity = self.get_element_capacity(element_id)
        errors: list[str] = []
        total = 0
        known_types = {int(row["id"]): row for row in self.list_person_types(True)}
        for person_type_id, count in person_counts.items():
            count = int(count)
            if count < 0:
                raise ValueError("Person counts cannot be negative")
            if count == 0:
                continue
            person_type_id = int(person_type_id)
            if person_type_id not in known_types:
                raise ValueError("Unknown person type")
            total += count
            limit = capacity["limits"].get(person_type_id)
            if limit is not None and count > int(limit):
                label = known_types[person_type_id]["name"]
                errors.append(f"{label}: maximum {limit}")
        max_total = int(capacity["max_total"])
        if max_total > 0 and total > max_total:
            errors.append(f"Total persons: maximum {max_total}")
        return errors

    def counts(self) -> dict[str, int]:
        return {
            "enquiries": self.connection.execute("SELECT COUNT(*) FROM enquiries").fetchone()[0],
            "bookings": self.connection.execute("SELECT COUNT(*) FROM bookings").fetchone()[0],
            "elements": self.connection.execute("SELECT COUNT(*) FROM elements WHERE active = 1").fetchone()[0],
            "transactions": self.connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0],
        }

    def close(self) -> None:
        self.connection.close()
