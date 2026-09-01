from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .app import COOKIE_NAME, esc, form_data, layout
from .database import iso_now
from .setup015_calculator import _addon_rule
from .setup015_catalogue import setup_nav
from .setup015_core import audit, context_for, require_csrf, rows, working_company

HOLD_MINUTES = 10
HOLD_GRACE_MINUTES = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS element_closures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    element_id INTEGER NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_by_user_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(element_id) REFERENCES setup_elements(id)
);
CREATE INDEX IF NOT EXISTS idx_element_closures_dates
ON element_closures(company_id, element_id, start_date, end_date);

CREATE TABLE IF NOT EXISTS element_holds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    element_id INTEGER NOT NULL,
    session_token TEXT NOT NULL,
    holder_user_id INTEGER,
    arrival_date TEXT NOT NULL,
    departure_date TEXT NOT NULL,
    renewal_required_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(element_id) REFERENCES setup_elements(id),
    UNIQUE(company_id, element_id, session_token)
);
CREATE INDEX IF NOT EXISTS idx_element_holds_dates
ON element_holds(company_id, element_id, arrival_date, departure_date, expires_at);
"""


def initialise_availability(database) -> None:
    with database.connect() as c:
        c.executescript(SCHEMA)


def _parse_day(value: str) -> date:
    return date.fromisoformat(value)


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _session_company(database, request: Request):
    context = database.session_context(request.cookies.get(COOKIE_NAME))
    if context is None:
        raise HTTPException(status_code=401, detail='Login required')
    company_id = context['acting_company_id'] if context['role'] == 'supervisor' else context['company_id']
    if not company_id:
        raise HTTPException(status_code=403, detail='No Client selected')
    return context, int(company_id)


def operating_window(database, company_id: int, year: int) -> tuple[date, date] | None:
    with database.connect() as c:
        row = c.execute('SELECT MIN(start_date) AS first_day, MAX(end_date) AS last_day FROM setup_seasons WHERE company_id=? AND year=?', (company_id, year)).fetchone()
    if not row or not row['first_day'] or not row['last_day']:
        return None
    return _parse_day(row['first_day']), _parse_day(row['last_day']) + timedelta(days=1)


def _purge_expired_holds(connection) -> None:
    expired = [int(r['id']) for r in connection.execute('SELECT id FROM element_holds WHERE expires_at<=?', (iso_now(),)).fetchall()]
    for hold_id in expired:
        connection.execute('DELETE FROM hold_requirement_people WHERE hold_id=?', (hold_id,))
        connection.execute('DELETE FROM hold_requirement_addons WHERE hold_id=?', (hold_id,))
    if expired:
        connection.execute('DELETE FROM element_holds WHERE expires_at<=?', (iso_now(),))


def _booking_conflict(connection, company_id: int, element_id: int, start: str, end: str, exclude_booking_id: int | None = None):
    sql = '''
        SELECT b.id,b.reference,b.status,be.arrival_date,be.departure_date
        FROM booking_elements be
        JOIN bookings b ON b.id=be.booking_id AND b.company_id=be.company_id
        WHERE be.company_id=? AND be.element_id=?
          AND b.status<>'cancelled'
          AND date(be.arrival_date)<date(?) AND date(be.departure_date)>date(?)
    '''
    params: list[Any] = [company_id, element_id, end, start]
    if exclude_booking_id is not None:
        sql += ' AND b.id<>?'; params.append(exclude_booking_id)
    sql += ' ORDER BY be.arrival_date LIMIT 1'
    return connection.execute(sql, params).fetchone()


def _closure_conflict(connection, company_id: int, element_id: int, start: str, end: str, exclude_closure_id: int | None = None):
    sql = '''SELECT * FROM element_closures WHERE company_id=? AND element_id=?
             AND date(start_date)<date(?) AND date(end_date)>date(?)'''
    params: list[Any] = [company_id, element_id, end, start]
    if exclude_closure_id is not None:
        sql += ' AND id<>?'; params.append(exclude_closure_id)
    sql += ' ORDER BY start_date LIMIT 1'
    return connection.execute(sql, params).fetchone()


def availability_state(database, company_id: int, element_id: int, arrival: str, departure: str, *, session_token: str = '', exclude_booking_id: int | None = None) -> dict[str, Any]:
    try:
        start, end = _parse_day(arrival), _parse_day(departure)
    except ValueError:
        return {'available': False, 'state': 'INVALID', 'reason': 'Enter valid arrival and departure dates.'}
    if end <= start:
        return {'available': False, 'state': 'INVALID', 'reason': 'Departure must be after arrival.'}
    if start.year != (end - timedelta(days=1)).year:
        return {'available': False, 'state': 'OUT_OF_SEASON', 'reason': 'The stay must remain within one pricing year.'}
    window = operating_window(database, company_id, start.year)
    if window is None:
        return {'available': False, 'state': 'OUT_OF_SEASON', 'reason': 'No operating season is configured for these dates.'}
    open_start, open_end = window
    if start < open_start or end > open_end:
        return {'available': False, 'state': 'OUT_OF_SEASON', 'reason': f'Element is open from {open_start.isoformat()} to {(open_end-timedelta(days=1)).isoformat()}.'}
    with database.connect() as c:
        element = c.execute('SELECT * FROM setup_elements WHERE id=? AND company_id=?', (element_id, company_id)).fetchone()
        if element is None or not int(element['active']):
            return {'available': False, 'state': 'INACTIVE', 'reason': 'Element is inactive.'}
        closed = _closure_conflict(c, company_id, element_id, arrival, departure)
        if closed:
            return {'available': False, 'state': 'CLOSED', 'reason': str(closed['reason'] or 'Closed'), 'closure_id': int(closed['id'])}
        booked = _booking_conflict(c, company_id, element_id, arrival, departure, exclude_booking_id)
        if booked:
            return {'available': False, 'state': 'BOOKED', 'reason': f"Booked: {booked['reference']}", 'booking_id': int(booked['id']), 'booking_reference': str(booked['reference'])}
        _purge_expired_holds(c)
        held = c.execute('''SELECT * FROM element_holds WHERE company_id=? AND element_id=?
                            AND date(arrival_date)<date(?) AND date(departure_date)>date(?)
                            ORDER BY expires_at DESC LIMIT 1''', (company_id, element_id, departure, arrival)).fetchone()
        if held:
            own = bool(session_token and str(held['session_token']) == session_token)
            return {'available': own, 'state': 'HELD_BY_YOU' if own else 'HELD', 'reason': 'Temporarily held' if not own else 'Held in your basket', 'hold_id': int(held['id']), 'expires_at': str(held['expires_at']), 'renewal_required_at': str(held['renewal_required_at'])}
    return {'available': True, 'state': 'AVAILABLE', 'reason': ''}


def available_elements(database, company_id: int, element_type: str, arrival: str, departure: str, *, session_token: str = '') -> list[dict[str, Any]]:
    try:
        year = _parse_day(arrival).year
    except ValueError:
        return []
    result: list[dict[str, Any]] = []
    for element in rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1 AND element_type=? ORDER BY name COLLATE NOCASE', (company_id, element_type)):
        state = availability_state(database, company_id, int(element['id']), arrival, departure, session_token=session_token)
        if not state['available']:
            continue
        addons = []
        for addon in rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (company_id,)):
            rule = _addon_rule(database, company_id, year, element, int(addon['id']))
            addons.append({'id': int(addon['id']), 'name': str(addon['name']), 'available': bool(rule['allowed'])})
        result.append({'id': int(element['id']), 'name': str(element['name']), 'element_type': str(element['element_type']), 'state': state['state'], 'addons': addons})
    return result


def create_or_replace_hold(database, context, company_id: int, session_token: str, element_id: int, arrival: str, departure: str) -> dict[str, Any]:
    state = availability_state(database, company_id, element_id, arrival, departure, session_token=session_token)
    if not state['available']:
        raise ValueError(state['reason'])
    now = _now(); prompt = now + timedelta(minutes=HOLD_MINUTES); expires = prompt + timedelta(minutes=HOLD_GRACE_MINUTES)
    with database.connect() as c:
        _purge_expired_holds(c)
        conflict = c.execute('''SELECT id FROM element_holds WHERE company_id=? AND element_id=? AND session_token<>?
                                AND date(arrival_date)<date(?) AND date(departure_date)>date(?) AND expires_at>? LIMIT 1''',
                             (company_id, element_id, session_token, departure, arrival, iso_now())).fetchone()
        if conflict or _booking_conflict(c, company_id, element_id, arrival, departure) or _closure_conflict(c, company_id, element_id, arrival, departure):
            raise ValueError('That Element has just become unavailable. Please choose another.')
        existing = c.execute('SELECT * FROM element_holds WHERE company_id=? AND element_id=? AND session_token=?', (company_id, element_id, session_token)).fetchone()
        if existing:
            hold_id = int(existing['id'])
            c.execute('UPDATE element_holds SET arrival_date=?,departure_date=?,renewal_required_at=?,expires_at=?,updated_at=? WHERE id=?',
                      (arrival, departure, prompt.isoformat(timespec='seconds'), expires.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'), hold_id))
            before = dict(existing)
        else:
            hold_id = int(c.execute('''INSERT INTO element_holds(company_id,element_id,session_token,holder_user_id,arrival_date,departure_date,renewal_required_at,expires_at,created_at,updated_at)
                                      VALUES (?,?,?,?,?,?,?,?,?,?)''',
                                    (company_id, element_id, session_token, context['user_id'], arrival, departure, prompt.isoformat(timespec='seconds'), expires.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'))).lastrowid)
            before = None
    audit(database, context, company_id, 'ELEMENT_HOLD_SAVED', 'element_hold', hold_id, before, {'element_id': element_id, 'arrival_date': arrival, 'departure_date': departure, 'renewal_required_at': prompt.isoformat(timespec='seconds'), 'expires_at': expires.isoformat(timespec='seconds')})
    return {'id': hold_id, 'renewal_required_at': prompt.isoformat(timespec='seconds'), 'expires_at': expires.isoformat(timespec='seconds')}


def register_availability_routes(app) -> None:
    database = app.state.database

    @app.get('/availability/search')
    def search_availability(request: Request, element_type: str, arrival: str, departure: str):
        context, cid = _session_company(database, request)
        return JSONResponse({'element_type': element_type, 'arrival': arrival, 'departure': departure, 'elements': available_elements(database, cid, element_type, arrival, departure, session_token=request.cookies.get(COOKIE_NAME, ''))})

    @app.post('/availability/hold')
    async def hold_element(request: Request):
        context, cid = _session_company(database, request); data = await form_data(request); require_csrf(context, data)
        try:
            element_id = int(data.get('element_id', ''))
            hold = create_or_replace_hold(database, context, cid, request.cookies.get(COOKIE_NAME, ''), element_id, data.get('arrival_date', ''), data.get('departure_date', ''))
        except (TypeError, ValueError) as exc:
            return JSONResponse({'ok': False, 'error': str(exc)}, status_code=409)
        return JSONResponse({'ok': True, 'hold': hold})

    @app.get('/availability/holds')
    def hold_status(request: Request):
        context, cid = _session_company(database, request); token = request.cookies.get(COOKIE_NAME, '')
        now = _now()
        with database.connect() as c:
            _purge_expired_holds(c)
            hold_rows = c.execute('''SELECT h.*,e.name AS element_name FROM element_holds h JOIN setup_elements e ON e.id=h.element_id
                                     WHERE h.company_id=? AND h.session_token=? ORDER BY e.name''', (cid, token)).fetchall()
        holds = []
        for h in hold_rows:
            holds.append({'id': int(h['id']), 'element_id': int(h['element_id']), 'element_name': str(h['element_name']), 'arrival_date': str(h['arrival_date']), 'departure_date': str(h['departure_date']), 'renewal_required_at': str(h['renewal_required_at']), 'expires_at': str(h['expires_at']), 'needs_confirmation': now >= _utc(str(h['renewal_required_at']))})
        return JSONResponse({'holds': holds})

    @app.post('/availability/holds/renew')
    async def renew_holds(request: Request):
        context, cid = _session_company(database, request); data = await form_data(request); require_csrf(context, data); token = request.cookies.get(COOKIE_NAME, '')
        now = _now(); prompt = now + timedelta(minutes=HOLD_MINUTES); expires = prompt + timedelta(minutes=HOLD_GRACE_MINUTES)
        with database.connect() as c:
            _purge_expired_holds(c)
            held = c.execute('SELECT * FROM element_holds WHERE company_id=? AND session_token=?', (cid, token)).fetchall()
            c.execute('UPDATE element_holds SET renewal_required_at=?,expires_at=?,updated_at=? WHERE company_id=? AND session_token=?', (prompt.isoformat(timespec='seconds'), expires.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'), cid, token))
        audit(database, context, cid, 'ELEMENT_HOLDS_RENEWED', 'element_hold', token[:12], {'count': len(held)}, {'count': len(held), 'expires_at': expires.isoformat(timespec='seconds')})
        return JSONResponse({'ok': True, 'count': len(held), 'renewal_required_at': prompt.isoformat(timespec='seconds'), 'expires_at': expires.isoformat(timespec='seconds')})

    @app.post('/availability/holds/release')
    async def release_holds(request: Request):
        context, cid = _session_company(database, request); data = await form_data(request); require_csrf(context, data); token = request.cookies.get(COOKIE_NAME, '')
        with database.connect() as c:
            held = c.execute('SELECT * FROM element_holds WHERE company_id=? AND session_token=?', (cid, token)).fetchall()
            for hold in held:
                hold_id = int(hold['id'])
                c.execute('DELETE FROM hold_requirement_people WHERE hold_id=?', (hold_id,))
                c.execute('DELETE FROM hold_requirement_addons WHERE hold_id=?', (hold_id,))
            c.execute('DELETE FROM element_holds WHERE company_id=? AND session_token=?', (cid, token))
        audit(database, context, cid, 'ELEMENT_HOLDS_RELEASED', 'element_hold', token[:12], {'count': len(held)}, {'count': 0})
        return JSONResponse({'ok': True, 'released': len(held)})

    @app.get('/setup/elements/availability', response_class=HTMLResponse)
    def element_availability_page(request: Request, element_id: int, edit: int = 0, message: str = ''):
        context = context_for(database, request); cid = working_company(context)
        element_rows = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND id=?', (cid, element_id))
        if not element_rows:
            return HTMLResponse(layout('Element Availability', f'<h1>Element not found</h1>{setup_nav()}', context), 404)
        element = element_rows[0]; closure_rows = rows(database, 'SELECT * FROM element_closures WHERE company_id=? AND element_id=? ORDER BY start_date', (cid, element_id))
        current = next((r for r in closure_rows if int(r['id']) == int(edit or 0)), None)
        body = f'<h1>{esc(element["name"])} — Availability</h1>{setup_nav()}'
        if message: body += f'<div class="error">{esc(message)}</div>'
        body += '<div class="card"><h2>Default availability</h2><p><strong>Available throughout the operating season.</strong></p><p class="muted">The operating season is taken from the earliest Season start date to the latest Season end date for each pricing year. Temporary exceptions are added below.</p></div>'
        body += f'''<div class="card"><h2>{'Edit' if current else 'Add'} closed period</h2><form method="post" action="/setup/elements/availability/save"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><input type="hidden" name="element_id" value="{element_id}"><input type="hidden" name="id" value="{int(current['id']) if current else ''}"><div class="grid"><div><label>Closed from</label><input type="date" name="start_date" value="{esc(current['start_date'] if current else '')}"></div><div><label>Reopens / departure boundary</label><input type="date" name="end_date" value="{esc(current['end_date'] if current else '')}"></div><div><label>Reason</label><input name="reason" value="{esc(current['reason'] if current else '')}" placeholder="e.g. Pitch damaged"></div></div><p><button>{'Save closed period' if current else 'Add closed period'}</button> <a class="button secondary" href="/setup/elements">Back to Elements</a></p></form></div>'''
        body += '<div class="card"><h2>Closed periods</h2><table><thead><tr><th>From</th><th>Reopens</th><th>Reason</th><th></th></tr></thead><tbody>'
        for r in closure_rows:
            body += f'''<tr><td>{esc(r['start_date'])}</td><td>{esc(r['end_date'])}</td><td>{esc(r['reason'])}</td><td><a href="/setup/elements/availability?element_id={element_id}&edit={int(r['id'])}">Edit</a> &nbsp; <form method="post" action="/setup/elements/availability/delete" style="display:inline"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><input type="hidden" name="element_id" value="{element_id}"><input type="hidden" name="id" value="{int(r['id'])}"><button class="secondary">Delete</button></form></td></tr>'''
        body += '</tbody></table></div>'
        return HTMLResponse(layout('Element Availability', body, context))

    @app.post('/setup/elements/availability/save')
    async def element_availability_save(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data)
        try:
            element_id = int(data.get('element_id', '')); closure_id = int(data.get('id', '')) if data.get('id', '').isdigit() else 0
            start, end = _parse_day(data.get('start_date', '')), _parse_day(data.get('end_date', ''))
        except (TypeError, ValueError):
            return RedirectResponse(f'/setup/elements/availability?element_id={data.get("element_id", "0")}&message=Enter+valid+closure+dates', 303)
        reason = data.get('reason', '').strip()
        if end <= start:
            return RedirectResponse(f'/setup/elements/availability?element_id={element_id}&message=Reopening+date+must+be+after+the+closure+start', 303)
        window = operating_window(database, cid, start.year)
        if start.year != (end - timedelta(days=1)).year or window is None or start < window[0] or end > window[1]:
            return RedirectResponse(f'/setup/elements/availability?element_id={element_id}&message=Closure+must+sit+inside+one+configured+operating+season+year', 303)
        with database.connect() as c:
            booked = _booking_conflict(c, cid, element_id, start.isoformat(), end.isoformat())
            if booked:
                return RedirectResponse(f'/setup/elements/availability?element_id={element_id}&message=Cannot+close+these+dates%3A+booking+{esc(booked["reference"])}+already+uses+this+Element', 303)
            overlap = _closure_conflict(c, cid, element_id, start.isoformat(), end.isoformat(), closure_id or None)
            if overlap:
                return RedirectResponse(f'/setup/elements/availability?element_id={element_id}&message=That+period+overlaps+an+existing+closure', 303)
            before = c.execute('SELECT * FROM element_closures WHERE id=? AND company_id=? AND element_id=?', (closure_id, cid, element_id)).fetchone() if closure_id else None
            now = iso_now()
            if closure_id:
                if before is None: return RedirectResponse(f'/setup/elements/availability?element_id={element_id}&message=Closure+not+found', 303)
                c.execute('UPDATE element_closures SET start_date=?,end_date=?,reason=?,updated_at=? WHERE id=? AND company_id=?', (start.isoformat(), end.isoformat(), reason, now, closure_id, cid)); saved_id = closure_id
            else:
                saved_id = int(c.execute('INSERT INTO element_closures(company_id,element_id,start_date,end_date,reason,created_by_user_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)', (cid, element_id, start.isoformat(), end.isoformat(), reason, context['user_id'], now, now)).lastrowid)
        audit(database, context, cid, 'ELEMENT_CLOSURE_SAVED', 'element_closure', saved_id, dict(before) if before else None, {'element_id': element_id, 'start_date': start.isoformat(), 'end_date': end.isoformat(), 'reason': reason})
        return RedirectResponse(f'/setup/elements/availability?element_id={element_id}', 303)

    @app.post('/setup/elements/availability/delete')
    async def element_availability_delete(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data)
        try: element_id = int(data.get('element_id', '')); closure_id = int(data.get('id', ''))
        except ValueError: return RedirectResponse('/setup/elements', 303)
        with database.connect() as c:
            before = c.execute('SELECT * FROM element_closures WHERE id=? AND company_id=? AND element_id=?', (closure_id, cid, element_id)).fetchone()
            if before: c.execute('DELETE FROM element_closures WHERE id=? AND company_id=?', (closure_id, cid))
        if before: audit(database, context, cid, 'ELEMENT_CLOSURE_DELETED', 'element_closure', closure_id, dict(before), None)
        return RedirectResponse(f'/setup/elements/availability?element_id={element_id}', 303)
