from __future__ import annotations

import json
from datetime import date

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import COOKIE_NAME, esc, form_data, layout
from .setup015_calculator import _addon_rule
from .setup015_core import audit, context_for, one, require_csrf, rows, working_company
from .webv1_booking_progress import booking_progress_strip
from .webv1_ordering import person_type_rows

REQUIREMENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS booking_requirement_sessions (session_token TEXT NOT NULL,company_id INTEGER NOT NULL,ready INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(session_token,company_id));
CREATE TABLE IF NOT EXISTS booking_requirement_people (session_token TEXT NOT NULL,company_id INTEGER NOT NULL,person_type_id INTEGER NOT NULL,quantity INTEGER NOT NULL DEFAULT 0,ages_json TEXT NOT NULL DEFAULT '[]',PRIMARY KEY(session_token,company_id,person_type_id));
CREATE TABLE IF NOT EXISTS booking_requirement_addons (session_token TEXT NOT NULL,company_id INTEGER NOT NULL,addon_id INTEGER NOT NULL,quantity INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(session_token,company_id,addon_id));
CREATE TABLE IF NOT EXISTS hold_requirement_people (hold_id INTEGER NOT NULL,company_id INTEGER NOT NULL,person_type_id INTEGER NOT NULL,quantity INTEGER NOT NULL DEFAULT 0,ages_json TEXT NOT NULL DEFAULT '[]',PRIMARY KEY(hold_id,person_type_id));
CREATE TABLE IF NOT EXISTS hold_requirement_addons (hold_id INTEGER NOT NULL,company_id INTEGER NOT NULL,addon_id INTEGER NOT NULL,quantity INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(hold_id,addon_id));
"""


def _ensure_column(connection, table, column, definition):
    if column not in {str(r['name']) for r in connection.execute(f'PRAGMA table_info({table})').fetchall()}:
        connection.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')


def initialise_booking_requirements(database):
    with database.connect() as c:
        c.executescript(REQUIREMENTS_SCHEMA)
        _ensure_column(c, 'setup_person_types', 'ask_age', 'INTEGER NOT NULL DEFAULT 0')
        _ensure_column(c, 'setup_addons', 'ask_before_availability', 'INTEGER NOT NULL DEFAULT 0')
        _ensure_column(c, 'setup_person_limits', 'min_count', 'INTEGER NOT NULL DEFAULT 0')
        _ensure_column(c, 'booking_requirement_sessions', 'arrival_date', "TEXT NOT NULL DEFAULT ''")
        _ensure_column(c, 'booking_requirement_sessions', 'departure_date', "TEXT NOT NULL DEFAULT ''")
        _ensure_column(c, 'booking_requirement_sessions', 'lead_name', "TEXT NOT NULL DEFAULT ''")
        _ensure_column(c, 'element_holds', 'lead_name', "TEXT NOT NULL DEFAULT ''")
        c.execute('DELETE FROM hold_requirement_people WHERE hold_id NOT IN (SELECT id FROM element_holds)')
        c.execute('DELETE FROM hold_requirement_addons WHERE hold_id NOT IN (SELECT id FROM element_holds)')


def _session_context(database, request):
    context = database.session_context(request.cookies.get(COOKIE_NAME))
    if context is None:
        raise HTTPException(status_code=401, detail='Login required')
    cid = context['acting_company_id'] if context['role'] == 'supervisor' else context['company_id']
    if not cid:
        raise HTTPException(status_code=403, detail='Select a Client first')
    return context, int(cid)


def _saved_requirements(database, cid, token):
    people = {
        int(r['person_type_id']): {'quantity': int(r['quantity']), 'ages': json.loads(r['ages_json'] or '[]')}
        for r in rows(database, 'SELECT * FROM booking_requirement_people WHERE company_id=? AND session_token=?', (cid, token))
    }
    addons = {
        int(r['addon_id']): int(r['quantity'])
        for r in rows(database, 'SELECT * FROM booking_requirement_addons WHERE company_id=? AND session_token=?', (cid, token))
    }
    saved = one(database, 'SELECT ready,arrival_date,departure_date FROM booking_requirement_sessions WHERE company_id=? AND session_token=?', (cid, token))
    ready = bool(saved and int(saved['ready'] or 0))
    arrival = str(saved['arrival_date'] or '') if saved else ''
    departure = str(saved['departure_date'] or '') if saved else ''
    return people, addons, ready, arrival, departure


def _saved_lead_name(database, cid, token):
    saved = one(database, 'SELECT lead_name FROM booking_requirement_sessions WHERE company_id=? AND session_token=?', (cid, token))
    return str(saved['lead_name'] or '') if saved else ''


def _load_hold_requirements_into_working(database, cid: int, token: str, hold_id: int) -> bool:
    with database.connect() as c:
        hold = c.execute(
            '''SELECT id,arrival_date,departure_date,lead_name FROM element_holds
               WHERE id=? AND company_id=? AND session_token=?''',
            (hold_id, cid, token),
        ).fetchone()
        if hold is None:
            return False
        c.execute('DELETE FROM booking_requirement_people WHERE company_id=? AND session_token=?', (cid, token))
        c.execute('DELETE FROM booking_requirement_addons WHERE company_id=? AND session_token=?', (cid, token))
        c.execute(
            '''INSERT INTO booking_requirement_people(session_token,company_id,person_type_id,quantity,ages_json)
               SELECT ?,company_id,person_type_id,quantity,ages_json FROM hold_requirement_people WHERE hold_id=?''',
            (token, hold_id),
        )
        c.execute(
            '''INSERT INTO booking_requirement_addons(session_token,company_id,addon_id,quantity)
               SELECT ?,company_id,addon_id,quantity FROM hold_requirement_addons WHERE hold_id=?''',
            (token, hold_id),
        )
        c.execute(
            '''INSERT INTO booking_requirement_sessions(session_token,company_id,ready,arrival_date,departure_date,lead_name,updated_at)
               VALUES (?,?,1,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(session_token,company_id) DO UPDATE SET ready=1,arrival_date=excluded.arrival_date,
               departure_date=excluded.departure_date,lead_name=excluded.lead_name,updated_at=CURRENT_TIMESTAMP''',
            (token, cid, str(hold['arrival_date']), str(hold['departure_date']), str(hold['lead_name'] or '')),
        )
    return True


def _fmt_user_date(value):
    try:
        return date.fromisoformat(value).strftime('%d/%m/%Y')
    except (TypeError, ValueError):
        return value or ''


def _held_element_type(database, cid: int, token: str, hold_id: int) -> str:
    if not hold_id:
        return ''
    item = one(database, '''SELECT e.element_type FROM element_holds h
                            JOIN setup_elements e ON e.id=h.element_id AND e.company_id=h.company_id
                            WHERE h.id=? AND h.company_id=? AND h.session_token=?''', (hold_id, cid, token))
    return str(item['element_type']) if item else ''


def _relevant_person_ids_for_type(database, cid: int, element_type: str, year: int) -> set[int]:
    if not element_type:
        return {int(r['id']) for r in person_type_rows(database, cid, active_only=True)}
    return {int(r['person_type_id']) for r in rows(database, '''SELECT DISTINCT l.person_type_id
        FROM setup_person_limits l JOIN setup_elements e ON e.id=l.element_id AND e.company_id=l.company_id
        JOIN setup_person_types p ON p.id=l.person_type_id AND p.company_id=l.company_id
        WHERE l.company_id=? AND l.year=? AND e.element_type=? AND e.active=1 AND p.active=1
        AND (l.max_count>0 OR l.min_count>0)''', (cid, year, element_type))}


def _relevant_addon_ids_for_type(database, cid: int, element_type: str, year: int) -> set[int]:
    if not element_type:
        return {int(r['id']) for r in rows(database, 'SELECT id FROM setup_addons WHERE company_id=? AND active=1 AND ask_before_availability=1', (cid,))}
    result: set[int] = set()
    elements = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1 AND element_type=?', (cid, element_type))
    addons = rows(database, 'SELECT id FROM setup_addons WHERE company_id=? AND active=1 AND ask_before_availability=1', (cid,))
    for addon in addons:
        aid = int(addon['id'])
        if any(_addon_rule(database, cid, year, element, aid).get('allowed') for element in elements):
            result.add(aid)
    return result


def _person_type_map(database, cid: int) -> dict[str, list[int]]:
    mapping: dict[str, list[int]] = {}
    years = [int(r['year']) for r in rows(database, 'SELECT year FROM setup_years WHERE company_id=?', (cid,))]
    types = [str(r['name']) for r in rows(database, 'SELECT name FROM setup_element_types WHERE company_id=? AND active=1', (cid,))]
    for typ in types:
        ids: set[int] = set()
        for year in years:
            ids |= _relevant_person_ids_for_type(database, cid, typ, year)
        mapping[typ] = sorted(ids)
    return mapping


def _requirements_page(database, context, cid, token, message='', edit_hold: int = 0, selected_element_type: str = ''):
    people_rows = person_type_rows(database, cid, active_only=True)
    addon_rows = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND active=1 AND ask_before_availability=1 ORDER BY name COLLATE NOCASE', (cid,))
    element_types = [str(r['name']) for r in rows(database, 'SELECT name FROM setup_element_types WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (cid,))]
    if edit_hold and not selected_element_type:
        selected_element_type = _held_element_type(database, cid, token, edit_hold)
    if selected_element_type not in element_types:
        selected_element_type = ''
    saved_people, saved_addons, _, saved_arrival, saved_departure = _saved_requirements(database, cid, token)
    saved_lead_name = _saved_lead_name(database, cid, token)
    error = f'<div class="error">{esc(message)}</div>' if message else ''
    progress = booking_progress_strip(database, context, cid, token)
    edit_hidden = f'<input type="hidden" name="edit_hold" value="{int(edit_hold)}">' if edit_hold else ''
    edit_note = ''
    if edit_hold:
        edit_note = '<div class="card edit-notice"><strong>Editing this basket item.</strong> Change the requirements that apply to this Element, then click <strong>SEARCH AVAILABILITY</strong>. Your current Element remains held while you explore alternatives.</div>'
    type_options = '<option value="">Select Element Type</option>' + ''.join(
        f'<option value="{esc(t)}" {"selected" if t == selected_element_type else ""}>{esc(t)}</option>' for t in element_types
    )
    body = f'''<h1>Booking requirements</h1>{progress}{edit_note}{error}
    <div class="card"><p>Choose the <strong>Element Type</strong> and DBS will show only the Person Types, Features and Extras that can affect that type. Items that do not define suitability are left out.</p><p class="muted">For privacy, age is requested only where the Client has enabled <strong>Ask for age</strong>.</p></div>
    <form method="post" action="/availability/requirements">{edit_hidden}<input type="hidden" name="csrf" value="{esc(context['csrf_token'])}">
    <div class="card"><h2>Who's coming and when?</h2><div class="grid"><div><label>Please enter the lead name</label><input name="lead_name" placeholder="NAME" required value="{esc(saved_lead_name)}"></div><div><label>Choose Element Type</label><select id="requirements-element-type" name="element_type" required>{type_options}</select></div><div><label>Arrival</label><input id="requirements-arrival" type="date" name="arrival" required value="{esc(saved_arrival)}"></div><div><label>Departure</label><input id="requirements-departure" type="date" name="departure" required value="{esc(saved_departure)}"></div>'''
    person_map = _person_type_map(database, cid)
    for p in people_rows:
        pid = int(p['id']); saved = saved_people.get(pid, {'quantity': 0, 'ages': []}); qty = int(saved['quantity'])
        body += f'<div class="requirement-person" data-person="{pid}"><label>{esc(p["name"])}</label><input class="person-qty" data-person="{pid}" data-ask-age="{1 if int(p["ask_age"] or 0) else 0}" type="number" min="0" max="99" name="person_{pid}" value="{qty}">'
        if int(p['ask_age'] or 0):
            body += f'<div class="age-fields" id="ages-{pid}" data-existing="{esc(json.dumps(saved["ages"]))}"></div>'
        body += '</div>'
    body += '</div><p id="requirements-type-note" class="muted">Select an Element Type to show only the relevant requirements.</p></div>'
    if addon_rows:
        body += '<div class="card"><h2>Must-have requirements</h2><p class="muted">Only Features or Extras marked “Ask before Availability” and usable by the chosen Element Type are shown.</p><div class="grid">'
        for a in addon_rows:
            aid = int(a['id']); qty = int(saved_addons.get(aid, 0))
            body += f'<div><label>{esc(a["name"])}</label><input type="number" min="0" max="99" name="addon_{aid}" value="{qty}"><small class="muted">0 = not required</small></div>'
        body += '</div></div>'
    map_json = json.dumps(person_map, ensure_ascii=False).replace('</', '<\\/')
    body += f'''<p><button type="submit">SEARCH AVAILABILITY</button></p></form><script>(()=>{{const arr=document.getElementById('requirements-arrival'),dep=document.getElementById('requirements-departure'),type=document.getElementById('requirements-element-type'),personMap={map_json};const next=(iso)=>{{const d=new Date(iso+'T12:00:00');d.setDate(d.getDate()+1);return d.toISOString().slice(0,10)}};if(arr&&dep)arr.addEventListener('change',()=>{{if(!arr.value)return;const n=next(arr.value);dep.min=n;dep.value=n;}});function draw(input){{if(input.dataset.askAge!=='1')return;const box=document.getElementById('ages-'+input.dataset.person);if(!box)return;const qty=Math.max(0,Number(input.value||0));let existing=[];try{{existing=JSON.parse(box.dataset.existing||'[]')}}catch(e){{}}const current=[...box.querySelectorAll('input')].map(x=>x.value);box.innerHTML='';for(let i=0;i<qty;i++){{const label=document.createElement('label');label.textContent='Age at arrival — '+(i+1);const age=document.createElement('input');age.type='number';age.min='0';age.max='120';age.required=true;age.name='age_'+input.dataset.person+'_'+(i+1);age.value=current[i]??existing[i]??'';box.append(label,age);}}box.dataset.existing='[]';}}function refreshPeople(){{const chosen=type?type.value:'',allowed=new Set((personMap[chosen]||[]).map(String));document.querySelectorAll('.requirement-person').forEach(w=>{{const show=!!chosen&&allowed.has(w.dataset.person);w.style.display=show?'block':'none';w.querySelectorAll('input').forEach(i=>i.disabled=!show);}});const note=document.getElementById('requirements-type-note');if(note)note.style.display=chosen?'none':'block';}}document.querySelectorAll('.person-qty').forEach(i=>{{draw(i);i.addEventListener('input',()=>draw(i));}});if(type)type.addEventListener('change',refreshPeople);refreshPeople();}})();</script>'''
    return layout('Booking requirements', body, context)


def _element_reasons(database, cid, year, element, people, addons):
    reasons = []
    total = sum(int(v.get('quantity', 0)) for v in people.values())
    occupancy = one(database, 'SELECT max_total FROM setup_occupancy WHERE company_id=? AND year=? AND element_id=?', (cid, year, int(element['id'])))
    if occupancy is None:
        reasons.append('occupancy setup incomplete')
    elif total > int(occupancy['max_total']):
        reasons.append(f'maximum occupancy {int(occupancy["max_total"])}')
    limits = rows(database, '''SELECT l.person_type_id,l.min_count,l.max_count,p.name FROM setup_person_limits l
        LEFT JOIN setup_person_types p ON p.id=l.person_type_id AND p.company_id=l.company_id
        WHERE l.company_id=? AND l.year=? AND l.element_id=?''', (cid, year, int(element['id'])))
    for limit in limits:
        pid = int(limit['person_type_id']); qty = int(people.get(pid, {}).get('quantity', 0)); minimum = int(limit['min_count'] or 0); maximum = int(limit['max_count']); name = str(limit['name'] or 'Person type')
        if qty < minimum:
            reasons.append(f'{name} minimum {minimum}')
        elif qty > maximum:
            reasons.append(f'{name} not allowed' if maximum == 0 else f'{name} max {maximum}')
    for aid, qty in addons.items():
        qty = int(qty)
        if qty <= 0: continue
        addon = one(database, 'SELECT name FROM setup_addons WHERE company_id=? AND id=?', (cid, aid)); rule = _addon_rule(database, cid, year, element, aid); name = str(addon['name']) if addon else 'Requirement'
        if not rule['allowed']: reasons.append(f'no {name}')
        elif rule['max'] is not None and qty > int(rule['max']): reasons.append(f'{name} max {int(rule["max"])}')
    return reasons


def _snapshot_hold_requirements(database, cid, token, hold_id):
    with database.connect() as c:
        c.execute('DELETE FROM hold_requirement_people WHERE hold_id=?', (hold_id,)); c.execute('DELETE FROM hold_requirement_addons WHERE hold_id=?', (hold_id,))
        c.execute('''INSERT INTO hold_requirement_people(hold_id,company_id,person_type_id,quantity,ages_json)
                     SELECT ?,company_id,person_type_id,quantity,ages_json FROM booking_requirement_people
                     WHERE company_id=? AND session_token=?''', (hold_id, cid, token))
        c.execute('''INSERT INTO hold_requirement_addons(hold_id,company_id,addon_id,quantity)
                     SELECT ?,company_id,addon_id,quantity FROM booking_requirement_addons
                     WHERE company_id=? AND session_token=?''', (hold_id, cid, token))


def register_booking_requirement_routes(app):
    database = app.state.database
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, 'path', None) != '/availability/hold']

    @app.get('/availability/start', response_class=HTMLResponse)
    def requirements_start(request: Request):
        context, cid = _session_context(database, request); token = request.cookies.get(COOKIE_NAME, ''); raw_edit = request.query_params.get('edit_hold', ''); edit_hold = int(raw_edit) if raw_edit.isdigit() and int(raw_edit) > 0 else 0
        if edit_hold and not _load_hold_requirements_into_working(database, cid, token, edit_hold): edit_hold = 0
        selected_element_type = str(request.query_params.get('element_type', '') or '').strip()
        return _requirements_page(database, context, cid, token, edit_hold=edit_hold, selected_element_type=selected_element_type)

    @app.post('/setup/person-types/age-toggle')
    async def person_age_toggle(request: Request):
        context = context_for(database, request); cid = int(working_company(context)); data = await form_data(request); require_csrf(context, data); pid = int(data.get('person_type_id', '0') or 0)
        with database.connect() as c:
            row = c.execute('SELECT ask_age FROM setup_person_types WHERE company_id=? AND id=?', (cid, pid)).fetchone()
            if row is None: raise HTTPException(status_code=404, detail='Person Type not found')
            old = int(row['ask_age'] or 0); new = 0 if old else 1; c.execute('UPDATE setup_person_types SET ask_age=? WHERE company_id=? AND id=?', (new, cid, pid))
        audit(database, context, cid, 'PERSON_TYPE_AGE_QUESTION_CHANGED', 'person_type', pid, {'ask_age': old}, {'ask_age': new}); return RedirectResponse('/setup/person-types', 303)

    @app.post('/setup/addons/requirement-toggle')
    async def addon_requirement_toggle(request: Request):
        context = context_for(database, request); cid = int(working_company(context)); data = await form_data(request); require_csrf(context, data); aid = int(data.get('addon_id', '0') or 0)
        with database.connect() as c:
            row = c.execute('SELECT ask_before_availability FROM setup_addons WHERE company_id=? AND id=?', (cid, aid)).fetchone()
            if row is None: raise HTTPException(status_code=404, detail='Feature / Extra not found')
            old = int(row['ask_before_availability'] or 0); new = 0 if old else 1; c.execute('UPDATE setup_addons SET ask_before_availability=? WHERE company_id=? AND id=?', (new, cid, aid))
        audit(database, context, cid, 'ADDON_AVAILABILITY_QUESTION_CHANGED', 'addon', aid, {'ask_before_availability': old}, {'ask_before_availability': new}); return RedirectResponse('/setup/addons', 303)