from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import HTTPException, Request

from .app import COOKIE_NAME

ELEMENT_PRICING_METHODS = (
    "Per night", "Per day", "Per stay", "Per person", "Per person per night", "Per package"
)
ADDON_PRICING_METHODS = (
    "Fixed once", "Per quantity", "Per night", "Per quantity per night", "Per day", "Per quantity per day"
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS setup_elements (
 id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL, name TEXT NOT NULL,
 element_type TEXT NOT NULL, pricing_method TEXT NOT NULL, base_price REAL NOT NULL DEFAULT 0,
 active INTEGER NOT NULL DEFAULT 1, UNIQUE(company_id,name COLLATE NOCASE)
);
CREATE TABLE IF NOT EXISTS setup_person_types (
 id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL, name TEXT NOT NULL,
 short_name TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
 UNIQUE(company_id,name COLLATE NOCASE)
);
CREATE TABLE IF NOT EXISTS setup_addons (
 id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL, name TEXT NOT NULL,
 pricing_method TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 UNIQUE(company_id,name COLLATE NOCASE)
);
CREATE TABLE IF NOT EXISTS setup_years (
 company_id INTEGER NOT NULL, year INTEGER NOT NULL, copied_from_year INTEGER,
 PRIMARY KEY(company_id,year)
);
CREATE TABLE IF NOT EXISTS setup_seasons (
 id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL, year INTEGER NOT NULL,
 name TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
 UNIQUE(company_id,year,name COLLATE NOCASE)
);
CREATE TABLE IF NOT EXISTS setup_element_rates (
 company_id INTEGER NOT NULL, year INTEGER NOT NULL, element_id INTEGER NOT NULL, season_id INTEGER NOT NULL,
 rate REAL NOT NULL, PRIMARY KEY(company_id,year,element_id,season_id)
);
CREATE TABLE IF NOT EXISTS setup_occupancy (
 company_id INTEGER NOT NULL, year INTEGER NOT NULL, element_id INTEGER NOT NULL, max_total INTEGER NOT NULL,
 PRIMARY KEY(company_id,year,element_id)
);
CREATE TABLE IF NOT EXISTS setup_person_limits (
 company_id INTEGER NOT NULL, year INTEGER NOT NULL, element_id INTEGER NOT NULL, person_type_id INTEGER NOT NULL,
 max_count INTEGER NOT NULL, PRIMARY KEY(company_id,year,element_id,person_type_id)
);
CREATE TABLE IF NOT EXISTS setup_type_addons (
 company_id INTEGER NOT NULL, year INTEGER NOT NULL, element_type TEXT NOT NULL, addon_id INTEGER NOT NULL,
 allowed INTEGER NOT NULL, min_qty INTEGER, max_qty INTEGER, rate REAL,
 PRIMARY KEY(company_id,year,element_type,addon_id)
);
CREATE TABLE IF NOT EXISTS setup_element_addons (
 company_id INTEGER NOT NULL, year INTEGER NOT NULL, element_id INTEGER NOT NULL, addon_id INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('Y','N')), min_qty INTEGER, max_qty INTEGER, rate REAL,
 PRIMARY KEY(company_id,year,element_id,addon_id)
);
"""


def initialise_setup014(database) -> None:
    with database.connect() as connection:
        connection.executescript(SCHEMA)


def working_company(context) -> int | None:
    if context["role"] == "supervisor":
        return context["acting_company_id"]
    if context["role"] == "operator":
        return context["company_id"]
    return None


def context_for(database, request: Request):
    context = database.session_context(request.cookies.get(COOKIE_NAME))
    if context is None:
        raise HTTPException(status_code=401, detail="Login required")
    if context["role"] not in {"supervisor", "operator"}:
        raise HTTPException(status_code=403, detail="Setup is not available to customers")
    if not working_company(context):
        raise HTTPException(status_code=403, detail="Select a client in Support Mode first")
    return context


def require_csrf(context, data: dict[str, str]) -> None:
    if data.get("csrf") != context["csrf_token"]:
        raise HTTPException(status_code=403, detail="Invalid form token")


def audit(database, context, company_id: int, action: str, entity_type: str, entity_id: Any, before=None, after=None):
    database.write_audit(
        action=action, entity_type=entity_type, entity_id=entity_id,
        actor_user_id=context["user_id"], actor_role=context["role"], company_id=company_id,
        acting_company_id=context["acting_company_id"], before=before, after=after,
    )


def rows(database, sql: str, params=()):
    with database.connect() as c:
        return c.execute(sql, params).fetchall()


def one(database, sql: str, params=()):
    with database.connect() as c:
        return c.execute(sql, params).fetchone()


def years(database, company_id: int) -> list[int]:
    return [int(r["year"]) for r in rows(database, "SELECT year FROM setup_years WHERE company_id=? ORDER BY year", (company_id,))]


def selected_year(database, company_id: int, raw: str | int | None) -> int | None:
    available = years(database, company_id)
    try:
        value = int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        value = None
    return value if value in available else (available[-1] if available else None)


def valid_whole(value: str) -> int:
    number = int(value)
    if number < 0:
        raise ValueError("Values cannot be negative")
    return number


def valid_money(value: str) -> float:
    number = float(value.replace(",", "."))
    if number < 0:
        raise ValueError("Prices cannot be negative")
    return round(number, 2)


def shift_date(value: str, target_year: int) -> str:
    old = date.fromisoformat(value)
    day = old.day
    while day > 27:
        try:
            return old.replace(year=target_year, day=day).isoformat()
        except ValueError:
            day -= 1
    return old.replace(year=target_year, day=day).isoformat()


def copy_previous_year(database, company_id: int, target_year: int) -> int:
    available = [y for y in years(database, company_id) if y < target_year]
    if not available:
        raise ValueError("There is no previous year to copy")
    source = max(available)
    if target_year in years(database, company_id):
        raise ValueError("That year already exists")
    with database.connect() as c:
        c.execute("INSERT INTO setup_years(company_id,year,copied_from_year) VALUES (?,?,?)", (company_id,target_year,source))
        season_map: dict[int,int] = {}
        for row in c.execute("SELECT * FROM setup_seasons WHERE company_id=? AND year=? ORDER BY id", (company_id,source)):
            new_id = c.execute(
                "INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)",
                (company_id,target_year,row["name"],shift_date(row["start_date"],target_year),shift_date(row["end_date"],target_year)),
            ).lastrowid
            season_map[int(row["id"])] = int(new_id)
        for row in c.execute("SELECT * FROM setup_element_rates WHERE company_id=? AND year=?", (company_id,source)):
            if int(row["season_id"]) in season_map:
                c.execute("INSERT INTO setup_element_rates VALUES (?,?,?,?,?)", (company_id,target_year,row["element_id"],season_map[int(row["season_id"])],row["rate"]))
        for table, cols in (
            ("setup_occupancy", "element_id,max_total"),
            ("setup_person_limits", "element_id,person_type_id,max_count"),
            ("setup_type_addons", "element_type,addon_id,allowed,min_qty,max_qty,rate"),
            ("setup_element_addons", "element_id,addon_id,state,min_qty,max_qty,rate"),
        ):
            source_rows = c.execute(f"SELECT {cols} FROM {table} WHERE company_id=? AND year=?", (company_id,source)).fetchall()
            names = cols.split(",")
            placeholders = ",".join("?" for _ in range(2 + len(names)))
            for row in source_rows:
                c.execute(f"INSERT INTO {table}(company_id,year,{cols}) VALUES ({placeholders})", (company_id,target_year,*[row[n] for n in names]))
    return source
