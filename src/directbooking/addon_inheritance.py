from __future__ import annotations

from .addon_model import ensure_addon_schema, list_addons
from .database import Database

GROUP_ADDON_SCHEMA = """
CREATE TABLE IF NOT EXISTS annual_group_addons (
    company_id INTEGER NOT NULL,
    year INTEGER NOT NULL,
    group_name TEXT NOT NULL COLLATE NOCASE,
    addon_id INTEGER NOT NULL,
    allowed INTEGER NOT NULL,
    min_qty INTEGER,
    max_qty INTEGER,
    rate REAL,
    PRIMARY KEY(company_id, year, group_name, addon_id),
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(addon_id) REFERENCES add_ons(id)
);
"""


def ensure_group_addon_schema(database: Database) -> None:
    ensure_addon_schema(database)
    database.connection.executescript(GROUP_ADDON_SCHEMA)
    database.connection.commit()


def list_element_types(database: Database) -> list[str]:
    ensure_group_addon_schema(database)
    rows = database.connection.execute(
        """
        SELECT DISTINCT TRIM(group_name) AS group_name
        FROM elements
        WHERE company_id=? AND active=1 AND TRIM(group_name)<>''
        ORDER BY group_name COLLATE NOCASE
        """,
        (database.company_id(),),
    ).fetchall()
    return [str(row["group_name"]) for row in rows]


def get_group_addon_rule(database: Database, year: int, group_name: str, addon_id: int):
    ensure_group_addon_schema(database)
    return database.connection.execute(
        """
        SELECT * FROM annual_group_addons
        WHERE company_id=? AND year=? AND group_name=? COLLATE NOCASE AND addon_id=?
        """,
        (database.company_id(), int(year), group_name.strip(), int(addon_id)),
    ).fetchone()


def save_group_addon_rule(
    database: Database,
    year: int,
    group_name: str,
    addon_id: int,
    allowed: bool,
    min_qty: int | None = None,
    max_qty: int | None = None,
    rate: float | None = None,
) -> None:
    ensure_group_addon_schema(database)
    group_name = group_name.strip()
    if not group_name:
        raise ValueError("Element Type is required")
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
        INSERT INTO annual_group_addons(company_id,year,group_name,addon_id,allowed,min_qty,max_qty,rate)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(company_id,year,group_name,addon_id)
        DO UPDATE SET allowed=excluded.allowed,min_qty=excluded.min_qty,max_qty=excluded.max_qty,rate=excluded.rate
        """,
        (
            database.company_id(), int(year), group_name, int(addon_id), int(bool(allowed)),
            min_qty, max_qty, rate,
        ),
    )


def delete_group_addon_rule(database: Database, year: int, group_name: str, addon_id: int) -> None:
    ensure_group_addon_schema(database)
    database.connection.execute(
        "DELETE FROM annual_group_addons WHERE company_id=? AND year=? AND group_name=? COLLATE NOCASE AND addon_id=?",
        (database.company_id(), int(year), group_name.strip(), int(addon_id)),
    )


def delete_element_override(database: Database, year: int, element_id: int, addon_id: int) -> None:
    ensure_group_addon_schema(database)
    database.connection.execute(
        "DELETE FROM annual_element_addons WHERE company_id=? AND year=? AND element_id=? AND addon_id=?",
        (database.company_id(), int(year), int(element_id), int(addon_id)),
    )


def resolve_addon_rule(database: Database, year: int, element_id: int, addon_id: int) -> dict:
    """Resolve Element override first, then Element Type default, otherwise unavailable/unconfigured."""
    ensure_group_addon_schema(database)
    element = database.connection.execute(
        "SELECT id,name,group_name FROM elements WHERE company_id=? AND id=?",
        (database.company_id(), int(element_id)),
    ).fetchone()
    if element is None:
        raise ValueError("Element no longer exists")
    override = database.connection.execute(
        "SELECT * FROM annual_element_addons WHERE company_id=? AND year=? AND element_id=? AND addon_id=?",
        (database.company_id(), int(year), int(element_id), int(addon_id)),
    ).fetchone()
    if override is not None:
        return {
            "source": "Element override",
            "configured": True,
            "allowed": bool(override["allowed"]),
            "min_qty": override["min_qty"],
            "max_qty": override["max_qty"],
            "rate": override["rate"],
        }
    group_name = str(element["group_name"] or "").strip()
    if group_name:
        group_rule = get_group_addon_rule(database, year, group_name, addon_id)
        if group_rule is not None:
            return {
                "source": f"Element Type: {group_name}",
                "configured": True,
                "allowed": bool(group_rule["allowed"]),
                "min_qty": group_rule["min_qty"],
                "max_qty": group_rule["max_qty"],
                "rate": group_rule["rate"],
            }
    return {
        "source": "No rule",
        "configured": False,
        "allowed": False,
        "min_qty": None,
        "max_qty": None,
        "rate": None,
    }


def validate_group_addon_year(database: Database, year: int) -> dict[str, int]:
    ensure_group_addon_schema(database)
    groups = list_element_types(database)
    addons = list_addons(database, False)
    missing = 0
    incomplete = 0
    for group_name in groups:
        for addon in addons:
            row = get_group_addon_rule(database, year, group_name, int(addon["id"]))
            if row is None:
                missing += 1
                continue
            if bool(row["allowed"]):
                if row["min_qty"] is None or row["max_qty"] is None or row["rate"] is None:
                    incomplete += 1
                elif int(row["min_qty"]) < 0 or int(row["max_qty"]) < int(row["min_qty"]) or float(row["rate"]) < 0:
                    incomplete += 1
    return {"unreviewed": missing, "incomplete": incomplete}


def copy_group_addon_year(database: Database, source_year: int, target_year: int) -> None:
    ensure_group_addon_schema(database)
    database.connection.execute(
        """
        INSERT INTO annual_group_addons(company_id,year,group_name,addon_id,allowed,min_qty,max_qty,rate)
        SELECT company_id, ?, group_name, addon_id, allowed, min_qty, max_qty, rate
        FROM annual_group_addons
        WHERE company_id=? AND year=?
        """,
        (int(target_year), database.company_id(), int(source_year)),
    )


def delete_group_addon_year(database: Database, year: int) -> None:
    ensure_group_addon_schema(database)
    database.connection.execute(
        "DELETE FROM annual_group_addons WHERE company_id=? AND year=?",
        (database.company_id(), int(year)),
    )
