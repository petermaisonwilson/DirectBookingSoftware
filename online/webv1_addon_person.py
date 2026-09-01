from __future__ import annotations

from .setup015_core import one, rows


ADDON_PERSON_SCHEMA = """
CREATE TABLE IF NOT EXISTS setup_addon_person_pricing (
    company_id INTEGER NOT NULL,
    addon_id INTEGER NOT NULL,
    pricing_mode TEXT NOT NULL DEFAULT 'single' CHECK(pricing_mode IN ('single','person_type')),
    PRIMARY KEY(company_id, addon_id)
);

CREATE TABLE IF NOT EXISTS setup_addon_person_rates (
    company_id INTEGER NOT NULL,
    addon_id INTEGER NOT NULL,
    year INTEGER NOT NULL,
    person_type_id INTEGER NOT NULL,
    rate REAL NOT NULL CHECK(rate >= 0),
    PRIMARY KEY(company_id, addon_id, year, person_type_id)
);

CREATE TABLE IF NOT EXISTS enquiry_addon_people (
    enquiry_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    addon_id INTEGER NOT NULL,
    person_type_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
    PRIMARY KEY(enquiry_id, addon_id, person_type_id)
);

CREATE TABLE IF NOT EXISTS enquiry_addon_person_days (
    enquiry_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    addon_id INTEGER NOT NULL,
    person_type_id INTEGER NOT NULL,
    service_date TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity >= 0),
    PRIMARY KEY(enquiry_id, addon_id, person_type_id, service_date)
);
"""


def initialise_addon_person(database) -> None:
    with database.connect() as connection:
        connection.executescript(ADDON_PERSON_SCHEMA)
        connection.execute(
            """INSERT OR IGNORE INTO setup_addon_person_pricing(company_id,addon_id,pricing_mode)
               SELECT company_id,id,'single' FROM setup_addons"""
        )


def addon_person_mode(database, company_id: int, addon_id: int) -> str:
    row = one(
        database,
        'SELECT pricing_mode FROM setup_addon_person_pricing WHERE company_id=? AND addon_id=?',
        (company_id, addon_id),
    )
    return str(row['pricing_mode']) if row else 'single'


def addon_person_rates(database, company_id: int, addon_id: int, year: int) -> dict[int, float]:
    return {
        int(row['person_type_id']): float(row['rate'])
        for row in rows(
            database,
            'SELECT person_type_id,rate FROM setup_addon_person_rates WHERE company_id=? AND addon_id=? AND year=?',
            (company_id, addon_id, year),
        )
    }


def addon_person_payload(database, company_id: int, addons, years) -> tuple[dict[str, str], dict[str, dict[str, dict[str, float]]]]:
    modes: dict[str, str] = {}
    rates: dict[str, dict[str, dict[str, float]]] = {}
    for addon in addons:
        aid = int(addon['id'])
        akey = str(aid)
        modes[akey] = addon_person_mode(database, company_id, aid)
        rates[akey] = {}
        for year_row in years:
            year = int(year_row['year'])
            rates[akey][str(year)] = {
                str(pid): rate for pid, rate in addon_person_rates(database, company_id, aid, year).items()
            }
    return modes, rates
