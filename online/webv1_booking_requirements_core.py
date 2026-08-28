from __future__ import annotations

import json
import re
from datetime import date

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import COOKIE_NAME, form_data
from .setup015_core import one, rows
from .webv1_booking_requirements import _requirements_page
from .webv1_rule_resolver import resolve_element_item_rule


def _working_context(database, request: Request):
    context = database.session_context(request.cookies.get(COOKIE_NAME))
    if context is None:
        raise HTTPException(status_code=401, detail='Login required')
    cid = context['acting_company_id'] if context['role'] == 'supervisor' else context['company_id']
    if not cid:
        raise HTTPException(status_code=403, detail='Select a Client first')
    return context, int(cid)


def _group_key(name: str) -> str:
    return 'feature_group_' + re.sub(r'[^a-z0-9]+', '_', name, flags=re.I).strip('_')


def _requirement_cap_anywhere(database, cid: int, addon_id: int) -> int:
    elements = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1', (cid,))
    configured_years = [int(r['year']) for r in rows(database, 'SELECT year FROM setup_years WHERE company_id=? ORDER BY year', (cid,))]
    if not configured_years:
        configured_years = [date.today().year]
    highest = 0
    unlimited = False
    for year in configured_years:
        for element in elements:
            rule = resolve_element_item_rule(database, cid, year, element, addon_id)
            if not rule.get('allowed'):
                continue
            maximum = rule.get('max')
            if maximum is None:
                unlimited = True
            else:
                highest = max(highest, int(maximum))
    return 99 if unlimited else highest


def _relevant_requirement_ids(database, cid: int, year: int, element_type: str) -> set[int]:
    relevant = {
        int(r['addon_id'])
        for r in rows(database,
            'SELECT addon_id FROM setup_type_addons WHERE company_id=? AND year=? AND element_type=? AND allowed=1',
            (cid, year, element_type))
    }
    relevant.update(
        int(r['addon_id'])
        for r in rows(database,
            '''SELECT DISTINCT ea.addon_id
               FROM setup_element_addons ea
               JOIN setup_elements e ON e.id=ea.element_id AND e.company_id=ea.company_id
               WHERE ea.company_id=? AND ea.year=? AND e.element_type=?
                 AND e.active=1 AND ea.state='Y' ''',
            (cid, year, element_type))
    )
    return relevant


def element_reasons(database, cid: int, year: int, element, people: dict, requirements: dict) -> list[str]:
    """Authoritative suitability calculation.

    Requirements are positive-only: quantity zero means the customer does not
    require that capability, never that an Element providing it is unsuitable.
    """
    reasons: list[str] = []
    element_type = str(element['element_type'])
    pricing_method = str(element['pricing_method'] or '')

    if pricing_method != 'Per day':
        total = sum(int(v.get('quantity', 0)) for v in people.values())
        occupancy = one(database, 'SELECT max_total FROM setup_occupancy WHERE company_id=? AND year=? AND element_id=?',
                        (cid, year, int(element['id'])))
        if occupancy is None:
            reasons.append('occupancy setup incomplete')
        elif total > int(occupancy['max_total']):
            reasons.append(f'maximum occupancy {int(occupancy["max_total"])}')

        for pid, data in people.items():
            qty = int(data.get('quantity', 0))
            if qty <= 0:
                continue
            person = one(database, 'SELECT name FROM setup_person_types WHERE company_id=? AND id=?', (cid, pid))
            name = str(person['name']) if person else 'Person type'
            limit = one(database, 'SELECT max_count FROM setup_person_limits WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?',
                        (cid, year, int(element['id']), pid))
            if limit is None:
                reasons.append(f'{name} not configured')
            elif qty > int(limit['max_count']):
                reasons.append(f'{name} not allowed' if int(limit['max_count']) == 0 else f'{name} max {int(limit["max_count"])}')

    relevant = _relevant_requirement_ids(database, cid, year, element_type)
    for aid, raw_qty in requirements.items():
        aid = int(aid); qty = int(raw_qty)
        if qty <= 0:
            continue
        if aid not in relevant:
            continue
        item = one(database, 'SELECT name FROM setup_addons WHERE company_id=? AND id=?', (cid, aid))
        name = str(item['name']) if item else 'Requirement'
        rule = resolve_element_item_rule(database, cid, year, element, aid)
        if not rule['allowed']:
            reasons.append(f'no {name}')
        elif rule['max'] is not None and qty > int(rule['max']):
            reasons.append(f'{name} max {int(rule["max"])}')
    return reasons


def _remove_route(app, path: str) -> None:
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, 'path', None) != path]


def register_booking_requirements_core(app) -> None:
    database = app.state.database
    _remove_route(app, '/availability/requirements-v2')

    @app.post('/availability/requirements-v2')
    async def requirements_save(request: Request):
        context, cid = _working_context(database, request)
        token = request.cookies.get(COOKIE_NAME, '')
        data = await form_data(request)
        if data.get('csrf') != context['csrf_token']:
            raise HTTPException(status_code=403, detail='Invalid form token')

        people_rows = rows(database, 'SELECT * FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name', (cid,))
        parsed_people: list[tuple[int, int, str]] = []
        total = 0
        for p in people_rows:
            pid = int(p['id'])
            try:
                qty = max(0, int(data.get(f'person_{pid}', '0') or 0))
            except ValueError:
                return HTMLResponse(_requirements_page(database, context, cid, token, f'Enter a valid number for {p["name"]}.'), 400)
            total += qty
            ages: list[int] = []
            if int(p['ask_age'] or 0):
                for i in range(1, qty + 1):
                    raw = str(data.get(f'age_{pid}_{i}', '')).strip()
                    try:
                        age = int(raw)
                    except ValueError:
                        return HTMLResponse(_requirements_page(database, context, cid, token, f'Enter the age at arrival for every {p["name"]}.'), 400)
                    if age < 0 or age > 120:
                        return HTMLResponse(_requirements_page(database, context, cid, token, 'Age must be between 0 and 120.'), 400)
                    ages.append(age)
            parsed_people.append((pid, qty, json.dumps(ages)))
        if people_rows and total <= 0:
            return HTMLResponse(_requirements_page(database, context, cid, token, 'Enter at least one person.'), 400)

        addon_rows = rows(database, '''SELECT * FROM setup_addons
               WHERE company_id=? AND active=1 AND ask_before_availability=1
               ORDER BY item_kind,feature_group,name COLLATE NOCASE''', (cid,))
        caps = {int(a['id']): _requirement_cap_anywhere(database, cid, int(a['id'])) for a in addon_rows}
        parsed: dict[int, int] = {int(a['id']): 0 for a in addon_rows}

        groups: dict[str, list] = {}; standalone = []
        for a in addon_rows:
            group = str(a['feature_group'] or '').strip() if str(a['item_kind'] or '') == 'Feature' else ''
            if group:
                groups.setdefault(group, []).append(a)
            else:
                standalone.append(a)

        for group, members in groups.items():
            key = _group_key(group)
            if key not in data:
                return HTMLResponse(_requirements_page(database, context, cid, token, f'Please select {group}, even if you choose None.'), 400)
            chosen = str(data.get(key, '')).strip()
            if chosen:
                valid_ids = {str(int(a['id'])) for a in members}
                if chosen not in valid_ids:
                    return HTMLResponse(_requirements_page(database, context, cid, token, f'Please select a valid {group}.'), 400)
                aid = int(chosen)
                if caps.get(aid, 0) <= 0:
                    item = next(a for a in members if int(a['id']) == aid)
                    return HTMLResponse(_requirements_page(database, context, cid, token, f'{item["name"]} is not available on any Element.'), 400)
                parsed[aid] = 1

        for a in standalone:
            aid = int(a['id']); cap = int(caps.get(aid, 0))
            values = data.getlist(f'addon_{aid}') if hasattr(data, 'getlist') else [data.get(f'addon_{aid}', '0')]
            try:
                qty = max(int(v or 0) for v in values)
            except (ValueError, TypeError):
                return HTMLResponse(_requirements_page(database, context, cid, token, f'Enter a valid quantity for {a["name"]}.'), 400)
            if qty < 0 or qty > cap:
                return HTMLResponse(_requirements_page(database, context, cid, token, f'{a["name"]} can be requested up to a maximum of {cap}.'), 400)
            parsed[aid] = qty

        with database.connect() as c:
            c.execute('DELETE FROM booking_requirement_people WHERE company_id=? AND session_token=?', (cid, token))
            c.execute('DELETE FROM booking_requirement_addons WHERE company_id=? AND session_token=?', (cid, token))
            for pid, qty, ages in parsed_people:
                c.execute('INSERT INTO booking_requirement_people(session_token,company_id,person_type_id,quantity,ages_json) VALUES (?,?,?,?,?)',
                          (token, cid, pid, qty, ages))
            for aid, qty in parsed.items():
                c.execute('INSERT INTO booking_requirement_addons(session_token,company_id,addon_id,quantity) VALUES (?,?,?,?)',
                          (token, cid, aid, qty))
            c.execute('''INSERT INTO booking_requirement_sessions(session_token,company_id,ready,updated_at)
                   VALUES (?,?,1,CURRENT_TIMESTAMP)
                   ON CONFLICT(session_token,company_id) DO UPDATE SET ready=1,updated_at=CURRENT_TIMESTAMP''', (token, cid))
        return RedirectResponse('/availability/calendar-v2', 303)
