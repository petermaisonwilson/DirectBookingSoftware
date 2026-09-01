from __future__ import annotations

from fastapi import HTTPException, Request

from .app import COOKIE_NAME
from .setup014_core import (
    ADDON_PRICING_METHODS,
    ELEMENT_PRICING_METHODS,
    audit,
    copy_previous_year as copy_previous_year014,
    one,
    rows,
    selected_year,
    valid_money,
    valid_whole,
    working_company,
    years,
)

BUILD015_SCHEMA = """
CREATE TABLE IF NOT EXISTS setup_element_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(company_id, name COLLATE NOCASE)
);
CREATE TABLE IF NOT EXISTS setup_person_prices (
    company_id INTEGER NOT NULL,
    year INTEGER NOT NULL,
    element_id INTEGER NOT NULL,
    person_type_id INTEGER NOT NULL,
    rate REAL NOT NULL,
    PRIMARY KEY(company_id, year, element_id, person_type_id)
);
"""


def initialise_setup015(database) -> None:
    from .setup014_core import initialise_setup014
    initialise_setup014(database)
    with database.connect() as connection:
        connection.executescript(BUILD015_SCHEMA)
        columns = {str(r['name']) for r in connection.execute('PRAGMA table_info(setup_person_limits)').fetchall()}
        if 'min_count' not in columns:
            connection.execute('ALTER TABLE setup_person_limits ADD COLUMN min_count INTEGER NOT NULL DEFAULT 0')
        connection.execute(
            """
            INSERT OR IGNORE INTO setup_element_types(company_id, name, active)
            SELECT DISTINCT company_id, TRIM(element_type), 1
            FROM setup_elements
            WHERE TRIM(element_type) <> ''
            """
        )


def copy_previous_year(database, company_id: int, target_year: int) -> int:
    source_year = copy_previous_year014(database, company_id, target_year)
    with database.connect() as connection:
        prices = connection.execute(
            "SELECT element_id,person_type_id,rate FROM setup_person_prices WHERE company_id=? AND year=?",
            (company_id, source_year),
        ).fetchall()
        for row in prices:
            connection.execute(
                "INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)",
                (company_id, target_year, row["element_id"], row["person_type_id"], row["rate"]),
            )
        minima = connection.execute(
            "SELECT element_id,person_type_id,min_count FROM setup_person_limits WHERE company_id=? AND year=?",
            (company_id, source_year),
        ).fetchall()
        for row in minima:
            connection.execute(
                "UPDATE setup_person_limits SET min_count=? WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?",
                (row["min_count"], company_id, target_year, row["element_id"], row["person_type_id"]),
            )
    return source_year


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