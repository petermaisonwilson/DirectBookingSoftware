from __future__ import annotations

from fastapi import HTTPException, Request

from .app import COOKIE_NAME
from .setup014_core import (
    ADDON_PRICING_METHODS,
    ELEMENT_PRICING_METHODS,
    audit,
    copy_previous_year,
    one,
    rows,
    selected_year,
    valid_money,
    valid_whole,
    working_company,
    years,
)

ELEMENT_TYPES_SCHEMA = """
CREATE TABLE IF NOT EXISTS setup_element_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(company_id, name COLLATE NOCASE)
);
"""


def initialise_setup015(database) -> None:
    from .setup014_core import initialise_setup014
    initialise_setup014(database)
    with database.connect() as connection:
        connection.executescript(ELEMENT_TYPES_SCHEMA)
        connection.execute(
            """
            INSERT OR IGNORE INTO setup_element_types(company_id, name, active)
            SELECT DISTINCT company_id, TRIM(element_type), 1
            FROM setup_elements
            WHERE TRIM(element_type) <> ''
            """
        )


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
