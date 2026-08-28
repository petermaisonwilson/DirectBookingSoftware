from __future__ import annotations

import json
from datetime import date

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .app import COOKIE_NAME, esc, form_data, layout
from .setup015_calculator import _addon_rule
from .setup015_core import audit, context_for, one, require_csrf, rows, working_company
from .webv1_availability import create_or_replace_hold
from .webv1_ordering import person_type_rows


REQUIREMENTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS booking_requirement_sessions (
    session_token TEXT NOT NULL,
    company_id INTEGER NOT NULL,
    ready INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(session_token, company_id)
);
CREATE TABLE IF NOT EXISTS booking_requirement_people (
    session_token TEXT NOT NULL,
    company_id INTEGER NOT NULL,
    person_type_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    ages_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(session_token, company_id, person_type_id)
);
CREATE TABLE IF NOT EXISTS booking_requirement_addons (
    session_token TEXT NOT NULL,
    company_id INTEGER NOT NULL,
    addon_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(session_token, company_id, addon_id)
);
CREATE TABLE IF NOT EXISTS hold_requirement_people (
    hold_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    person_type_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    ages_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY(hold_id, person_type_id)
);
CREATE TABLE IF NOT EXISTS hold_requirement_addons (
    hold_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    addon_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(hold_id, addon_id)
);
"""


def _ensure_column(connection, table: str, column: str, definition: str) -> None:
    names = {str(r['name']) for r in connection.execute(f'PRAGMA table_info({table})').fetchall()}
    if column not in names:
        connection.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')


def initialise_booking_requirements(database) -> None:
    with database.connect() as c:
        c.executescript(REQUIREMENTS_SCHEMA)
        _ensure_column(c, 'setup_person_types', 'ask_age', 'INTEGER NOT NULL DEFAULT 0')
        _ensure_column(c, 'setup_addons', 'ask_before_availability', 'INTEGER NOT NULL DEFAULT 0')
        _ensure_column(c, 'booking_requirement_sessions', 'arrival_date', "TEXT NOT NULL DEFAULT ''")
        _ensure_column(c, 'booking_requirement_sessions', 'departure_date', "TEXT NOT NULL DEFAULT ''")
        c.execute('DELETE FROM hold_requirement_people WHERE hold_id NOT IN (SELECT id FROM element_holds)')
        c.execute('DELETE FROM hold_requirement_addons WHERE hold_id NOT IN (SELECT id FROM element_holds)')


def _session_context(database, request: Request):
    context = database.session_context(request.cookies.get(COOKIE_NAME))
    if context is None:
        raise HTTPException(status_code=401, detail='Login required')
    cid = context['acting_company_id'] if context['role'] == 'supervisor' else context['company_id']
    if not cid:
        raise HTTPException(status_code=403, detail='Select a Client first')
    return context, int(cid)


def _saved_requirements(database, cid: int, token: str):
    people = {int(r['person_type_id']): {'quantity': int(r['quantity']), 'ages': json.loads(r['ages_json'] or '[]')}
              for r in rows(database, 'SELECT * FROM booking_requirement_people WHERE company_id=? AND session_token=?', (cid, token))}
    addons = {int(r['addon_id']): int(r['quantity'])
              for r in rows(database, 'SELECT * FROM booking_requirement_addons WHERE company_id=? AND session_token=?', (cid, token))}
    saved = one(database, 'SELECT ready,arrival_date,departure_date FROM booking_requirement_sessions WHERE company_id=? AND session_token=?', (cid, token))
    ready = bool(saved and int(saved['ready'] or 0))
    arrival = str(saved['arrival_date'] or '') if saved else ''
    departure = str(saved['departure_date'] or '') if saved else ''
    return people, addons, ready, arrival, departure


def _fmt_user_date(value: str) -> str:
    try:
        return date.fromisoformat(value).strftime('%d/%m/%Y')
    except (TypeError, ValueError):
        return value or ''


def _requirements_page(database, context, cid: int, token: str, message: str = '') -> str:
    people_rows = person_type_rows(database, cid, active_only=True)
    addon_rows = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND active=1 AND ask_before_availability=1 ORDER BY name COLLATE NOCASE', (cid,))
    saved_people, saved_addons, _, saved_arrival, saved_departure = _saved_requirements(database, cid, token)
    error = f'<div class="error">{esc(message)}</div>' if message else ''
    body = f'''<h1>Booking requirements</h1>{error}
    <div class="card"><p>Tell us who is coming, when they are staying and anything that the Element <strong>must</strong> provide. We use this only to prevent you choosing an unsuitable Element.</p>
    <p class="muted">For privacy, age is requested only for Person Types where the Client has enabled <strong>Ask for age</strong>. Date of birth is not collected.</p></div>
    <form method="post" action="/availability/requirements">
    <input type="hidden" name="csrf" value="{esc(context['csrf_token'])}">
    <div class="card"><h2>Who's coming and when?</h2><div class="grid">
      <div><label>Arrival</label><input type="date" name="arrival" required value="{esc(saved_arrival)}"></div>
      <div><label>Departure</label><input type="date" name="departure" required value="{esc(saved_departure)}"></div>'''
    for p in people_rows:
        pid = int(p['id']); saved = saved_people.get(pid, {'quantity': 0, 'ages': []}); qty = int(saved['quantity'])
        body += f'<div class="requirement-person"><label>{esc(p["name"])}</label><input class="person-qty" data-person="{pid}" data-ask-age="{1 if int(p["ask_age"] or 0) else 0}" type="number" min="0" max="99" name="person_{pid}" value="{qty}">'
        if int(p['ask_age'] or 0):
            body += f'<div class="age-fields" id="ages-{pid}" data-existing="{esc(json.dumps(saved["ages"]))}"></div>'
        body += '</div>'
    body += '</div></div>'
    if addon_rows:
        body += '<div class="card"><h2>Must-have requirements</h2><p class="muted">Only Features or Extras marked “Ask before Availability” appear here.</p><div class="grid">'
        for a in addon_rows:
            aid = int(a['id']); qty = int(saved_addons.get(aid, 0))
            body += f'<div><label>{esc(a["name"])}</label><input type="number" min="0" max="99" name="addon_{aid}" value="{qty}"><small class="muted">0 = not required</small></div>'
        body += '</div></div>'
    body += '''<p><button type="submit">SEARCH AVAILABILITY</button></p></form>
    <script>
    (()=>{
      function draw(input){
        if(input.dataset.askAge!=='1') return;
        const box=document.getElementById('ages-'+input.dataset.person); if(!box) return;
        const qty=Math.max(0,Number(input.value||0));
        let existing=[]; try{existing=JSON.parse(box.dataset.existing||'[]')}catch(e){}
        const current=[...box.querySelectorAll('input')].map(x=>x.value);
        box.innerHTML='';
        for(let i=0;i<qty;i++){
          const label=document.createElement('label'); label.textContent='Age at arrival — '+(i+1);
          const age=document.createElement('input'); age.type='number'; age.min='0'; age.max='120'; age.required=true;
          age.name='age_'+input.dataset.person+'_'+(i+1); age.value=current[i]??existing[i]??'';
          box.append(label,age);
        }
        box.dataset.existing='[]';
      }
      document.querySelectorAll('.person-qty').forEach(i=>{draw(i);i.addEventListener('input',()=>draw(i));});
    })();
    </script>'''
    return layout('Booking requirements', body, context)


def _element_reasons(database, cid: int, year: int, element, people: dict, addons: dict) -> list[str]:
    reasons: list[str] = []
    total = sum(int(v.get('quantity', 0)) for v in people.values())
    occupancy = one(database, 'SELECT max_total FROM setup_occupancy WHERE company_id=? AND year=? AND element_id=?', (cid, year, int(element['id'])))
    if occupancy is None:
        reasons.append('occupancy setup incomplete')
    elif total > int(occupancy['max_total']):
        reasons.append(f'maximum occupancy {int(occupancy["max_total"])}')
    for pid, data in people.items():
        qty = int(data.get('quantity', 0))
        if qty <= 0:
            continue
        p = one(database, 'SELECT name FROM setup_person_types WHERE company_id=? AND id=?', (cid, pid))
        limit = one(database, 'SELECT max_count FROM setup_person_limits WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?', (cid, year, int(element['id']), pid))
        if limit is None:
            reasons.append(f'{str(p["name"]) if p else "Person type"} not configured')
        elif qty > int(limit['max_count']):
            name = str(p['name']) if p else 'Person type'
            reasons.append(f'{name} not allowed' if int(limit['max_count']) == 0 else f'{name} max {int(limit["max_count"])}')
    for aid, qty in addons.items():
        qty = int(qty)
        if qty <= 0:
            continue
        addon = one(database, 'SELECT name FROM setup_addons WHERE company_id=? AND id=?', (cid, aid))
        rule = _addon_rule(database, cid, year, element, aid)
        name = str(addon['name']) if addon else 'Requirement'
        if not rule['allowed']:
            reasons.append(f'no {name}')
        elif rule['max'] is not None and qty > int(rule['max']):
            reasons.append(f'{name} max {int(rule["max"])}')
    return reasons


def _snapshot_hold_requirements(database, cid: int, token: str, hold_id: int) -> None:
    with database.connect() as c:
        c.execute('DELETE FROM hold_requirement_people WHERE hold_id=?', (hold_id,))
        c.execute('DELETE FROM hold_requirement_addons WHERE hold_id=?', (hold_id,))
        c.execute('''INSERT INTO hold_requirement_people(hold_id,company_id,person_type_id,quantity,ages_json)
                     SELECT ?,company_id,person_type_id,quantity,ages_json FROM booking_requirement_people
                     WHERE company_id=? AND session_token=?''', (hold_id, cid, token))
        c.execute('''INSERT INTO hold_requirement_addons(hold_id,company_id,addon_id,quantity)
                     SELECT ?,company_id,addon_id,quantity FROM booking_requirement_addons
                     WHERE company_id=? AND session_token=?''', (hold_id, cid, token))


def _held_requirement_html(database, cid: int, token: str) -> str:
    hold_rows = rows(database, '''SELECT h.id,h.arrival_date,h.departure_date,e.name AS element_name
                                  FROM element_holds h JOIN setup_elements e ON e.id=h.element_id
                                  WHERE h.company_id=? AND h.session_token=? ORDER BY h.id''', (cid, token))
    if not hold_rows:
        return ''
    blocks = []
    for h in hold_rows:
        hid = int(h['id']); details = []
        for p in rows(database, '''SELECT hp.quantity,hp.ages_json,pt.name FROM hold_requirement_people hp
                                   LEFT JOIN setup_person_types pt ON pt.id=hp.person_type_id AND pt.company_id=hp.company_id
                                   WHERE hp.hold_id=? AND hp.quantity>0 ORDER BY pt.name''', (hid,)):
            label = f'{int(p["quantity"])} {str(p["name"] or "person")}'
            try: ages = json.loads(p['ages_json'] or '[]')
            except json.JSONDecodeError: ages = []
            if ages: label += ' (age' + ('s ' if len(ages) != 1 else ' ') + ', '.join(str(x) for x in ages) + ')'
            details.append(label)
        for a in rows(database, '''SELECT ha.quantity,sa.name FROM hold_requirement_addons ha
                                   LEFT JOIN setup_addons sa ON sa.id=ha.addon_id AND sa.company_id=ha.company_id
                                   WHERE ha.hold_id=? AND ha.quantity>0 ORDER BY sa.name''', (hid,)):
            details.append(f'{str(a["name"] or "Requirement")} {int(a["quantity"])}')
        detail_text = ' · '.join(esc(x) for x in details) or 'No special requirements'
        blocks.append(f'<div><strong>{esc(h["element_name"])}</strong> — {_fmt_user_date(str(h["arrival_date"]))} to {_fmt_user_date(str(h["departure_date"]))}<br><span class="muted">{detail_text}</span></div>')
    return '<div class="card held-requirements"><h3>Requirements by held item</h3>' + ''.join(blocks) + '</div>'


def register_booking_requirement_routes(app) -> None:
    database = app.state.database

    app.router.routes[:] = [r for r in app.router.routes if getattr(r, 'path', None) != '/availability/hold']

    @app.get('/availability/start', response_class=HTMLResponse)
    def requirements_start(request: Request):
        context, cid = _session_context(database, request)
        token = request.cookies.get(COOKIE_NAME, '')
        return _requirements_page(database, context, cid, token)

    @app.post('/availability/requirements')
    async def requirements_save(request: Request):
        context, cid = _session_context(database, request)
        token = request.cookies.get(COOKIE_NAME, '')
        data = await form_data(request)
        if data.get('csrf') != context['csrf_token']:
            raise HTTPException(status_code=403, detail='Invalid form token')
        arrival = str(data.get('arrival', '')).strip(); departure = str(data.get('departure', '')).strip()
        try:
            arrival_day = date.fromisoformat(arrival); departure_day = date.fromisoformat(departure)
        except ValueError:
            return HTMLResponse(_requirements_page(database, context, cid, token, 'Enter valid arrival and departure dates.'), 400)
        if departure_day <= arrival_day:
            return HTMLResponse(_requirements_page(database, context, cid, token, 'Departure must be after arrival.'), 400)
        if arrival_day.year != (departure_day.fromordinal(departure_day.toordinal()-1)).year:
            return HTMLResponse(_requirements_page(database, context, cid, token, 'The stay must remain within one pricing year.'), 400)
        people_rows = person_type_rows(database, cid, active_only=True)
        addon_rows = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND active=1 AND ask_before_availability=1 ORDER BY name', (cid,))
        parsed_people = []
        total = 0
        for p in people_rows:
            pid = int(p['id'])
            try: qty = max(0, int(data.get(f'person_{pid}', '0') or 0))
            except ValueError: return HTMLResponse(_requirements_page(database, context, cid, token, f'Enter a valid number for {p["name"]}.'), 400)
            total += qty; ages = []
            if int(p['ask_age'] or 0):
                for i in range(1, qty + 1):
                    raw = data.get(f'age_{pid}_{i}', '').strip()
                    try: age = int(raw)
                    except ValueError: return HTMLResponse(_requirements_page(database, context, cid, token, f'Enter the age at arrival for every {p["name"]}.'), 400)
                    if age < 0 or age > 120: return HTMLResponse(_requirements_page(database, context, cid, token, 'Age must be between 0 and 120.'), 400)
                    ages.append(age)
            parsed_people.append((pid, qty, json.dumps(ages)))
        if people_rows and total <= 0:
            return HTMLResponse(_requirements_page(database, context, cid, token, 'Enter at least one person.'), 400)
        parsed_addons = []
        for a in addon_rows:
            aid = int(a['id'])
            try: qty = max(0, int(data.get(f'addon_{aid}', '0') or 0))
            except ValueError: return HTMLResponse(_requirements_page(database, context, cid, token, f'Enter a valid quantity for {a["name"]}.'), 400)
            parsed_addons.append((aid, qty))
        with database.connect() as c:
            c.execute('DELETE FROM booking_requirement_people WHERE company_id=? AND session_token=?', (cid, token))
            c.execute('DELETE FROM booking_requirement_addons WHERE company_id=? AND session_token=?', (cid, token))
            for pid, qty, ages in parsed_people:
                c.execute('INSERT INTO booking_requirement_people(session_token,company_id,person_type_id,quantity,ages_json) VALUES (?,?,?,?,?)', (token, cid, pid, qty, ages))
            for aid, qty in parsed_addons:
                c.execute('INSERT INTO booking_requirement_addons(session_token,company_id,addon_id,quantity) VALUES (?,?,?,?)', (token, cid, aid, qty))
            c.execute('''INSERT INTO booking_requirement_sessions(session_token,company_id,ready,arrival_date,departure_date,updated_at)
                         VALUES (?,?,1,?,?,CURRENT_TIMESTAMP)
                         ON CONFLICT(session_token,company_id) DO UPDATE SET ready=1,arrival_date=excluded.arrival_date,departure_date=excluded.departure_date,updated_at=CURRENT_TIMESTAMP''',
                      (token, cid, arrival, departure))
        return RedirectResponse(f'/availability/calendar-v2?arrival={arrival}&departure={departure}&start={arrival}', 303)

    @app.post('/availability/search/change')
    async def requirements_change(request: Request):
        context, cid = _session_context(database, request); data = await form_data(request); require_csrf(context, data)
        token = request.cookies.get(COOKIE_NAME, '')
        with database.connect() as c:
            hold_ids = [int(r['id']) for r in c.execute('SELECT id FROM element_holds WHERE company_id=? AND session_token=?', (cid, token)).fetchall()]
            for hid in hold_ids:
                c.execute('DELETE FROM hold_requirement_people WHERE hold_id=?', (hid,))
                c.execute('DELETE FROM hold_requirement_addons WHERE hold_id=?', (hid,))
            c.execute('DELETE FROM element_holds WHERE company_id=? AND session_token=?', (cid, token))
        return RedirectResponse('/availability/start', 303)

    @app.post('/availability/search/add')
    async def requirements_add(request: Request):
        context, _ = _session_context(database, request); data = await form_data(request); require_csrf(context, data)
        return RedirectResponse('/availability/start', 303)

    @app.post('/availability/hold')
    async def hold_element(request: Request):
        context, cid = _session_context(database, request); data = await form_data(request); require_csrf(context, data)
        token = request.cookies.get(COOKIE_NAME, '')
        try:
            element_id = int(data.get('element_id', ''))
            hold = create_or_replace_hold(database, context, cid, token, element_id, data.get('arrival_date', ''), data.get('departure_date', ''))
            _snapshot_hold_requirements(database, cid, token, int(hold['id']))
        except (TypeError, ValueError) as exc:
            return JSONResponse({'ok': False, 'error': str(exc)}, status_code=409)
        return JSONResponse({'ok': True, 'hold': hold})

    @app.post('/setup/person-types/age-toggle')
    async def person_age_toggle(request: Request):
        context = context_for(database, request); cid = int(working_company(context)); data = await form_data(request); require_csrf(context, data)
        pid = int(data.get('person_type_id', '0') or 0)
        with database.connect() as c:
            row = c.execute('SELECT ask_age FROM setup_person_types WHERE company_id=? AND id=?', (cid, pid)).fetchone()
            if row is None: raise HTTPException(status_code=404, detail='Person Type not found')
            old = int(row['ask_age'] or 0); new = 0 if old else 1
            c.execute('UPDATE setup_person_types SET ask_age=? WHERE company_id=? AND id=?', (new, cid, pid))
        audit(database, context, cid, 'PERSON_TYPE_AGE_QUESTION_CHANGED', 'person_type', pid, {'ask_age': old}, {'ask_age': new})
        return RedirectResponse('/setup/person-types', 303)

    @app.post('/setup/addons/requirement-toggle')
    async def addon_requirement_toggle(request: Request):
        context = context_for(database, request); cid = int(working_company(context)); data = await form_data(request); require_csrf(context, data)
        aid = int(data.get('addon_id', '0') or 0)
        with database.connect() as c:
            row = c.execute('SELECT ask_before_availability FROM setup_addons WHERE company_id=? AND id=?', (cid, aid)).fetchone()
            if row is None: raise HTTPException(status_code=404, detail='Add-on not found')
            old = int(row['ask_before_availability'] or 0); new = 0 if old else 1
            c.execute('UPDATE setup_addons SET ask_before_availability=? WHERE company_id=? AND id=?', (new, cid, aid))
        audit(database, context, cid, 'ADDON_AVAILABILITY_QUESTION_CHANGED', 'addon', aid, {'ask_before_availability': old}, {'ask_before_availability': new})
        return RedirectResponse('/setup/addons', 303)


def install_booking_requirements(app) -> None:
    database = app.state.database

    @app.middleware('http')
    async def booking_requirements_ui(request, call_next):
        response = await call_next(request)
        if response.status_code >= 400 or 'text/html' not in response.headers.get('content-type', ''):
            return response
        path = request.url.path
        if path not in {'/setup/person-types', '/setup/addons', '/availability/calendar-v2'}:
            return response
        body = b''
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else str(chunk).encode('utf-8')
        text = body.decode('utf-8')
        context = database.session_context(request.cookies.get(COOKIE_NAME))
        if not context:
            return Response(content=text, status_code=response.status_code, media_type='text/html')
        cid = context['acting_company_id'] if context['role'] == 'supervisor' else context['company_id']
        if not cid:
            return Response(content=text, status_code=response.status_code, media_type='text/html')
        cid = int(cid); token = request.cookies.get(COOKIE_NAME, '')

        if path == '/setup/person-types':
            controls = '<div class="card"><h2>Age question</h2><p class="muted">Privacy-by-design: ask for age only where it is genuinely needed. Date of birth is not collected.</p><table><thead><tr><th>Person Type</th><th>Ask for age at arrival</th></tr></thead><tbody>'
            for p in person_type_rows(database, cid, active_only=True):
                controls += f'<tr><td>{esc(p["name"])}</td><td><form method="post" action="/setup/person-types/age-toggle"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="person_type_id" value="{int(p["id"])}"><button class="secondary">{"✓ Yes" if int(p["ask_age"] or 0) else "No"}</button></form></td></tr>'
            controls += '</tbody></table></div>'
            text = text.replace('<div class="card"><table>', controls + '<div class="card"><table>', 1)

        elif path == '/setup/addons':
            controls = '<div class="card"><h2>Ask before Availability</h2><p class="muted">Use this for any Feature or Extra that can make an Element unsuitable — for example Dogs, Electric Hook-up or Extra Vehicle. Optional Extras can remain Extras and still be asked before Availability.</p><table><thead><tr><th>Feature / Extra</th><th>Ask before Availability</th></tr></thead><tbody>'
            for a in rows(database, 'SELECT id,name,ask_before_availability FROM setup_addons WHERE company_id=? AND active=1 ORDER BY name', (cid,)):
                controls += f'<tr><td>{esc(a["name"])}</td><td><form method="post" action="/setup/addons/requirement-toggle"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="addon_id" value="{int(a["id"])}"><button class="secondary">{"✓ Yes" if int(a["ask_before_availability"] or 0) else "No"}</button></form></td></tr>'
            controls += '</tbody></table></div>'
            text = text.replace('<div class="card"><table>', controls + '<div class="card"><table>', 1)

        else:
            people, addons, ready, saved_arrival, saved_departure = _saved_requirements(database, cid, token)
            if not ready:
                return RedirectResponse('/availability/start', 303)
            summary = []
            for pid, data in people.items():
                qty = int(data.get('quantity', 0))
                if qty:
                    p = one(database, 'SELECT name,ask_age FROM setup_person_types WHERE company_id=? AND id=?', (cid, pid))
                    label = f'{qty} {str(p["name"]) if p else "person"}'
                    ages = data.get('ages', [])
                    if ages: label += ' (age' + ('s ' if len(ages) != 1 else ' ') + ', '.join(str(x) for x in ages) + ')'
                    summary.append(label)
            for aid, qty in addons.items():
                if int(qty):
                    a = one(database, 'SELECT name FROM setup_addons WHERE company_id=? AND id=?', (cid, aid))
                    summary.append(f'{str(a["name"]) if a else "Requirement"} {int(qty)}')
            summary_html = ' · '.join(esc(x) for x in summary) or 'No special requirements'
            date_html = f'{_fmt_user_date(saved_arrival)} to {_fmt_user_date(saved_departure)}' if saved_arrival and saved_departure else ''
            controls = f'''<form method="post" action="/availability/search/change" style="display:inline;margin-left:10px"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><button class="secondary">Change</button></form>
            <form method="post" action="/availability/search/add" style="display:inline;margin-left:6px"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><button class="secondary">Add</button></form>'''
            card = f'<div class="card requirement-summary"><strong>Your requirements:</strong> {esc(date_html)}' + (' · ' if date_html and summary_html else '') + summary_html + controls + '</div>'
            text = text.replace('<h1>Availability Calendar</h1>', '<h1>Availability Calendar</h1>' + card + _held_requirement_html(database, cid, token), 1)

            raw_day = request.query_params.get('arrival') or request.query_params.get('start') or saved_arrival or date.today().isoformat()
            try: year = date.fromisoformat(raw_day).year
            except ValueError: year = date.today().year
            unsuitable = {}
            for e in rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1', (cid,)):
                reasons = _element_reasons(database, cid, year, e, people, addons)
                if reasons: unsuitable[int(e['id'])] = reasons
            for eid, reasons in unsuitable.items():
                marker = f'<div class="cal-row element-row" data-element="{eid}"'
                replacement = marker.replace('class="cal-row element-row"', 'class="cal-row element-row party-unsuitable"')
                text = text.replace(marker, replacement, 1)
                reason_text = 'Not suitable: ' + ' · '.join(reasons)
                row_start = text.find(replacement)
                if row_start >= 0:
                    name_end = text.find('</div>', row_start)
                    if name_end >= 0:
                        text = text[:name_end] + f'<small class="party-reason">{esc(reason_text)}</small>' + text[name_end:]
            legend_marker = '<span class="legend mini available-key">Available</span>'
            text = text.replace(legend_marker, legend_marker + '<span class="legend mini party-unsuitable-key">Not suitable for your party</span>', 1)
            injection = '''<style id="booking-requirements-style">
              .party-unsuitable-key{background:#eadcf4}
              #calendar-scroll .element-row.party-unsuitable .cal-cell.available{background:#eadcf4 !important;pointer-events:none;cursor:not-allowed}
              #calendar-scroll .element-row.party-unsuitable .selection-action{display:none !important}
              .party-reason{display:block;color:#6d3f7c;font-size:10px;line-height:1.15;margin-top:2px}
              #calendar-scroll .element-row .selection-action:not([style*="grid-column"]){display:none !important}
              .held-requirements>div{margin-top:8px}
            </style>
            <script id="requirements-calendar-date-ui">(()=>{['arrival-date','departure-date'].forEach(id=>{const el=document.getElementById(id);if(el&&el.closest('div'))el.closest('div').style.display='none';});})();</script>'''
            text = text.replace('</body>', injection + '</body>', 1)

        headers = {k:v for k,v in response.headers.items() if k.lower() not in {'content-length','content-type'}}
        return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
