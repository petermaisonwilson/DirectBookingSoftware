from __future__ import annotations
from .sql import execute_sql

import json
from datetime import date
from urllib.parse import quote_plus

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import COOKIE_NAME, form_data
from .setup015_core import rows
from .webv1_booking_requirements import (
    _requirements_page,
    _relevant_addon_ids_for_type,
    _relevant_person_ids_for_type,
    _person_cap_map,
)
from .webv1_booking_requirements_refinements import _addon_caps, _working_context
from .webv1_ordering import person_type_rows


def register_booking_requirements_v3(app) -> None:
    database = app.state.database

    @app.post('/availability/requirements')
    @app.post('/availability/requirements-v3')
    async def requirements_save_v3(request: Request):
        context, cid = _working_context(database, request)
        token = request.cookies.get(COOKIE_NAME, '')
        data = await form_data(request)
        if data.get('csrf') != context['csrf_token']:
            raise HTTPException(status_code=403, detail='Invalid form token')

        raw_edit = str(data.get('edit_hold', '') or '').strip()
        edit_hold = int(raw_edit) if raw_edit.isdigit() and int(raw_edit) > 0 else 0
        held_type = ''
        if edit_hold:
            with database.connect() as c:
                owned = execute_sql(c,
                    'SELECT h.id,e.element_type FROM element_holds h\n                       JOIN setup_elements e ON e.id=h.element_id AND e.company_id=h.company_id\n                       WHERE h.id=:q470_0 AND h.company_id=:q470_1 AND h.session_token=:q470_2',
                    (edit_hold, cid, token),
                ).fetchone()
            if owned is None:
                edit_hold = 0
            else:
                held_type = str(owned['element_type'])

        element_type = str(data.get('element_type', '') or '').strip() or held_type
        active_types = {str(r['name']) for r in rows(database, 'SELECT name FROM setup_element_types WHERE company_id=:q461_0 AND active=1', (cid,))}
        if element_type and element_type not in active_types:
            return HTMLResponse(_requirements_page(database, context, cid, token, 'Please select a valid Element Type.', edit_hold=edit_hold, selected_element_type=element_type), 400)

        lead_name = str(data.get('lead_name', '') or '').strip()
        if not lead_name:
            return HTMLResponse(_requirements_page(database, context, cid, token, 'Please enter the lead name.', edit_hold=edit_hold, selected_element_type=element_type), 400)

        arrival = str(data.get('arrival', '')).strip()
        departure = str(data.get('departure', '')).strip()
        try:
            arrival_day = date.fromisoformat(arrival)
            departure_day = date.fromisoformat(departure)
        except ValueError:
            return HTMLResponse(_requirements_page(database, context, cid, token, 'Enter valid arrival and departure dates.', edit_hold=edit_hold, selected_element_type=element_type), 400)
        if departure_day <= arrival_day:
            return HTMLResponse(_requirements_page(database, context, cid, token, 'Departure must be after arrival.', edit_hold=edit_hold, selected_element_type=element_type), 400)
        if arrival_day.year != date.fromordinal(departure_day.toordinal() - 1).year:
            return HTMLResponse(_requirements_page(database, context, cid, token, 'The stay must remain within one pricing year.', edit_hold=edit_hold, selected_element_type=element_type), 400)

        year = arrival_day.year
        relevant_people = _relevant_person_ids_for_type(database, cid, element_type, year)
        relevant_addons = _relevant_addon_ids_for_type(database, cid, element_type, year)
        people_rows = [p for p in person_type_rows(database, cid, active_only=True) if int(p['id']) in relevant_people]
        addon_rows = [a for a in rows(database, 'SELECT * FROM setup_addons WHERE company_id=:q462_0 AND active=1 AND ask_before_availability=1 ORDER BY lower(name)', (cid,)) if int(a['id']) in relevant_addons]

        parsed_people = []
        total = 0
        type_caps = _person_cap_map(database, cid).get(element_type, {}).get(str(year), {})
        for p in people_rows:
            pid = int(p['id'])
            try:
                qty = max(0, int(data.get(f'person_{pid}', '0') or 0))
            except ValueError:
                return HTMLResponse(_requirements_page(database, context, cid, token, f'Enter a valid number for {p["name"]}.', edit_hold=edit_hold, selected_element_type=element_type), 400)
            cap = int(type_caps.get(str(pid), 99))
            if qty > cap:
                return HTMLResponse(_requirements_page(database, context, cid, token, f'{p["name"]} can be entered up to a maximum of {cap} for {element_type}.', edit_hold=edit_hold, selected_element_type=element_type), 400)
            total += qty
            ages = []
            if int(p['ask_age'] or 0):
                for i in range(1, qty + 1):
                    try:
                        age = int(str(data.get(f'age_{pid}_{i}', '')).strip())
                    except ValueError:
                        return HTMLResponse(_requirements_page(database, context, cid, token, f'Enter the age at arrival for every {p["name"]}.', edit_hold=edit_hold, selected_element_type=element_type), 400)
                    if age < 0 or age > 120:
                        return HTMLResponse(_requirements_page(database, context, cid, token, 'Age must be between 0 and 120.', edit_hold=edit_hold, selected_element_type=element_type), 400)
                    ages.append(age)
            parsed_people.append((pid, qty, json.dumps(ages)))
        if people_rows and total <= 0:
            return HTMLResponse(_requirements_page(database, context, cid, token, 'Enter at least one person.', edit_hold=edit_hold, selected_element_type=element_type), 400)

        caps = _addon_caps(database, cid)
        parsed_addons = []
        for a in addon_rows:
            aid = int(a['id']); cap = int(caps.get(aid, 0))
            raw_values = data.getlist(f'addon_{aid}') if hasattr(data, 'getlist') else [data.get(f'addon_{aid}', '0')]
            try:
                values = [max(0, int(v or 0)) for v in raw_values]; qty = max(values) if values else 0
            except (ValueError, TypeError):
                return HTMLResponse(_requirements_page(database, context, cid, token, f'Enter a valid quantity for {a["name"]}.', edit_hold=edit_hold, selected_element_type=element_type), 400)
            if qty > cap:
                return HTMLResponse(_requirements_page(database, context, cid, token, f'{a["name"]} can be requested up to a maximum of {cap}.', edit_hold=edit_hold, selected_element_type=element_type), 400)
            parsed_addons.append((aid, qty))

        with database.connect() as c:
            execute_sql(c, 'DELETE FROM booking_requirement_people WHERE company_id=:q458_0 AND session_token=:q458_1', (cid, token))
            execute_sql(c, 'DELETE FROM booking_requirement_addons WHERE company_id=:q459_0 AND session_token=:q459_1', (cid, token))
            for pid, qty, ages in parsed_people:
                execute_sql(c, 'INSERT INTO booking_requirement_people(session_token,company_id,person_type_id,quantity,ages_json) VALUES (:q463_0,:q463_1,:q463_2,:q463_3,:q463_4)', (token, cid, pid, qty, ages))
            for aid, qty in parsed_addons:
                execute_sql(c, 'INSERT INTO booking_requirement_addons(session_token,company_id,addon_id,quantity) VALUES (:q464_0,:q464_1,:q464_2,:q464_3)', (token, cid, aid, qty))
            execute_sql(c, 'INSERT INTO booking_requirement_sessions(session_token,company_id,ready,arrival_date,departure_date,lead_name,updated_at)\n                         VALUES (:q460_0,:q460_1,1,:q460_2,:q460_3,:q460_4,CURRENT_TIMESTAMP)\n                         ON CONFLICT(session_token,company_id) DO UPDATE SET ready=1,arrival_date=excluded.arrival_date,\n                         departure_date=excluded.departure_date,lead_name=excluded.lead_name,updated_at=CURRENT_TIMESTAMP',
                      (token, cid, arrival, departure, lead_name))

            # Editing the same Element Type commits only this held item's relevant
            # requirements. Other Elements in the same named booking can intentionally
            # have different occupancy and requirements.
            if edit_hold and element_type == held_type:
                execute_sql(c, 'DELETE FROM hold_requirement_people WHERE hold_id=:q465_0', (edit_hold,))
                execute_sql(c, 'DELETE FROM hold_requirement_addons WHERE hold_id=:q466_0', (edit_hold,))
                execute_sql(c, 'INSERT INTO hold_requirement_people(hold_id,company_id,person_type_id,quantity,ages_json)\n                             SELECT :q467_0,company_id,person_type_id,quantity,ages_json FROM booking_requirement_people\n                             WHERE company_id=:q467_1 AND session_token=:q467_2', (edit_hold, cid, token))
                execute_sql(c, 'INSERT INTO hold_requirement_addons(hold_id,company_id,addon_id,quantity)\n                             SELECT :q468_0,company_id,addon_id,quantity FROM booking_requirement_addons\n                             WHERE company_id=:q468_1 AND session_token=:q468_2', (edit_hold, cid, token))
                execute_sql(c, 'UPDATE element_holds SET lead_name=:q469_0 WHERE id=:q469_1 AND company_id=:q469_2 AND session_token=:q469_3', (lead_name, edit_hold, cid, token))

        parts = []
        if element_type: parts.append('element_type=' + quote_plus(element_type))
        parts.extend([f'arrival={arrival}', f'departure={departure}'])
        if edit_hold: parts.append(f'edit_hold={edit_hold}')
        return RedirectResponse('/availability/calendar-v2?' + '&'.join(parts), 303)
