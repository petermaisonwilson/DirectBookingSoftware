from __future__ import annotations

from datetime import date, timedelta


def _table_exists(connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _season_for_date(connection, company_id: int, year: int, day: date):
    rows = connection.execute(
        '''SELECT * FROM setup_seasons
           WHERE company_id=? AND year=? AND start_date<=? AND end_date>=?''',
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
    row = connection.execute(
        '''SELECT pricing_mode FROM setup_addon_person_pricing
           WHERE company_id=? AND addon_id=?''',
        (company_id, addon_id),
    ).fetchone()
    return bool(row and str(row['pricing_mode']) == 'person_type')


def _allowed_addon_rule(connection, company_id: int, year: int, element, addon_id: int):
    override = connection.execute(
        '''SELECT * FROM setup_element_addons
           WHERE company_id=? AND year=? AND element_id=? AND addon_id=?''',
        (company_id, year, int(element['id']), addon_id),
    ).fetchone()
    if override:
        state = str(override['state'])
        if state == 'N':
            return None
        if state == 'Y':
            return override
    return connection.execute(
        '''SELECT * FROM setup_type_addons
           WHERE company_id=? AND year=? AND element_type=? AND addon_id=? AND allowed=1''',
        (company_id, year, str(element['element_type']), addon_id),
    ).fetchone()


def element_missing_items(database, company_id: int, year: int, element_id: int) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    with database.connect() as c:
        element = c.execute(
            'SELECT * FROM setup_elements WHERE company_id=? AND id=? AND active=1',
            (company_id, element_id),
        ).fetchone()
        if element is None:
            return items

        seasons = c.execute(
            'SELECT * FROM setup_seasons WHERE company_id=? AND year=? ORDER BY start_date,id',
            (company_id, year),
        ).fetchall()
        if not seasons:
            items.append({
                'category': 'Seasons',
                'text': f"{element['name']} — no Season dates for {year}",
                'href': f'/setup/pricing?year={year}',
            })
        for season in seasons:
            rate = c.execute(
                '''SELECT rate FROM setup_element_rates
                   WHERE company_id=? AND year=? AND element_id=? AND season_id=?''',
                (company_id, year, element_id, int(season['id'])),
            ).fetchone()
            if rate is None:
                items.append({
                    'category': 'Element pricing',
                    'text': f"{element['name']} — {season['name']} price missing",
                    'href': f'/setup/pricing?year={year}',
                })

        occupancy = c.execute(
            '''SELECT max_total FROM setup_occupancy
               WHERE company_id=? AND year=? AND element_id=?''',
            (company_id, year, element_id),
        ).fetchone()
        if occupancy is None:
            items.append({
                'category': 'Occupancy',
                'text': f"{element['name']} — total occupancy missing",
                'href': f'/setup/occupancy?year={year}',
            })

        people = c.execute(
            '''SELECT * FROM setup_person_types
               WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE''',
            (company_id,),
        ).fetchall()
        for person in people:
            pid = int(person['id'])
            limit = c.execute(
                '''SELECT max_count FROM setup_person_limits
                   WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?''',
                (company_id, year, element_id, pid),
            ).fetchone()
            if limit is None:
                items.append({
                    'category': 'Occupancy',
                    'text': f"{element['name']} — {person['name']} maximum missing",
                    'href': f'/setup/occupancy?year={year}',
                })
            price = c.execute(
                '''SELECT rate FROM setup_person_prices
                   WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?''',
                (company_id, year, element_id, pid),
            ).fetchone()
            if price is None:
                items.append({
                    'category': 'Person pricing',
                    'text': f"{element['name']} — {person['name']} price missing",
                    'href': f'/setup/occupancy?year={year}',
                })

        addons = c.execute(
            '''SELECT * FROM setup_addons
               WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE''',
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
                    rate = c.execute(
                        '''SELECT rate FROM setup_addon_person_rates
                           WHERE company_id=? AND addon_id=? AND year=? AND person_type_id=?''',
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
        elements = c.execute(
            '''SELECT id,name FROM setup_elements
               WHERE company_id=? AND active=1 ORDER BY element_type,name COLLATE NOCASE''',
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
    missing = element_missing_items(database, company_id, year, element_id)
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
            rate = c.execute(
                '''SELECT rate FROM setup_element_rates
                   WHERE company_id=? AND year=? AND element_id=? AND season_id=?''',
                (company_id, year, element_id, int(season['id'])),
            ).fetchone()
            if rate is None:
                return False, f"Pricing is not complete for {season['name']}."
            day += timedelta(days=1)
    return True, ''
