from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, form_data, layout
from .database import iso_now
from .setup015_core import audit, context_for, require_csrf, rows, working_company

INTERNAL_STATES = (
    ('HELD', 'Held / Enquiry'),
    ('RESERVED', 'Reserved / Provisional'),
    ('CONFIRMED', 'Confirmed booking'),
    ('ON_SITE', 'On site'),
    ('RELEASED', 'Released / no longer blocks'),
)

DEFAULT_STATUSES = (
    ('Enquiry / Held', 'Held', '#FFE39A', 10, 'HELD', 1, 10),
    ('Deposit Paid', 'Deposit', '#F3C5C9', 20, 'CONFIRMED', 1, None),
    ('Balance Paid', 'Paid', '#CDECCF', 30, 'CONFIRMED', 1, None),
    ('On Site', 'On site', '#CFE2FF', 40, 'ON_SITE', 1, None),
    ('Released / Cancelled', 'Released', '#E5E7EB', 90, 'RELEASED', 0, None),
)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS booking_status_definitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    short_name TEXT NOT NULL DEFAULT '',
    colour TEXT NOT NULL DEFAULT '#F3C5C9',
    display_order INTEGER NOT NULL DEFAULT 10,
    internal_state TEXT NOT NULL DEFAULT 'CONFIRMED',
    blocks_availability INTEGER NOT NULL DEFAULT 1,
    expiry_minutes INTEGER,
    automation_config_json TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(company_id, name COLLATE NOCASE)
);
CREATE INDEX IF NOT EXISTS idx_booking_status_company
ON booking_status_definitions(company_id, active, display_order, name);
'''


def _columns(connection, table: str) -> set[str]:
    return {str(r['name']) for r in connection.execute(f'PRAGMA table_info({table})').fetchall()}


def _seed_defaults(connection) -> None:
    now = iso_now()
    companies = connection.execute('SELECT id FROM companies').fetchall()
    for company in companies:
        cid = int(company['id'])
        existing = int(connection.execute('SELECT COUNT(*) AS n FROM booking_status_definitions WHERE company_id=?', (cid,)).fetchone()['n'])
        if existing:
            continue
        for name, short_name, colour, order_no, state, blocks, expiry in DEFAULT_STATUSES:
            connection.execute(
                '''INSERT INTO booking_status_definitions
                   (company_id,name,short_name,colour,display_order,internal_state,blocks_availability,expiry_minutes,automation_config_json,active,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,1,?,?)''',
                (cid, name, short_name, colour, order_no, state, blocks, expiry, '{}', now, now),
            )


def initialise_booking_statuses(database) -> None:
    with database.connect() as c:
        c.executescript(SCHEMA)
        if 'workflow_status_id' not in _columns(c, 'enquiries'):
            c.execute('ALTER TABLE enquiries ADD COLUMN workflow_status_id INTEGER')
        if 'availability_expires_at' not in _columns(c, 'enquiries'):
            c.execute('ALTER TABLE enquiries ADD COLUMN availability_expires_at TEXT')
        if 'workflow_status_id' not in _columns(c, 'bookings'):
            c.execute('ALTER TABLE bookings ADD COLUMN workflow_status_id INTEGER')
        _seed_defaults(c)
        # Existing historical records are deliberately not turned into fresh blocking Enquiries.
        c.executescript('''
        DROP TRIGGER IF EXISTS webv1_default_enquiry_workflow;
        CREATE TRIGGER webv1_default_enquiry_workflow
        AFTER INSERT ON enquiries
        WHEN NEW.workflow_status_id IS NULL
        BEGIN
          UPDATE enquiries
          SET workflow_status_id=(
                SELECT id FROM booking_status_definitions
                WHERE company_id=NEW.company_id AND active=1 AND internal_state='HELD'
                ORDER BY display_order,id LIMIT 1
              ),
              availability_expires_at=(
                SELECT CASE WHEN expiry_minutes IS NULL THEN NULL
                            ELSE datetime('now','+' || expiry_minutes || ' minutes') END
                FROM booking_status_definitions
                WHERE company_id=NEW.company_id AND active=1 AND internal_state='HELD'
                ORDER BY display_order,id LIMIT 1
              )
          WHERE id=NEW.id;
        END;

        DROP TRIGGER IF EXISTS webv1_default_booking_workflow;
        CREATE TRIGGER webv1_default_booking_workflow
        AFTER INSERT ON bookings
        WHEN NEW.workflow_status_id IS NULL
        BEGIN
          UPDATE bookings
          SET workflow_status_id=(
            SELECT id FROM booking_status_definitions
            WHERE company_id=NEW.company_id AND active=1
              AND internal_state=CASE
                WHEN NEW.status='cancelled' THEN 'RELEASED'
                WHEN NEW.status='provisional' THEN 'RESERVED'
                WHEN NEW.status IN ('completed','no_show') THEN 'RELEASED'
                ELSE 'CONFIRMED' END
            ORDER BY display_order,id LIMIT 1
          )
          WHERE id=NEW.id;
        END;
        ''')


def default_status(database, company_id: int, internal_state: str):
    result = rows(database, '''SELECT * FROM booking_status_definitions
                              WHERE company_id=? AND active=1 AND internal_state=?
                              ORDER BY display_order,id LIMIT 1''', (company_id, internal_state))
    return result[0] if result else None


def status_by_id(database, company_id: int, status_id: int | None):
    if not status_id:
        return None
    result = rows(database, 'SELECT * FROM booking_status_definitions WHERE company_id=? AND id=?', (company_id, status_id))
    return result[0] if result else None


def _page(database, context, message: str = '', edit: int = 0) -> str:
    cid = int(working_company(context))
    all_rows = rows(database, 'SELECT * FROM booking_status_definitions WHERE company_id=? ORDER BY display_order,name', (cid,))
    current = next((r for r in all_rows if int(r['id']) == int(edit or 0)), None)
    state = str(current['internal_state']) if current else 'CONFIRMED'
    state_options = ''.join(f'<option value="{key}" {"selected" if key == state else ""}>{esc(label)}</option>' for key, label in INTERNAL_STATES)
    checked = 'checked' if (current is None or int(current['blocks_availability'])) else ''
    expiry = '' if current is None or current['expiry_minutes'] is None else str(int(current['expiry_minutes']))
    body = '<h1>Booking Statuses</h1>'
    body += '<div class="card"><p><a class="button secondary" href="/setup">Setup home</a> <a class="button secondary" href="/operations">Operations</a> <a class="button secondary" href="/availability/calendar">Availability Calendar</a></p></div>'
    if message:
        body += f'<div class="error">{esc(message)}</div>'
    body += f'''<div class="card"><h2>{'Edit' if current else 'Add'} Booking Status</h2>
    <p>Clients control the visible status name and colour. The System meaning keeps availability and future automation safe even if the visible wording changes.</p>
    <form method="post" action="/setup/booking-statuses/save">
      <input type="hidden" name="csrf" value="{esc(context['csrf_token'])}">
      <input type="hidden" name="id" value="{int(current['id']) if current else ''}">
      <div class="grid">
        <div><label>Status name</label><input name="name" value="{esc(current['name'] if current else '')}" required></div>
        <div><label>Short name</label><input name="short_name" maxlength="16" value="{esc(current['short_name'] if current else '')}"></div>
        <div><label>Calendar colour</label><input type="color" name="colour" value="{esc(current['colour'] if current else '#F3C5C9')}"></div>
        <div><label>Display order</label><input type="number" name="display_order" value="{int(current['display_order']) if current else 10}"></div>
        <div><label>System meaning</label><select name="internal_state">{state_options}</select></div>
        <div><label>Automatic expiry (minutes)</label><input type="number" min="1" name="expiry_minutes" value="{esc(expiry)}" placeholder="blank = no automatic expiry"></div>
      </div>
      <p><label><input type="checkbox" name="blocks_availability" value="1" {checked}> Blocks availability</label></p>
      <p class="muted">Future email automation will attach to these statuses; its configuration is reserved now but no emails are sent by this build.</p>
      <p><button>Save Status</button>{' <a class="button secondary" href="/setup/booking-statuses">Cancel edit</a>' if current else ''}</p>
    </form></div>'''
    body += '<div class="card"><table><thead><tr><th>Order</th><th>Status</th><th>Colour</th><th>System meaning</th><th>Blocks</th><th>Expiry</th><th>Status</th><th></th></tr></thead><tbody>'
    for r in all_rows:
        colour = str(r['colour'])
        expiry_text = f"{int(r['expiry_minutes'])} min" if r['expiry_minutes'] is not None else 'No expiry'
        body += f'''<tr><td>{int(r['display_order'])}</td><td>{esc(r['name'])}<br><span class="muted">{esc(r['short_name'])}</span></td>
        <td><span style="display:inline-block;width:46px;height:22px;border-radius:4px;background:{esc(colour)};border:1px solid #cbd3dc"></span> {esc(colour)}</td>
        <td>{esc(r['internal_state'])}</td><td>{'Yes' if int(r['blocks_availability']) else 'No'}</td><td>{esc(expiry_text)}</td><td>{'Active' if int(r['active']) else 'Inactive'}</td>
        <td><a href="/setup/booking-statuses?edit={int(r['id'])}">Edit</a> &nbsp;
        <form method="post" action="/setup/booking-statuses/toggle" style="display:inline"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><input type="hidden" name="id" value="{int(r['id'])}"><button class="secondary">{'Deactivate' if int(r['active']) else 'Reactivate'}</button></form> &nbsp;
        <form method="post" action="/setup/booking-statuses/delete" style="display:inline" onsubmit="return confirm('Delete this Booking Status? Used statuses are protected.')"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><input type="hidden" name="id" value="{int(r['id'])}"><button class="secondary">Delete</button></form></td></tr>'''
    body += '</tbody></table></div>'
    return layout('Booking Statuses', body, context)


def register_booking_status_routes(app) -> None:
    database = app.state.database

    @app.get('/setup/booking-statuses', response_class=HTMLResponse)
    def booking_statuses(request: Request, edit: int = 0, message: str = ''):
        context = context_for(database, request)
        return HTMLResponse(_page(database, context, message, edit))

    @app.post('/setup/booking-statuses/save')
    async def booking_status_save(request: Request):
        context = context_for(database, request); cid = int(working_company(context)); data = await form_data(request); require_csrf(context, data)
        raw_id = str(data.get('id', '')); status_id = int(raw_id) if raw_id.isdigit() else 0
        name = str(data.get('name', '')).strip(); short_name = str(data.get('short_name', '')).strip()[:16]
        colour = str(data.get('colour', '#F3C5C9')).strip(); internal_state = str(data.get('internal_state', 'CONFIRMED')).strip()
        try: order_no = int(data.get('display_order', '10') or 10)
        except ValueError: order_no = 10
        raw_expiry = str(data.get('expiry_minutes', '')).strip()
        try: expiry = int(raw_expiry) if raw_expiry else None
        except ValueError: expiry = None
        blocks = 1 if data.get('blocks_availability') == '1' else 0
        if not name or internal_state not in {x[0] for x in INTERNAL_STATES} or not (colour.startswith('#') and len(colour) == 7):
            return HTMLResponse(_page(database, context, 'Enter a valid status name, colour and System meaning.', status_id), 400)
        if expiry is not None and expiry <= 0:
            return HTMLResponse(_page(database, context, 'Automatic expiry must be blank or greater than zero.', status_id), 400)
        now = iso_now()
        try:
            with database.connect() as c:
                before = c.execute('SELECT * FROM booking_status_definitions WHERE id=? AND company_id=?', (status_id, cid)).fetchone() if status_id else None
                if status_id and before is None:
                    return HTMLResponse(_page(database, context, 'Booking Status not found.'), 404)
                if status_id:
                    c.execute('''UPDATE booking_status_definitions SET name=?,short_name=?,colour=?,display_order=?,internal_state=?,blocks_availability=?,expiry_minutes=?,updated_at=? WHERE id=? AND company_id=?''',
                              (name, short_name, colour, order_no, internal_state, blocks, expiry, now, status_id, cid))
                    saved_id = status_id
                else:
                    saved_id = int(c.execute('''INSERT INTO booking_status_definitions(company_id,name,short_name,colour,display_order,internal_state,blocks_availability,expiry_minutes,automation_config_json,active,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,1,?,?)''',
                                             (cid, name, short_name, colour, order_no, internal_state, blocks, expiry, json.dumps({}), now, now)).lastrowid)
        except Exception as exc:
            if 'UNIQUE' in str(exc).upper():
                return HTMLResponse(_page(database, context, 'That Booking Status name already exists.', status_id), 400)
            raise
        audit(database, context, cid, 'BOOKING_STATUS_SAVED', 'booking_status', saved_id, dict(before) if before else None,
              {'name': name, 'short_name': short_name, 'colour': colour, 'display_order': order_no, 'internal_state': internal_state, 'blocks_availability': blocks, 'expiry_minutes': expiry})
        return RedirectResponse('/setup/booking-statuses', 303)

    @app.post('/setup/booking-statuses/toggle')
    async def booking_status_toggle(request: Request):
        context = context_for(database, request); cid = int(working_company(context)); data = await form_data(request); require_csrf(context, data)
        try: status_id = int(data.get('id', ''))
        except ValueError: return RedirectResponse('/setup/booking-statuses', 303)
        with database.connect() as c:
            before = c.execute('SELECT * FROM booking_status_definitions WHERE id=? AND company_id=?', (status_id, cid)).fetchone()
            if before is None: return RedirectResponse('/setup/booking-statuses?message=Status+not+found', 303)
            new_active = 0 if int(before['active']) else 1
            if new_active == 0 and str(before['internal_state']) == 'HELD':
                others = int(c.execute("SELECT COUNT(*) AS n FROM booking_status_definitions WHERE company_id=? AND active=1 AND internal_state='HELD' AND id<>?", (cid, status_id)).fetchone()['n'])
                if others == 0:
                    return RedirectResponse('/setup/booking-statuses?message=Keep+at+least+one+active+Held+status+so+Enquiries+can+block+availability', 303)
            c.execute('UPDATE booking_status_definitions SET active=?,updated_at=? WHERE id=? AND company_id=?', (new_active, iso_now(), status_id, cid))
        audit(database, context, cid, 'BOOKING_STATUS_TOGGLED', 'booking_status', status_id, dict(before), {'active': new_active})
        return RedirectResponse('/setup/booking-statuses', 303)

    @app.post('/setup/booking-statuses/delete')
    async def booking_status_delete(request: Request):
        context = context_for(database, request); cid = int(working_company(context)); data = await form_data(request); require_csrf(context, data)
        try: status_id = int(data.get('id', ''))
        except ValueError: return RedirectResponse('/setup/booking-statuses', 303)
        with database.connect() as c:
            before = c.execute('SELECT * FROM booking_status_definitions WHERE id=? AND company_id=?', (status_id, cid)).fetchone()
            if before is None: return RedirectResponse('/setup/booking-statuses?message=Status+not+found', 303)
            used_e = int(c.execute('SELECT COUNT(*) AS n FROM enquiries WHERE company_id=? AND workflow_status_id=?', (cid, status_id)).fetchone()['n'])
            used_b = int(c.execute('SELECT COUNT(*) AS n FROM bookings WHERE company_id=? AND workflow_status_id=?', (cid, status_id)).fetchone()['n'])
            if used_e or used_b:
                return RedirectResponse('/setup/booking-statuses?message=That+status+is+already+used+and+cannot+be+deleted.+Deactivate+it+instead', 303)
            if str(before['internal_state']) == 'HELD':
                others = int(c.execute("SELECT COUNT(*) AS n FROM booking_status_definitions WHERE company_id=? AND active=1 AND internal_state='HELD' AND id<>?", (cid, status_id)).fetchone()['n'])
                if others == 0:
                    return RedirectResponse('/setup/booking-statuses?message=Keep+at+least+one+active+Held+status+so+Enquiries+can+block+availability', 303)
            c.execute('DELETE FROM booking_status_definitions WHERE id=? AND company_id=?', (status_id, cid))
        audit(database, context, cid, 'BOOKING_STATUS_DELETED', 'booking_status', status_id, dict(before), None)
        return RedirectResponse('/setup/booking-statuses', 303)

    @app.post('/operations/bookings/status')
    async def booking_status_change(request: Request):
        context = context_for(database, request); cid = int(working_company(context)); data = await form_data(request); require_csrf(context, data)
        try: booking_id = int(data.get('booking_id', '')); status_id = int(data.get('status_id', ''))
        except ValueError: return RedirectResponse('/availability/calendar', 303)
        with database.connect() as c:
            booking = c.execute('SELECT * FROM bookings WHERE id=? AND company_id=?', (booking_id, cid)).fetchone()
            status = c.execute('SELECT * FROM booking_status_definitions WHERE id=? AND company_id=? AND active=1', (status_id, cid)).fetchone()
            if booking is None or status is None: return RedirectResponse('/availability/calendar', 303)
            before = dict(booking)
            c.execute('UPDATE bookings SET workflow_status_id=?,updated_at=? WHERE id=? AND company_id=?', (status_id, iso_now(), booking_id, cid))
        audit(database, context, cid, 'BOOKING_STATUS_CHANGED', 'booking', booking_id, before,
              {'workflow_status_id': status_id, 'status_name': status['name'], 'internal_state': status['internal_state'], 'blocks_availability': int(status['blocks_availability'])})
        return RedirectResponse(f'/operations/bookings/{booking_id}', 303)
