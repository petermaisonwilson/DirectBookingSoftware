from __future__ import annotations

from .database import Database


ADDON_PRICING_METHODS = [
    "Fixed once",
    "Per quantity",
    "Per night",
    "Per quantity per night",
    "Per day",
    "Per quantity per day",
]

ADDON_SCHEMA = """
CREATE TABLE IF NOT EXISTS add_ons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    pricing_method TEXT NOT NULL DEFAULT 'Fixed once',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company_id, name),
    FOREIGN KEY(company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS annual_element_addons (
    company_id INTEGER NOT NULL,
    year INTEGER NOT NULL,
    element_id INTEGER NOT NULL,
    addon_id INTEGER NOT NULL,
    allowed INTEGER NOT NULL,
    min_qty INTEGER,
    max_qty INTEGER,
    rate REAL,
    PRIMARY KEY(company_id, year, element_id, addon_id),
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(element_id) REFERENCES elements(id),
    FOREIGN KEY(addon_id) REFERENCES add_ons(id)
);
"""


def ensure_addon_schema(database: Database) -> None:
    database.connection.executescript(ADDON_SCHEMA)
    database.connection.commit()


def list_addons(database: Database, include_inactive: bool = True):
    ensure_addon_schema(database)
    sql = "SELECT * FROM add_ons WHERE company_id=?"
    params: list[object] = [database.company_id()]
    if not include_inactive:
        sql += " AND active=1"
    sql += " ORDER BY active DESC, name COLLATE NOCASE"
    return database.connection.execute(sql, params).fetchall()


def save_addon(database: Database, addon_id: int | None, name: str, pricing_method: str, active: bool = True) -> int:
    ensure_addon_schema(database)
    name = name.strip()
    if not name:
        raise ValueError("Add-on name is required")
    if pricing_method not in ADDON_PRICING_METHODS:
        raise ValueError("Unknown Add-on pricing method")
    duplicate = database.connection.execute(
        "SELECT id FROM add_ons WHERE company_id=? AND name=? COLLATE NOCASE AND (? IS NULL OR id<>?)",
        (database.company_id(), name, addon_id, addon_id),
    ).fetchone()
    if duplicate:
        raise ValueError(f"An Add-on named '{name}' already exists")
    if addon_id is None:
        cursor = database.connection.execute(
            "INSERT INTO add_ons(company_id,name,pricing_method,active) VALUES (?,?,?,?)",
            (database.company_id(), name, pricing_method, int(active)),
        )
        addon_id = int(cursor.lastrowid)
    else:
        database.connection.execute(
            "UPDATE add_ons SET name=?, pricing_method=?, active=? WHERE company_id=? AND id=?",
            (name, pricing_method, int(active), database.company_id(), int(addon_id)),
        )
    database.connection.commit()
    return int(addon_id)


def set_addon_active(database: Database, addon_id: int, active: bool) -> None:
    ensure_addon_schema(database)
    database.connection.execute(
        "UPDATE add_ons SET active=? WHERE company_id=? AND id=?",
        (int(active), database.company_id(), int(addon_id)),
    )
    database.connection.commit()


def delete_addon(database: Database, addon_id: int) -> None:
    ensure_addon_schema(database)
    row = database.connection.execute(
        "SELECT name FROM add_ons WHERE company_id=? AND id=?",
        (database.company_id(), int(addon_id)),
    ).fetchone()
    if row is None:
        raise ValueError("Add-on no longer exists")
    used = database.connection.execute(
        "SELECT COUNT(*) FROM annual_element_addons WHERE company_id=? AND addon_id=?",
        (database.company_id(), int(addon_id)),
    ).fetchone()[0]
    if int(used):
        raise ValueError(
            f"{row['name']} cannot be deleted because annual Element/Add-on rules already reference it. "
            "Make it inactive instead so historical setup remains intact."
        )
    database.connection.execute(
        "DELETE FROM add_ons WHERE company_id=? AND id=?",
        (database.company_id(), int(addon_id)),
    )
    database.connection.commit()


def get_addon_rule(database: Database, year: int, element_id: int, addon_id: int):
    ensure_addon_schema(database)
    return database.connection.execute(
        "SELECT * FROM annual_element_addons WHERE company_id=? AND year=? AND element_id=? AND addon_id=?",
        (database.company_id(), int(year), int(element_id), int(addon_id)),
    ).fetchone()


def save_addon_rule(
    database: Database,
    year: int,
    element_id: int,
    addon_id: int,
    allowed: bool,
    min_qty: int | None = None,
    max_qty: int | None = None,
    rate: float | None = None,
) -> None:
    ensure_addon_schema(database)
    if allowed:
        if min_qty is None or max_qty is None or rate is None:
            raise ValueError("Allowed Add-ons require minimum quantity, maximum quantity and price")
        if int(min_qty) < 0:
            raise ValueError("Minimum Add-on quantity cannot be negative")
        if int(max_qty) < int(min_qty):
            raise ValueError("Maximum Add-on quantity cannot be less than minimum quantity")
        if float(rate) < 0:
            raise ValueError("Add-on price cannot be negative")
    else:
        min_qty = None
        max_qty = None
        rate = None
    database.connection.execute(
        """
        INSERT INTO annual_element_addons(company_id,year,element_id,addon_id,allowed,min_qty,max_qty,rate)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(company_id,year,element_id,addon_id)
        DO UPDATE SET allowed=excluded.allowed,min_qty=excluded.min_qty,max_qty=excluded.max_qty,rate=excluded.rate
        """,
        (
            database.company_id(), int(year), int(element_id), int(addon_id), int(bool(allowed)),
            min_qty, max_qty, rate,
        ),
    )


def validate_addon_year(database: Database, year: int) -> dict[str, int]:
    ensure_addon_schema(database)
    elements = database.list_elements(False)
    addons = list_addons(database, False)
    unreviewed = 0
    incomplete_allowed = 0
    for element in elements:
        for addon in addons:
            row = get_addon_rule(database, year, int(element["id"]), int(addon["id"]))
            if row is None:
                unreviewed += 1
                continue
            if bool(row["allowed"]):
                if row["min_qty"] is None or row["max_qty"] is None or row["rate"] is None:
                    incomplete_allowed += 1
                elif int(row["min_qty"]) < 0 or int(row["max_qty"]) < int(row["min_qty"]) or float(row["rate"]) < 0:
                    incomplete_allowed += 1
    return {"unreviewed": unreviewed, "incomplete": incomplete_allowed}


def copy_addon_year(database: Database, source_year: int, target_year: int) -> None:
    ensure_addon_schema(database)
    database.connection.execute(
        """
        INSERT INTO annual_element_addons(company_id,year,element_id,addon_id,allowed,min_qty,max_qty,rate)
        SELECT company_id, ?, element_id, addon_id, allowed, min_qty, max_qty, rate
        FROM annual_element_addons
        WHERE company_id=? AND year=?
        """,
        (int(target_year), database.company_id(), int(source_year)),
    )


def delete_addon_year(database: Database, year: int) -> None:
    ensure_addon_schema(database)
    database.connection.execute(
        "DELETE FROM annual_element_addons WHERE company_id=? AND year=?",
        (database.company_id(), int(year)),
    )


def addon_amount(pricing_method: str, rate: float, quantity: int, nights: int, days: int) -> float:
    q = max(0, int(quantity))
    n = max(0, int(nights))
    d = max(0, int(days))
    r = float(rate)
    if pricing_method == "Fixed once":
        return r
    if pricing_method == "Per quantity":
        return r * q
    if pricing_method == "Per night":
        return r * n
    if pricing_method == "Per quantity per night":
        return r * q * n
    if pricing_method == "Per day":
        return r * d
    if pricing_method == "Per quantity per day":
        return r * q * d
    raise ValueError(f"Unknown Add-on pricing method: {pricing_method}")
