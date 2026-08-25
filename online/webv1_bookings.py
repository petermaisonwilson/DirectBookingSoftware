from __future__ import annotations

import json
from datetime import datetime

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, form_data, layout
from .database import iso_now
from .setup015_calculator import _addon_rule
from .setup015_core import audit, context_for, one, require_csrf, rows, working_company
from .webv1_booking_status import default_status, status_by_id
from .webv1_status_availability import availability_state

PAYMENT_SCHEMA = '''
CREATE TABLE IF NOT EXISTS booking_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    booking_id INTEGER NOT NULL,
    amount REAL NOT NULL CHECK(amount > 0),
    payment_date TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT '',
    reference TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_by_user_id INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(booking_id) REFERENCES bookings(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_booking_payments_booking ON booking_payments(company_id, booking_id, payment_date, id);
'''


def initialise_booking_workflow(database) -> None:
    with database.connect() as c:
        c.executescript(PAYMENT_SCHEMA)


def _fmt_day(value: str | None) -> str:
    if not value:
        return '—'
    try:
        return datetime.fromisoformat(str(value)).strftime('%d/%m/%Y')
    except ValueError:
        try:
            return datetime.strptime(str(value), '%Y-%m-%d').strftime('%d/%m/%Y')
        except ValueError:
            return str(value)


def _money(value) -> str:
    return f'€{float(value or 0):.2f}'


def _booking(database, cid: int, booking_id: int):
    return one(database, '''SELECT b.*,c.first_name,c.last_name,c.email,c.phone,
        s.name AS workflow_name,s.colour,s.internal_state,s.blocks_availability
        FROM bookings b
        LEFT JOIN customer_records c ON c.id=b.customer_id AND c.company_id=b.company_id
        LEFT JOIN booking_status_definitions s ON s.id=b.workflow_status_id AND s.company_id=b.company_id
        WHERE b.id=? AND b.company_id=?''', (booking_id, cid))


def _next_reference(connection, cid: int) -> str:
    prefix = datetime.now().strftime('DB%y')
    rows_found = connection.execute('SELECT reference FROM bookings WHERE company_id=? AND reference LIKE ? ORDER BY id DESC LIMIT 100', (cid, prefix + '-%')).fetchall()
    highest = 0
    for row in rows_found:
        try:
            highest = max(highest, int(str(row['reference']).rsplit('-', 1)[1]))
        except (ValueError, IndexError):
            pass
    return f'{prefix}-{highest + 1:05d}'


def _conversion_statuses(database, cid: int):
    return rows(database, '''SELECT * FROM booking_status_definitions
        WHERE company_id=? AND active=1 AND internal_state IN ('RESERVED','CONFIRMED','ON_SITE')
        ORDER BY CASE internal_state WHEN 'RESERVED' THEN 1 WHEN 'CONFIRMED' THEN 2 ELSE 3 END, display_order,id''', (cid,))


def _snapshot_line_amount(snapshot: dict, name: str) -> float:
    for line in snapshot.get('lines') or []:
        if str(line.get('item', '')) == name:
            try:
                return float(line.get('amount', 0) or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def convert_enquiry(database, context, cid: int, enquiry_id: int, workflow_status_id: int) -> int:
    enquiry = one(database, '''SELECT e.*,er.element_type,er.element_id,er.provisional_total,er.pricing_snapshot_json,
        se.name AS element_name,se.pricing_method
        FROM enquiries e JOIN enquiry_requests er ON er.enquiry_id=e.id AND er.company_id=e.company_id
        LEFT JOIN setup_elements se ON se.id=er.element_id AND se.company_id=er.company_id
        WHERE e.id=? AND e.company_id=?''', (enquiry_id, cid))
    if enquiry is None:
        raise ValueError('Enquiry not found.')
    if str(enquiry['status']) == 'converted':
        existing = one(database, 'SELECT id FROM bookings WHERE company_id=? AND enquiry_id=? ORDER BY id DESC LIMIT 1', (cid, enquiry_id))
        if existing:
            return int(existing['id'])
        raise ValueError('This Enquiry is already marked converted.')
    if not enquiry['element_id'] or not enquiry['arrival_date'] or not enquiry['departure_date']:
        raise ValueError('Choose a specific Element and stay dates before converting this Enquiry.')
    if enquiry['provisional_total'] is None:
        raise ValueError('Calculate and save the Enquiry price before converting it to a Booking.')
    status = status_by_id(database, cid, workflow_status_id)
    if status is None or not int(status['active']) or str(status['internal_state']) not in {'RESERVED','CONFIRMED','ON_SITE'}:
        raise ValueError('Choose a valid Booking Status.')
    state = availability_state(database, cid, int(enquiry['element_id']), str(enquiry['arrival_date']), str(enquiry['departure_date']), exclude_enquiry_id=enquiry_id)
    if not state['available']:
        raise ValueError('The Element is no longer available: ' + str(state['reason']))
    try:
        snapshot = json.loads(enquiry['pricing_snapshot_json'] or '{}')
    except (TypeError, json.JSONDecodeError):
        snapshot = {}
    if not snapshot:
        raise ValueError('The Enquiry does not contain a frozen price snapshot. Recalculate it first.')
    now = iso_now()
    total = float(enquiry['provisional_total'])
    with database.connect() as c:
        reference = _next_reference(c, cid)
        booking_id = int(c.execute('''INSERT INTO bookings
            (company_id,reference,customer_id,enquiry_id,status,arrival_date,departure_date,currency,total_amount,pricing_snapshot_json,notes,created_at,updated_at,workflow_status_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (cid, reference, enquiry['customer_id'], enquiry_id, 'confirmed', enquiry['arrival_date'], enquiry['departure_date'], 'EUR', total, enquiry['pricing_snapshot_json'], enquiry['notes'] or '', now, now, workflow_status_id)).lastrowid)
        element_amount = _snapshot_line_amount(snapshot, str(enquiry['element_name'] or ''))
        booking_element_id = int(c.execute('''INSERT INTO booking_elements
            (company_id,booking_id,element_id,arrival_date,departure_date,pricing_method_snapshot,unit_price_snapshot,total_amount,pricing_snapshot_json)
            VALUES (?,?,?,?,?,?,?,?,?)''',
            (cid, booking_id, enquiry['element_id'], enquiry['arrival_date'], enquiry['departure_date'], enquiry['pricing_method'] or '', 0, element_amount, enquiry['pricing_snapshot_json'])).lastrowid)
        year = int(snapshot.get('year') or str(enquiry['arrival_date'])[:4])
        for person in c.execute('''SELECT ep.person_type_id,ep.quantity,pt.name FROM enquiry_people ep
            JOIN setup_person_types pt ON pt.id=ep.person_type_id AND pt.company_id=ep.company_id
            WHERE ep.enquiry_id=? AND ep.company_id=?''', (enquiry_id, cid)).fetchall():
            price = c.execute('SELECT rate FROM setup_person_prices WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?', (cid, year, enquiry['element_id'], person['person_type_id'])).fetchone()
            unit = float(price['rate']) if price else 0.0
            amount = _snapshot_line_amount(snapshot, str(person['name']))
            c.execute('''INSERT INTO booking_people(company_id,booking_element_id,person_type_id,quantity,unit_price_snapshot,total_amount)
                VALUES (?,?,?,?,?,?)''', (cid, booking_element_id, person['person_type_id'], person['quantity'], unit, amount))
        selected_addons = [int(x) for x in (snapshot.get('selected_addons') or [])]
        for aid in selected_addons:
            addon = c.execute('SELECT * FROM setup_addons WHERE company_id=? AND id=?', (cid, aid)).fetchone()
            if addon is None:
                continue
            qrow = c.execute('SELECT quantity FROM enquiry_addons WHERE enquiry_id=? AND company_id=? AND addon_id=?', (enquiry_id, cid, aid)).fetchone()
            qty = int(qrow['quantity']) if qrow else 0
            rule = _addon_rule(database, cid, year, one(database, 'SELECT * FROM setup_elements WHERE company_id=? AND id=?', (cid, enquiry['element_id'])), aid)
            amount = _snapshot_line_amount(snapshot, str(addon['name']))
            detail = {
                'rule': dict(rule),
                'when': (snapshot.get('addon_when') or {}).get(str(aid), (snapshot.get('addon_when') or {}).get(aid)),
                'days': (snapshot.get('addon_days') or {}).get(str(aid), (snapshot.get('addon_days') or {}).get(aid, {})),
                'people': (snapshot.get('addon_people') or {}).get(str(aid), (snapshot.get('addon_people') or {}).get(aid, {})),
                'person_days': (snapshot.get('addon_person_days') or {}).get(str(aid), (snapshot.get('addon_person_days') or {}).get(aid, {})),
                'frozen_amount': amount,
            }
            c.execute('''INSERT INTO booking_addons(company_id,booking_element_id,addon_id,quantity,pricing_method_snapshot,unit_price_snapshot,total_amount,rule_snapshot_json)
                VALUES (?,?,?,?,?,?,?,?)''', (cid, booking_element_id, aid, qty, addon['pricing_method'], float(rule.get('rate') or 0), amount, json.dumps(detail, separators=(',',':'))))
        c.execute("UPDATE enquiries SET status='converted',availability_expires_at=NULL,updated_at=? WHERE id=? AND company_id=?", (now, enquiry_id, cid))
        token = str(context['token']) if 'token' in context.keys() else ''
        if token:
            c.execute('DELETE FROM element_holds WHERE company_id=? AND element_id=? AND session_token=?', (cid, enquiry['element_id'], token))
    audit(database, context, cid, 'ENQUIRY_CONVERTED_TO_BOOKING', 'enquiry', enquiry_id, after={'booking_id': booking_id, 'reference': reference})
    audit(database, context, cid, 'BOOKING_CREATED', 'booking', booking_id, after={'reference': reference, 'enquiry_id': enquiry_id, 'workflow_status_id': workflow_status_id, 'total_amount': total})
    return booking_id


def _history(database, cid: int, booking_id: int):
    return rows(database, '''SELECT a.*,u.first_name,u.last_name FROM audit_log a
        LEFT JOIN users u ON u.id=a.actor_user_id
        WHERE a.company_id=? AND a.entity_type='booking' AND a.entity_id=?
        ORDER BY a.id DESC''', (cid, str(booking_id)))


def register_booking_routes(app) -> None:
    database = app.state.database

    @app.get('/operations/bookings', response_class=HTMLResponse)
    def booking_register(request: Request):
        context = context_for(database, request); cid = int(working_company(context))
        data = rows(database, '''SELECT b.*,c.first_name,c.last_name,s.name AS workflow_name,s.colour
            FROM bookings b LEFT JOIN customer_records c ON c.id=b.customer_id AND c.company_id=b.company_id
            LEFT JOIN booking_status_definitions s ON s.id=b.workflow_status_id AND s.company_id=b.company_id
            WHERE b.company_id=? ORDER BY b.arrival_date,b.id''', (cid,))
        trs = ''.join(f'''<tr><td><a href="/operations/bookings/{int(r['id'])}">{esc(r['reference'])}</a></td><td>{esc((str(r['first_name'] or '')+' '+str(r['last_name'] or '')).strip() or 'Customer')}</td><td><span style="padding:4px 8px;border-radius:4px;background:{esc(r['colour'] or '#eee')}">{esc(r['workflow_name'] or r['status'])}</span></td><td>{_fmt_day(r['arrival_date'])}</td><td>{_fmt_day(r['departure_date'])}</td><td>{_money(r['total_amount'])}</td></tr>''' for r in data) or '<tr><td colspan="6" class="muted">No Bookings yet.</td></tr>'
        body = f'''<h1>Bookings</h1><p><a href="/operations">← Operations</a></p><div class="card"><table><thead><tr><th>Reference</th><th>Customer</th><th>Status</th><th>Arrival</th><th>Departure</th><th>Total</th></tr></thead><tbody>{trs}</tbody></table></div>'''
        return layout('Bookings', body, context)

    @app.post('/operations/enquiries/{enquiry_id}/convert')
    async def convert(enquiry_id: int, request: Request):
        context = context_for(database, request); cid = int(working_company(context)); data = await form_data(request); require_csrf(context, data)
        try:
            status_id = int(data.get('workflow_status_id', ''))
            booking_id = convert_enquiry(database, context, cid, enquiry_id, status_id)
        except (TypeError, ValueError) as exc:
            return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error={esc(str(exc))}', 303)
        return RedirectResponse(f'/operations/bookings/{booking_id}?created=1', 303)

    @app.get('/operations/bookings/{booking_id}', response_class=HTMLResponse)
    def detail(booking_id: int, request: Request, created: int = 0, message: str = ''):
        context = context_for(database, request); cid = int(working_company(context)); b = _booking(database, cid, booking_id)
        if b is None:
            return HTMLResponse(layout('Booking not found', '<div class="error">Booking not found.</div>', context), 404)
        elements = rows(database, '''SELECT be.*,se.name AS element_name,se.element_type FROM booking_elements be JOIN setup_elements se ON se.id=be.element_id AND se.company_id=be.company_id WHERE be.booking_id=? AND be.company_id=? ORDER BY be.id''', (booking_id, cid))
        people = rows(database, '''SELECT bp.*,pt.name FROM booking_people bp JOIN booking_elements be ON be.id=bp.booking_element_id JOIN setup_person_types pt ON pt.id=bp.person_type_id AND pt.company_id=bp.company_id WHERE be.booking_id=? AND bp.company_id=? ORDER BY pt.name''', (booking_id, cid))
        addons = rows(database, '''SELECT ba.*,a.name FROM booking_addons ba JOIN booking_elements be ON be.id=ba.booking_element_id JOIN setup_addons a ON a.id=ba.addon_id AND a.company_id=ba.company_id WHERE be.booking_id=? AND ba.company_id=? ORDER BY a.name''', (booking_id, cid))
        payments = rows(database, 'SELECT * FROM booking_payments WHERE company_id=? AND booking_id=? ORDER BY payment_date,id', (cid, booking_id))
        paid = sum(float(p['amount']) for p in payments); balance = max(0.0, float(b['total_amount']) - paid)
        statuses = rows(database, 'SELECT * FROM booking_status_definitions WHERE company_id=? AND active=1 ORDER BY display_order,name', (cid,))
        opts = ''.join(f'<option value="{int(s["id"])}" {"selected" if int(s["id"])==int(b["workflow_status_id"] or 0) else ""}>{esc(s["name"])}</option>' for s in statuses)
        notice = '<div class="ok">Booking created from Enquiry. Prices are now frozen.</div>' if created else (f'<div class="ok">{esc(message)}</div>' if message else '')
        person_text = ', '.join(f'{esc(p["name"])} × {int(p["quantity"])} ({_money(p["total_amount"])})' for p in people) or '—'
        addon_text = ', '.join(f'{esc(a["name"])} × {int(a["quantity"])} ({_money(a["total_amount"])})' for a in addons) or '—'
        element_html = ''.join(f'<p><strong>{esc(e["element_name"])}</strong> ({esc(e["element_type"])}) — {_fmt_day(e["arrival_date"])} to {_fmt_day(e["departure_date"])}</p>' for e in elements)
        payment_rows = ''.join(f'<tr><td>{_fmt_day(p["payment_date"])}</td><td>{_money(p["amount"])}</td><td>{esc(p["method"] or "—")}</td><td>{esc(p["reference"] or "—")}</td><td>{esc(p["notes"] or "—")}</td></tr>' for p in payments) or '<tr><td colspan="5" class="muted">No payments recorded.</td></tr>'
        hist = _history(database, cid, booking_id)
        history_rows = ''.join(f'<tr><td>{esc(h["created_at"])}</td><td>{esc(((h["first_name"] or "")+" "+(h["last_name"] or "")).strip() or h["actor_role"] or "System")}</td><td>{esc(h["action"])}</td><td>{esc(h["after_json"] or h["before_json"] or "")}</td></tr>' for h in hist) or '<tr><td colspan="4" class="muted">No history yet.</td></tr>'
        body = f'''<h1>Booking {esc(b['reference'])}</h1><p><a href="/operations/bookings">← Bookings</a> &nbsp; <a href="/availability/calendar?arrival={esc(b['arrival_date'])}&departure={esc(b['departure_date'])}">Availability Calendar</a></p>{notice}
        <div class="grid"><div class="card"><h2>Customer</h2><p><strong>{esc((str(b['first_name'] or '')+' '+str(b['last_name'] or '')).strip() or 'Customer')}</strong><br>{esc(b['email'] or '—')}<br>{esc(b['phone'] or '—')}</p></div>
        <div class="card"><h2>Stay</h2><p><strong>Arrival:</strong> {_fmt_day(b['arrival_date'])}<br><strong>Departure:</strong> {_fmt_day(b['departure_date'])}<br><strong>Total:</strong> {_money(b['total_amount'])}<br><strong>Paid:</strong> {_money(paid)}<br><strong>Outstanding:</strong> {_money(balance)}</p></div></div>
        <div class="card"><h2>Booking Status</h2><form method="post" action="/operations/bookings/{booking_id}/status"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><div class="grid"><div><select name="workflow_status_id">{opts}</select></div><div><button>Change Status</button></div></div></form></div>
        <div class="card"><h2>Frozen Booking</h2>{element_html}<p><strong>People:</strong> {person_text}</p><p><strong>Add-ons:</strong> {addon_text}</p><p class="muted">These quantities and prices are snapshots. Later Setup price changes do not alter this Booking.</p></div>
        <div class="card"><h2>Payments</h2><table><thead><tr><th>Date</th><th>Amount</th><th>Method</th><th>Reference</th><th>Notes</th></tr></thead><tbody>{payment_rows}</tbody></table><h3>Record payment</h3><form method="post" action="/operations/bookings/{booking_id}/payments"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><div class="grid"><div><label>Amount</label><input name="amount" type="number" min="0.01" step="0.01" required></div><div><label>Date</label><input name="payment_date" type="date" value="{datetime.now().strftime('%Y-%m-%d')}" required></div><div><label>Method</label><input name="method" placeholder="Card, cash, bank transfer"></div><div><label>Reference</label><input name="reference"></div></div><label>Notes</label><input name="notes"><p><button>Record Payment</button></p></form></div>
        <div class="card"><h2>Booking History</h2><table><thead><tr><th>When</th><th>Who</th><th>Activity</th><th>Detail</th></tr></thead><tbody>{history_rows}</tbody></table></div>'''
        return layout(f'Booking {b["reference"]}', body, context)

    @app.post('/operations/bookings/{booking_id}/status')
    async def change_status(booking_id: int, request: Request):
        context = context_for(database, request); cid = int(working_company(context)); data = await form_data(request); require_csrf(context, data)
        b = _booking(database, cid, booking_id)
        if b is None:
            return RedirectResponse('/operations/bookings', 303)
        try:
            sid = int(data.get('workflow_status_id', ''))
        except ValueError:
            return RedirectResponse(f'/operations/bookings/{booking_id}', 303)
        status = status_by_id(database, cid, sid)
        if status is None or not int(status['active']):
            return RedirectResponse(f'/operations/bookings/{booking_id}', 303)
        before = {'workflow_status_id': b['workflow_status_id'], 'workflow_name': b['workflow_name']}
        internal = str(status['internal_state'])
        legacy = 'cancelled' if internal == 'RELEASED' else ('completed' if internal == 'ON_SITE' and str(status['name']).lower().startswith('complete') else 'confirmed')
        with database.connect() as c:
            c.execute('UPDATE bookings SET workflow_status_id=?,status=?,updated_at=? WHERE id=? AND company_id=?', (sid, legacy, iso_now(), booking_id, cid))
        audit(database, context, cid, 'BOOKING_STATUS_CHANGED', 'booking', booking_id, before, {'workflow_status_id': sid, 'workflow_name': str(status['name']), 'internal_state': internal, 'blocks_availability': int(status['blocks_availability'])})
        return RedirectResponse(f'/operations/bookings/{booking_id}?message=Booking+status+updated', 303)

    @app.post('/operations/bookings/{booking_id}/payments')
    async def add_payment(booking_id: int, request: Request):
        context = context_for(database, request); cid = int(working_company(context)); data = await form_data(request); require_csrf(context, data)
        if _booking(database, cid, booking_id) is None:
            return RedirectResponse('/operations/bookings', 303)
        try:
            amount = round(float(str(data.get('amount', '')).replace(',', '.')), 2)
            if amount <= 0:
                raise ValueError
        except ValueError:
            return RedirectResponse(f'/operations/bookings/{booking_id}?message=Enter+a+valid+payment+amount', 303)
        payment_date = str(data.get('payment_date', '')).strip()
        try:
            datetime.strptime(payment_date, '%Y-%m-%d')
        except ValueError:
            return RedirectResponse(f'/operations/bookings/{booking_id}?message=Enter+a+valid+payment+date', 303)
        with database.connect() as c:
            payment_id = int(c.execute('''INSERT INTO booking_payments(company_id,booking_id,amount,payment_date,method,reference,notes,created_by_user_id,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)''', (cid, booking_id, amount, payment_date, str(data.get('method','')).strip(), str(data.get('reference','')).strip(), str(data.get('notes','')).strip(), context['user_id'], iso_now())).lastrowid)
        audit(database, context, cid, 'BOOKING_PAYMENT_RECORDED', 'booking', booking_id, after={'payment_id': payment_id, 'amount': amount, 'payment_date': payment_date, 'method': str(data.get('method','')).strip(), 'reference': str(data.get('reference','')).strip()})
        return RedirectResponse(f'/operations/bookings/{booking_id}?message=Payment+recorded', 303)


def enquiry_conversion_panel(database, context, enquiry_id: int) -> str:
    cid = int(working_company(context))
    existing = one(database, 'SELECT id,reference FROM bookings WHERE company_id=? AND enquiry_id=? ORDER BY id DESC LIMIT 1', (cid, enquiry_id))
    if existing:
        return f'<div class="card"><h2>Booking</h2><p>This Enquiry has been converted to <a href="/operations/bookings/{int(existing["id"])}"><strong>{esc(existing["reference"])}</strong></a>.</p></div>'
    statuses = _conversion_statuses(database, cid)
    if not statuses:
        return '<div class="card"><h2>Convert to Booking</h2><div class="error">Create an active Reserved/Confirmed Booking Status first.</div></div>'
    default = next((s for s in statuses if str(s['internal_state']) == 'CONFIRMED'), statuses[0])
    opts = ''.join(f'<option value="{int(s["id"])}" {"selected" if int(s["id"])==int(default["id"]) else ""}>{esc(s["name"])}</option>' for s in statuses)
    return f'''<div class="card"><h2>Convert to Booking</h2><p>This freezes the Enquiry's current Element, people, Add-ons and price into a permanent Booking.</p><form method="post" action="/operations/enquiries/{enquiry_id}/convert"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><div class="grid"><div><label>Initial Booking Status</label><select name="workflow_status_id">{opts}</select></div><div style="align-self:end"><button>Confirm / Convert to Booking</button></div></div></form></div>'''
