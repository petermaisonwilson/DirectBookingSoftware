from __future__ import annotations

import json
from datetime import date
from urllib.parse import quote_plus

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import COOKIE_NAME, form_data
from .setup015_core import rows
from .webv1_booking_requirements import _requirements_page, _snapshot_hold_requirements
from .webv1_booking_requirements_refinements import _addon_caps, _working_context
from .webv1_ordering import person_type_rows


def register_booking_requirements_v3(app) -> None:
    database = app.state.database

    @app.post('/availability/requirements-v3')
    async def requirements_save_v3(request: Request):
        context, cid = _working_context(database, request)
        token = request.cookies.get(COOKIE_NAME, '')
        data = await form_data(request)
        if data.get('csrf') != context['csrf_token']:
            raise HTTPException(status_code=403, detail='Invalid form token')

        raw_edit = str(data.get('edit_hold', '') or '').strip()
        edit_hold = int(raw_edit) if raw_edit.isdigit() and int(raw_edit) > 0 else 0
        if edit_hold:
            with database.connect() as c:
                owned = c.execute('SELECT id FROM element_holds WHERE id=? AND company_id=? AND session_token=?', (edit_hold, cid, token)).fetchone()
            if owned is None:
                edit_hold = 0

        lead_name = str(data.get('lead_name', '') or '').strip()
        if not lead_name:
            return HTMLResponse(_requirements_page(database, context, cid, token, 'Please enter the lead name.', edit_hold=edit_hold), 400)

        arrival = str(data.get('arrival', '')).strip()
        departure = str(data.get('departure', '')).strip()
        try:
            arrival_day = date.fromisoformat(arrival)
            departure_day = date.fromisoformat(departure)
        except ValueError:
            return HTMLResponse(_requirements_page(database, context, cid, token, 'Enter valid arrival and departure dates.', edit_hold=edit_hold), 400)
        if departure_day <= arrival_day:
            return HTMLResponse(_requirements_page(database, context, cid, token, 'Departure must be after arrival.', edit_hold=edit_hold), 400)
        if arrival_day.year != date.fromordinal(departure_day.toordinal() - 1).year:
            return HTMLResponse(_requirements_page(database, context, cid, token, 'The stay must remain within one pricing year.', edit_hold=edit_hold), 400)

        people_rows = person_type_rows(database, cid, active_only=True)
        addon_rows = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND active=1 AND ask_before_availability=1 ORDER BY name COLLATE NOCASE', (cid,))
        parsed_people = []
        total = 0
        for p in people_rows:
            pid = int(p['id'])
            try:
                qty = max(0, int(data.get(f'person_{pid}', '0') or 0))
            except ValueError:
                return HTMLResponse(_requirements_page(database, context, cid, token, f'Enter a valid number for {p["name"]}.', edit_hold=edit_hold), 400)
            total += qty
            ages = []
            if int(p['ask_age'] or 0):
                for i in range(1, qty + 1):
                    try:
                        age = int(str(data.get(f'age_{pid}_{i}', '')).strip())
                    except ValueError:
                        return HTMLResponse(_requirements_page(database, context, cid, token, f'Enter the age at arrival for every {p["name"]}.', edit_hold=edit_hold), 400)
                    if age < 0 or age > 120:
                        return HTMLResponse(_requirements_page(database, context, cid, token, 'Age must be between 0 and 120.', edit_hold=edit_hold), 400)
                    ages.append(age)
            parsed_people.append((pid, qty, json.dumps(ages)))
        if people_rows and total <= 0:
            return HTMLResponse(_requirements_page(database, context, cid, token, 'Enter at least one person.', edit_hold=edit_hold), 400)

        caps = _addon_caps(database, cid)
        parsed_addons = []
        for a in addon_rows:
            aid = int(a['id'])
            cap = int(caps.get(aid, 0))
            raw_values = data.getlist(f'addon_{aid}') if hasattr(data, 'getlist') else [data.get(f'addon_{aid}', '0')]
            try:
                values = [max(0, int(v or 0)) for v in raw_values]
                qty = max(values) if values else 0
            except (ValueError, TypeError):
                return HTMLResponse(_requirements_page(database, context, cid, token, f'Enter a valid quantity for {a["name"]}.', edit_hold=edit_hold), 400)
            if qty > cap:
                return HTMLResponse(_requirements_page(database, context, cid, token, f'{a["name"]} can be requested up to a maximum of {cap}.', edit_hold=edit_hold), 400)
            parsed_addons.append((aid, qty))

        with database.connect() as c:
            c.execute('DELETE FROM booking_requirement_people WHERE company_id=? AND session_token=?', (cid, token))
            c.execute('DELETE FROM booking_requirement_addons WHERE company_id=? AND session_token=?', (cid, token))
            for pid, qty, ages in parsed_people:
                c.execute('INSERT INTO booking_requirement_people(session_token,company_id,person_type_id,quantity,ages_json) VALUES (?,?,?,?,?)', (token, cid, pid, qty, ages))
            for aid, qty in parsed_addons:
                c.execute('INSERT INTO booking_requirement_addons(session_token,company_id,addon_id,quantity) VALUES (?,?,?,?)', (token, cid, aid, qty))
            c.execute('''INSERT INTO booking_requirement_sessions(session_token,company_id,ready,arrival_date,departure_date,lead_name,updated_at)
                         VALUES (?,?,1,?,?,?,CURRENT_TIMESTAMP)
                         ON CONFLICT(session_token,company_id) DO UPDATE SET
                           ready=1,arrival_date=excluded.arrival_date,departure_date=excluded.departure_date,
                           lead_name=excluded.lead_name,updated_at=CURRENT_TIMESTAMP''',
                      (token, cid, arrival, departure, lead_name))

        if edit_hold:
            _snapshot_hold_requirements(database, cid, token, edit_hold)
            with database.connect() as c:
                c.execute('UPDATE element_holds SET lead_name=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND company_id=? AND session_token=?', (lead_name, edit_hold, cid, token))
                item = c.execute(
                    '''SELECT e.element_type FROM element_holds h
                       JOIN setup_elements e ON e.id=h.element_id AND e.company_id=h.company_id
                       WHERE h.id=? AND h.company_id=? AND h.session_token=?''',
                    (edit_hold, cid, token),
                ).fetchone()
            element_type = quote_plus(str(item['element_type'])) if item else ''
            return RedirectResponse(f'/availability/calendar-v2?element_type={element_type}&arrival={arrival}&departure={departure}&edit_hold={edit_hold}', 303)
        return RedirectResponse(f'/availability/calendar-v2?arrival={arrival}&departure={departure}', 303)


def install_booking_requirements_v3_form(app) -> None:
    @app.middleware('http')
    async def booking_requirements_v3_form(request, call_next):
        response = await call_next(request)
        if request.url.path != '/availability/start' or response.status_code >= 400 or 'text/html' not in response.headers.get('content-type', ''):
            return response
        body = b''
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else str(chunk).encode('utf-8')
        text = body.decode('utf-8')
        headers = {k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'content-type'}}
        text = text.replace('action="/availability/requirements-v2"', 'action="/availability/requirements-v3"', 1)
        from fastapi.responses import Response
        return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
