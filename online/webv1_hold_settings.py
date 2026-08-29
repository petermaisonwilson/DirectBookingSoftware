from __future__ import annotations

from datetime import timedelta

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .app import COOKIE_NAME, esc, form_data, layout
from .database import iso_now
from .setup015_core import audit, context_for, require_csrf, working_company
from . import webv1_availability as legacy

DEFAULT_HOLD_SECONDS = 600
DEFAULT_GRACE_SECONDS = 60
MIN_HOLD_SECONDS = 10
MAX_HOLD_SECONDS = 14400
MIN_GRACE_SECONDS = 5
MAX_GRACE_SECONDS = 1800

SCHEMA = """
CREATE TABLE IF NOT EXISTS company_hold_settings (
    company_id INTEGER PRIMARY KEY,
    hold_seconds INTEGER NOT NULL DEFAULT 600,
    grace_seconds INTEGER NOT NULL DEFAULT 60,
    updated_by_user_id INTEGER,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(company_id) REFERENCES companies(id)
);
"""


def initialise_hold_settings(database) -> None:
    with database.connect() as c:
        c.executescript(SCHEMA)
        now = iso_now()
        for row in c.execute('SELECT id FROM companies WHERE active=1').fetchall():
            c.execute(
                'INSERT OR IGNORE INTO company_hold_settings(company_id,hold_seconds,grace_seconds,updated_at) VALUES (?,?,?,?)',
                (int(row['id']), DEFAULT_HOLD_SECONDS, DEFAULT_GRACE_SECONDS, now),
            )


def hold_timing(database, company_id: int) -> tuple[int, int]:
    with database.connect() as c:
        row = c.execute('SELECT hold_seconds,grace_seconds FROM company_hold_settings WHERE company_id=?', (company_id,)).fetchone()
    if row is None:
        return DEFAULT_HOLD_SECONDS, DEFAULT_GRACE_SECONDS
    return int(row['hold_seconds']), int(row['grace_seconds'])


def _validate(raw: str, minimum: int, maximum: int, label: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f'{label} must be a whole number of seconds.')
    if value < minimum or value > maximum:
        raise ValueError(f'{label} must be between {minimum} and {maximum} seconds.')
    return value


def create_or_replace_hold(database, context, company_id: int, session_token: str, element_id: int, arrival: str, departure: str):
    # A hold already in this basket is immutable during normal ADD Element / NEW
    # BOOKING work. It can only be changed through the explicit basket EDIT route.
    with database.connect() as c:
        legacy._purge_expired_holds(c)
        existing = c.execute(
            'SELECT * FROM element_holds WHERE company_id=? AND element_id=? AND session_token=?',
            (company_id, element_id, session_token),
        ).fetchone()
    if existing is not None:
        raise ValueError('That Element is already held in your basket. Use EDIT in Booking in progress to change it.')

    state = legacy.availability_state(database, company_id, element_id, arrival, departure, session_token=session_token)
    if not state['available']:
        raise ValueError(state['reason'])
    hold_seconds, grace_seconds = hold_timing(database, company_id)
    now = legacy._now()
    prompt = now + timedelta(seconds=hold_seconds)
    expires = prompt + timedelta(seconds=grace_seconds)
    with database.connect() as c:
        legacy._purge_expired_holds(c)
        conflict = c.execute(
            '''SELECT id FROM element_holds WHERE company_id=? AND element_id=?
               AND date(arrival_date)<date(?) AND date(departure_date)>date(?) AND expires_at>? LIMIT 1''',
            (company_id, element_id, departure, arrival, iso_now()),
        ).fetchone()
        if conflict or legacy._booking_conflict(c, company_id, element_id, arrival, departure) or legacy._closure_conflict(c, company_id, element_id, arrival, departure):
            raise ValueError('That Element has just become unavailable. Please choose another.')
        hold_id = int(c.execute(
            '''INSERT INTO element_holds(company_id,element_id,session_token,holder_user_id,arrival_date,departure_date,renewal_required_at,expires_at,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (company_id, element_id, session_token, context['user_id'], arrival, departure, prompt.isoformat(timespec='seconds'), expires.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'), now.isoformat(timespec='seconds')),
        ).lastrowid)
    audit(database, context, company_id, 'ELEMENT_HOLD_SAVED', 'element_hold', hold_id, None, {
        'element_id': element_id,
        'arrival_date': arrival,
        'departure_date': departure,
        'hold_seconds': hold_seconds,
        'grace_seconds': grace_seconds,
        'renewal_required_at': prompt.isoformat(timespec='seconds'),
        'expires_at': expires.isoformat(timespec='seconds'),
    })
    return {
        'id': hold_id,
        'hold_seconds': hold_seconds,
        'grace_seconds': grace_seconds,
        'renewal_required_at': prompt.isoformat(timespec='seconds'),
        'expires_at': expires.isoformat(timespec='seconds'),
    }


def install_hold_timing() -> None:
    legacy.create_or_replace_hold = create_or_replace_hold


def register_hold_settings_routes(app) -> None:
    database = app.state.database

    @app.get('/company/hold-settings', response_class=HTMLResponse)
    def hold_settings_page(request: Request, saved: int = 0, error: str = ''):
        context = context_for(database, request)
        cid = int(working_company(context))
        hold_seconds, grace_seconds = hold_timing(database, cid)
        saved_html = '<div class="ok">Hold timing saved and written to the audit trail.</div>' if saved else ''
        error_html = f'<div class="error">{esc(error)}</div>' if error else ''
        body = f'''<h1>Hold timing</h1>{saved_html}{error_html}
        <div class="card"><h2>Temporary Element holds</h2>
        <p>These settings apply to both Client/Operator and Customer baskets. Only Supervisor and Client/Operator users can change them.</p>
        <form method="post" action="/company/hold-settings">
          <input type="hidden" name="csrf" value="{esc(context['csrf_token'])}">
          <div class="grid">
            <div><label>Hold time (seconds)</label><input type="number" name="hold_seconds" min="{MIN_HOLD_SECONDS}" max="{MAX_HOLD_SECONDS}" value="{hold_seconds}" required><p class="muted">Default 600 seconds = 10 minutes. For quick testing you can temporarily use 30 or 60 seconds.</p></div>
            <div><label>Response grace time (seconds)</label><input type="number" name="grace_seconds" min="{MIN_GRACE_SECONDS}" max="{MAX_GRACE_SECONDS}" value="{grace_seconds}" required><p class="muted">Default 60 seconds = 1 minute. This is the time allowed to click Yes after the warning appears.</p></div>
          </div>
          <p><button type="submit">Save hold timing</button> <a class="button secondary" href="/availability/calendar">Back to Availability Calendar</a></p>
        </form></div>'''
        return layout('Hold timing', body, context)

    @app.post('/company/hold-settings')
    async def hold_settings_save(request: Request):
        context = context_for(database, request)
        cid = int(working_company(context))
        data = await form_data(request)
        require_csrf(context, data)
        try:
            hold_seconds = _validate(data.get('hold_seconds', ''), MIN_HOLD_SECONDS, MAX_HOLD_SECONDS, 'Hold time')
            grace_seconds = _validate(data.get('grace_seconds', ''), MIN_GRACE_SECONDS, MAX_GRACE_SECONDS, 'Response grace time')
        except ValueError as exc:
            return RedirectResponse('/company/hold-settings?error=' + str(exc).replace(' ', '+'), 303)
        with database.connect() as c:
            before_row = c.execute('SELECT hold_seconds,grace_seconds FROM company_hold_settings WHERE company_id=?', (cid,)).fetchone()
            before = dict(before_row) if before_row else {'hold_seconds': DEFAULT_HOLD_SECONDS, 'grace_seconds': DEFAULT_GRACE_SECONDS}
            c.execute(
                '''INSERT INTO company_hold_settings(company_id,hold_seconds,grace_seconds,updated_by_user_id,updated_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(company_id) DO UPDATE SET hold_seconds=excluded.hold_seconds,grace_seconds=excluded.grace_seconds,updated_by_user_id=excluded.updated_by_user_id,updated_at=excluded.updated_at''',
                (cid, hold_seconds, grace_seconds, context['user_id'], iso_now()),
            )
        after = {'hold_seconds': hold_seconds, 'grace_seconds': grace_seconds}
        audit(database, context, cid, 'HOLD_TIMING_UPDATED', 'company_hold_settings', cid, before, after)
        return RedirectResponse('/company/hold-settings?saved=1', 303)

    @app.post('/availability/holds/renew')
    async def renew_holds_with_client_timing(request: Request):
        context, cid = legacy._session_company(database, request)
        data = await form_data(request)
        require_csrf(context, data)
        token = request.cookies.get(COOKIE_NAME, '')
        hold_seconds, grace_seconds = hold_timing(database, cid)
        now = legacy._now()
        prompt = now + timedelta(seconds=hold_seconds)
        expires = prompt + timedelta(seconds=grace_seconds)
        with database.connect() as c:
            legacy._purge_expired_holds(c)
            held = c.execute('SELECT * FROM element_holds WHERE company_id=? AND session_token=?', (cid, token)).fetchall()
            c.execute(
                'UPDATE element_holds SET renewal_required_at=?,expires_at=?,updated_at=? WHERE company_id=? AND session_token=?',
                (prompt.isoformat(timespec='seconds'), expires.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'), cid, token),
            )
        audit(database, context, cid, 'ELEMENT_HOLDS_RENEWED', 'element_hold', token[:12], {'count': len(held)}, {
            'count': len(held),
            'hold_seconds': hold_seconds,
            'grace_seconds': grace_seconds,
            'expires_at': expires.isoformat(timespec='seconds'),
        })
        return JSONResponse({
            'ok': True,
            'count': len(held),
            'hold_seconds': hold_seconds,
            'grace_seconds': grace_seconds,
            'renewal_required_at': prompt.isoformat(timespec='seconds'),
            'expires_at': expires.isoformat(timespec='seconds'),
        })
