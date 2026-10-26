from __future__ import annotations
from .sql import execute_sql, table_exists

from datetime import date, timedelta


def _table_exists(connection, table: str) -> bool:
    return (True if table_exists(connection, table) else None) is not None


def _season_for_date(connection, company_id: int, year: int, day: date):
    rows = execute_sql(connection,
        'SELECT * FROM setup_seasons\n           WHERE company_id=:q629_0 AND year=:q629_1 AND start_date<=:q629_2 AND end_date>=:q629_3',
        (company_id, year, day.isoformat(), day.isoformat()),
    ).fetchall()
    if not rows:
        return None
    return min(
        rows,
        key=lambda r: (
            date.fromisoformat(r['end_date']) - date.fromisoformat(r['start_date'])
        ).days,
    )


def _addon_is_person_type(connection, company_id: int, addon_id: int) -> bool:
    if not _table_exists(connection, 'setup_addon_person_pricing'):
        return False
    row = execute_sql(connection,
        'SELECT pricing_mode FROM setup_addon_person_pricing\n           WHERE company_id=:q630_0 AND addon_id=:q630_1',
        (company_id, addon_id),
    ).fetchone()
    return bool(row and str(row['pricing_mode']) == 'person_type')


def _allowed_addon_rule(connection, company_id: int, year: int, element, addon_id: int):
    override = execute_sql(connection,
        'SELECT * FROM setup_element_addons\n           WHERE company_id=:q631_0 AND year=:q631_1 AND element_id=:q631_2 AND addon_id=:q631_3',
        (company_id, year, int(element['id']), addon_id),
    ).fetchone()
    if override:
        state = str(override['state'])
        if state == 'N':
            return None
        if state == 'Y':
            return override
    return execute_sql(connection,
        'SELECT * FROM setup_type_addons\n           WHERE company_id=:q632_0 AND year=:q632_1 AND element_type=:q632_2 AND addon_id=:q632_3 AND allowed=1',
        (company_id, year, str(element['element_type']), addon_id),
    ).fetchone()


def element_missing_items(database, company_id: int, year: int, element_id: int) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    with database.connect() as c:
        element = execute_sql(c,
            'SELECT * FROM setup_elements WHERE company_id=:q634_0 AND id=:q634_1 AND active=1',
            (company_id, element_id),
        ).fetchone()
        if element is None:
            return items

        seasons = execute_sql(c,
            'SELECT * FROM setup_seasons WHERE company_id=:q635_0 AND year=:q635_1 ORDER BY start_date,id',
            (company_id, year),
        ).fetchall()
        if not seasons:
            items.append({
                'category': 'Seasons',
                'text': f"{element['name']} — no Season dates for {year}",
                'href': f'/setup/pricing?year={year}',
            })
        for season in seasons:
            rate = execute_sql(c,
                'SELECT rate FROM setup_element_rates\n                   WHERE company_id=:q640_0 AND year=:q640_1 AND element_id=:q640_2 AND season_id=:q640_3',
                (company_id, year, element_id, int(season['id'])),
            ).fetchone()
            if rate is None:
                items.append({
                    'category': 'Element pricing',
                    'text': f"{element['name']} — {season['name']} price missing",
                    'href': f'/setup/pricing?year={year}',
                })

        occupancy = execute_sql(c,
            'SELECT max_total FROM setup_occupancy\n               WHERE company_id=:q636_0 AND year=:q636_1 AND element_id=:q636_2',
            (company_id, year, element_id),
        ).fetchone()
        if occupancy is None:
            items.append({
                'category': 'Occupancy',
                'text': f"{element['name']} — total occupancy missing",
                'href': f'/setup/occupancy?year={year}',
            })

        people = execute_sql(c,
            'SELECT * FROM setup_person_types\n               WHERE company_id=:q637_0 AND active=1 ORDER BY lower(name)',
            (company_id,),
        ).fetchall()
        for person in people:
            pid = int(person['id'])
            limit = execute_sql(c,
                'SELECT max_count FROM setup_person_limits\n                   WHERE company_id=:q641_0 AND year=:q641_1 AND element_id=:q641_2 AND person_type_id=:q641_3',
                (company_id, year, element_id, pid),
            ).fetchone()
            if limit is None:
                items.append({
                    'category': 'Occupancy',
                    'text': f"{element['name']} — {person['name']} maximum missing",
                    'href': f'/setup/occupancy?year={year}',
                })
            price = execute_sql(c,
                'SELECT rate FROM setup_person_prices\n                   WHERE company_id=:q642_0 AND year=:q642_1 AND element_id=:q642_2 AND person_type_id=:q642_3',
                (company_id, year, element_id, pid),
            ).fetchone()
            if price is None:
                items.append({
                    'category': 'Person pricing',
                    'text': f"{element['name']} — {person['name']} price missing",
                    'href': f'/setup/occupancy?year={year}',
                })

        addons = execute_sql(c,
            'SELECT * FROM setup_addons\n               WHERE company_id=:q638_0 AND active=1 ORDER BY lower(name)',
            (company_id,),
        ).fetchall()
        for addon in addons:
            aid = int(addon['id'])
            rule = _allowed_addon_rule(c, company_id, year, element, aid)
            if rule is None:
                continue
            if rule['rate'] is None:
                items.append({
                    'category': 'Add-on pricing',
                    'text': f"{element['name']} — {addon['name']} price missing",
                    'href': f'/setup/addon-rules?year={year}',
                })
            if _addon_is_person_type(c, company_id, aid) and _table_exists(c, 'setup_addon_person_rates'):
                for person in people:
                    pid = int(person['id'])
                    rate = execute_sql(c,
                        'SELECT rate FROM setup_addon_person_rates\n                           WHERE company_id=:q644_0 AND addon_id=:q644_1 AND year=:q644_2 AND person_type_id=:q644_3',
                        (company_id, aid, year, pid),
                    ).fetchone()
                    if rate is None:
                        items.append({
                            'category': 'Add-on Person pricing',
                            'text': f"{element['name']} — {addon['name']} / {person['name']} price missing",
                            'href': f'/setup/addons/when?year={year}',
                        })
    return items


def incomplete_elements(database, company_id: int, year: int) -> list[dict[str, object]]:
    with database.connect() as c:
        elements = execute_sql(c,
            'SELECT id,name FROM setup_elements\n               WHERE company_id=:q639_0 AND active=1 ORDER BY element_type,lower(name)',
            (company_id,),
        ).fetchall()
    result: list[dict[str, object]] = []
    for element in elements:
        missing = element_missing_items(database, company_id, year, int(element['id']))
        if missing:
            result.append({
                'id': int(element['id']),
                'name': str(element['name']),
                'count': len(missing),
            })
    return result


def element_available_setup_ready(
    database,
    company_id: int,
    element_id: int,
    arrival: date,
    departure: date,
    *,
    missing_items_cache: dict | None = None,
) -> tuple[bool, str]:
    """Return whether an Element is safe to offer for these dates.

    Year-wide setup omissions (occupancy/person/Add-on rules) take the Element
    off sale for that year. Seasonal price omissions only take the affected
    dates off sale, so an incomplete future/new Season does not disturb an
    already-complete earlier Season.
    """
    if departure <= arrival:
        return False, 'Invalid stay dates.'
    if arrival.year != (departure - timedelta(days=1)).year:
        return False, 'The stay must remain within one pricing year.'
    year = arrival.year
    cache_key = (company_id, year, element_id)
    if missing_items_cache is not None and cache_key in missing_items_cache:
        missing = missing_items_cache[cache_key]
    else:
        missing = element_missing_items(database, company_id, year, element_id)
        if missing_items_cache is not None:
            missing_items_cache[cache_key] = missing
    year_wide = [
        item for item in missing
        if item['category'] not in {'Element pricing', 'Seasons'}
    ]
    if year_wide:
        return False, f"Setup incomplete for this Element in {year}."

    with database.connect() as c:
        day = arrival
        while day < departure:
            season = _season_for_date(c, company_id, year, day)
            if season is None:
                return False, f'No Season covers {day.isoformat()}.'
            rate = execute_sql(c,
                'SELECT rate FROM setup_element_rates\n                   WHERE company_id=:q643_0 AND year=:q643_1 AND element_id=:q643_2 AND season_id=:q643_3',
                (company_id, year, element_id, int(season['id'])),
            ).fetchone()
            if rate is None:
                return False, f"Pricing is not complete for {season['name']}."
            day += timedelta(days=1)
    return True, ''
