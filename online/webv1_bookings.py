from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, form_data, layout
from .database import iso_now
from .setup015_calculator import _addon_rule
from .setup015_core import audit, context_for, one, require_csrf, rows, working_company
from .webv1_booking_status import default_status, status_by_id
from .webv1_payment_methods import payment_rule
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


def _local_today():
    return datetime.now(timezone.utc).astimezone(ZoneInfo('Europe/Paris')).date()


def _fmt_when(value) -> str:
    if not value:
        return '—'
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(ZoneInfo('Europe/Paris')).strftime('%d/%m/%Y %H:%M')
    except ValueError:
        return str(value)


def _status_named(database, cid: int, name: str):
    found = rows(database, 'SELECT * FROM booking_status_definitions WHERE company_id=? AND active=1 AND name=? COLLATE NOCASE ORDER BY id LIMIT 1', (cid, name))
    return found[0] if found else None


def _booking(database, cid: int, booking_id: int):
    return one(database, '''SELECT b.*,c.first_name,c.last_name,c.email,c.phone,
        s.name AS workflow_name,s.colour,s.internal_state,s.blocks_availability
        FROM bookings b
        LEFT JOIN customer_records c ON c.id=b.customer_id AND c.company_id=b.company_id
        LEFT JOIN booking_status_definitions s ON s.id=b.workflow_status_id AND s.company_id=b.company_id
        WHERE b.id=? AND b.company_id=?''', (booking_id, cid))


def _next_reference(connection, cid: int) -> str:
    prefix = datetime.now(timezone.utc).astimezone(ZoneInfo('Europe/Paris')).strftime('DB%y')
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


def payment_due(database, cid: int, total: float, arrival_date: str, *, today=None) -> tuple[float, str]:
    rule=payment_rule(database,cid); total=round(max(0.0,float(total or 0)),2)
    if rule is None:return total,'No Payment Rules are configured, so full payment is required.'
    if int(rule['always_require_full_payment']):return total,'Always Require Full Payment is enabled.'
    threshold=rule['full_payment_threshold']
    if threshold is not None and total<=float(threshold):return total,f'Booking total is within the full-payment threshold of {_money(threshold)}.'
    current=today or _local_today()
    try:arrival=datetime.strptime(str(arrival_date),'%Y-%m-%d').date()
    except ValueError:arrival=current
    balance_days=int(rule['balance_due_days'] or 0)
    if arrival<=current+timedelta(days=balance_days):return total,f'Arrival is within the {balance_days}-day balance-due period.'
    value=float(rule['deposit_value'] or 0)
    if str(rule['deposit_type'])=='percent':due=round(total*value/100.0,2);reason=f'{value:g}% deposit is required.'
    else:due=round(value,2);reason=f'Fixed deposit of {_money(value)} is required.'
    return min(total,max(0.0,due)),reason


def _snapshot_line_amount(snapshot: dict, name: str) -> float:
    for line in snapshot.get('lines') or []:
        if str(line.get('item', '')) == name:
            try:
                return float(line.get('amount', 0) or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def _enquiry_elements(database,cid,enquiry_id):
    data=rows(database,'''SELECT ee.*,se.name AS element_name,se.pricing_method FROM enquiry_elements ee JOIN setup_elements se ON se.id=ee.element_id AND se.company_id=ee.company_id WHERE ee.company_id=? AND ee.enquiry_id=? ORDER BY ee.sort_order,ee.id''',(cid,enquiry_id))
    if data:return data
    legacy=one(database,'''SELECT e.id AS enquiry_id,e.company_id,er.element_type,er.element_id,e.arrival_date,e.departure_date,'' AS lead_name,e.party_size,er.provisional_total,er.pricing_snapshot_json,se.name AS element_name,se.pricing_method FROM enquiries e JOIN enquiry_requests er ON er.enquiry_id=e.id AND er.company_id=e.company_id JOIN setup_elements se ON se.id=er.element_id AND se.company_id=er.company_id WHERE e.id=? AND e.company_id=?''',(enquiry_id,cid))
    return [legacy] if legacy is not None else []

def _write_booking(c,database,context,cid,enquiry,elements,workflow_status_id):
    now=iso_now();total=sum(float(e['provisional_total'] or 0) for e in elements);reference=_next_reference(c,cid)
    arrival=min(str(e['arrival_date']) for e in elements);departure=max(str(e['departure_date']) for e in elements)
    booking_id=int(c.execute('''INSERT INTO bookings (company_id,reference,customer_id,enquiry_id,status,arrival_date,departure_date,currency,total_amount,pricing_snapshot_json,notes,created_at,updated_at,workflow_status_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(cid,reference,enquiry['customer_id'],enquiry['id'],'confirmed',arrival,departure,'EUR',total,json.dumps({'multi_element':len(elements)>1,'element_count':len(elements),'total':total},separators=(',',':')),enquiry['notes'] or '',now,now,workflow_status_id)).lastrowid)
    for ee in elements:
        try:snapshot=json.loads(ee['pricing_snapshot_json'] or '{}')
        except (TypeError,json.JSONDecodeError):snapshot={}
        element_amount=_snapshot_line_amount(snapshot,str(ee['element_name'] or ''))
        beid=int(c.execute('''INSERT INTO booking_elements(company_id,booking_id,element_id,arrival_date,departure_date,pricing_method_snapshot,unit_price_snapshot,total_amount,pricing_snapshot_json,lead_name) VALUES (?,?,?,?,?,?,?,?,?,?)''',(cid,booking_id,ee['element_id'],ee['arrival_date'],ee['departure_date'],ee['pricing_method'] or '',0,element_amount,ee['pricing_snapshot_json'],ee['lead_name'] or '')).lastrowid)
        year=int(snapshot.get('year') or str(ee['arrival_date'])[:4])
        if 'id' in ee.keys():
            people_rows=c.execute('''SELECT ep.person_type_id,ep.quantity,pt.name FROM enquiry_element_people ep JOIN setup_person_types pt ON pt.id=ep.person_type_id AND pt.company_id=ep.company_id WHERE ep.enquiry_element_id=? AND ep.company_id=?''',(ee['id'],cid)).fetchall()
        else:
            people_rows=c.execute('''SELECT ep.person_type_id,ep.quantity,pt.name FROM enquiry_people ep JOIN setup_person_types pt ON pt.id=ep.person_type_id AND pt.company_id=ep.company_id WHERE ep.enquiry_id=? AND ep.company_id=?''',(enquiry['id'],cid)).fetchall()
        for p in people_rows:
            pr=c.execute('SELECT rate FROM setup_person_prices WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?',(cid,year,ee['element_id'],p['person_type_id'])).fetchone();unit=float(pr['rate']) if pr else 0.0
            c.execute('INSERT INTO booking_people(company_id,booking_element_id,person_type_id,quantity,unit_price_snapshot,total_amount) VALUES (?,?,?,?,?,?)',(cid,beid,p['person_type_id'],p['quantity'],unit,_snapshot_line_amount(snapshot,str(p['name']))))
        element=c.execute('SELECT * FROM setup_elements WHERE company_id=? AND id=?',(cid,ee['element_id'])).fetchone()
        for aid in [int(x) for x in (snapshot.get('selected_addons') or [])]:
            addon=c.execute('SELECT * FROM setup_addons WHERE company_id=? AND id=?',(cid,aid)).fetchone()
            if addon is None:continue
            qr=(c.execute('SELECT quantity FROM enquiry_element_addons WHERE enquiry_element_id=? AND company_id=? AND addon_id=?',(ee['id'],cid,aid)).fetchone() if 'id' in ee.keys() else c.execute('SELECT quantity FROM enquiry_addons WHERE enquiry_id=? AND company_id=? AND addon_id=?',(enquiry['id'],cid,aid)).fetchone());qty=int(qr['quantity']) if qr else 0;rule=_addon_rule(database,cid,year,element,aid);amount=_snapshot_line_amount(snapshot,str(addon['name']))
            detail={'rule':dict(rule),'when':(snapshot.get('addon_when') or {}).get(str(aid),(snapshot.get('addon_when') or {}).get(aid)),'days':(snapshot.get('addon_days') or {}).get(str(aid),(snapshot.get('addon_days') or {}).get(aid,{})),'people':(snapshot.get('addon_people') or {}).get(str(aid),(snapshot.get('addon_people') or {}).get(aid,{})),'person_days':(snapshot.get('addon_person_days') or {}).get(str(aid),(snapshot.get('addon_person_days') or {}).get(aid,{})),'frozen_amount':amount}
            c.execute('INSERT INTO booking_addons(company_id,booking_element_id,addon_id,quantity,pricing_method_snapshot,unit_price_snapshot,total_amount,rule_snapshot_json) VALUES (?,?,?,?,?,?,?,?)',(cid,beid,aid,qty,addon['pricing_method'],float(rule.get('rate') or 0),amount,json.dumps(detail,separators=(',',':'))))
    c.execute("UPDATE enquiries SET status='converted',availability_expires_at=NULL,updated_at=? WHERE id=? AND company_id=?",(now,enquiry['id'],cid))
    return booking_id,reference,total

def _conversion_enquiry(database,context,cid,enquiry_id,workflow_status_id):
    enquiry=one(database,'SELECT * FROM enquiries WHERE id=? AND company_id=?',(enquiry_id,cid))
    if enquiry is None:raise ValueError('Enquiry not found.')
    if str(enquiry['status'])=='converted':raise ValueError('This Enquiry is already converted.')
    status=status_by_id(database,cid,workflow_status_id)
    if status is None or not int(status['active']) or str(status['internal_state']) not in {'RESERVED','CONFIRMED','ON_SITE'}:raise ValueError('Choose a valid Booking Status.')
    elements=_enquiry_elements(database,cid,enquiry_id)
    if not elements:raise ValueError('Choose at least one specific Element and stay dates before converting this Enquiry.')
    token=str(context['token']) if 'token' in context.keys() else ''
    for ee in elements:
        if ee['provisional_total'] is None:raise ValueError('Calculate and save every Element price before converting this Enquiry.')
        state=availability_state(database,cid,int(ee['element_id']),str(ee['arrival_date']),str(ee['departure_date']),session_token=token,exclude_enquiry_id=enquiry_id)
        if not state['available']:raise ValueError(str(ee['element_name'])+' is no longer available: '+str(state['reason']))
        try:snapshot=json.loads(ee['pricing_snapshot_json'] or '{}')
        except (TypeError,json.JSONDecodeError):snapshot={}
        if not snapshot:raise ValueError(str(ee['element_name'])+' does not contain a frozen price snapshot. Recalculate it first.')
    return enquiry,elements

def convert_enquiry(database,context,cid,enquiry_id,workflow_status_id):
    enquiry,elements=_conversion_enquiry(database,context,cid,enquiry_id,workflow_status_id)
    with database.connect() as c:booking_id,reference,total=_write_booking(c,database,context,cid,enquiry,elements,workflow_status_id)
    audit(database,context,cid,'ENQUIRY_CONVERTED_TO_BOOKING','enquiry',enquiry_id,after={'booking_id':booking_id,'reference':reference,'element_count':len(elements)});audit(database,context,cid,'BOOKING_CREATED','booking',booking_id,after={'reference':reference,'enquiry_id':enquiry_id,'workflow_status_id':workflow_status_id,'total_amount':total})
    return booking_id

def convert_enquiry_with_payment(database,context,cid,enquiry_id,workflow_status_id,*,amount,payment_date,method,reference='',notes=''):
    enquiry,elements=_conversion_enquiry(database,context,cid,enquiry_id,workflow_status_id);total=sum(float(e['provisional_total'] or 0) for e in elements);required,_=payment_due(database,cid,total,min(str(e['arrival_date']) for e in elements))
    if round(float(amount),2)!=round(required,2):raise ValueError(f'Payment must equal the required amount of {_money(required)}.')
    with database.connect() as c:
        booking_id,bref,total=_write_booking(c,database,context,cid,enquiry,elements,workflow_status_id)
        payment_id=int(c.execute('INSERT INTO booking_payments(company_id,booking_id,amount,payment_date,method,reference,notes,created_by_user_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)',(cid,booking_id,amount,payment_date,str(method['name']),reference,notes,context['user_id'],iso_now())).lastrowid)
    audit(database,context,cid,'ENQUIRY_CONVERTED_TO_BOOKING','enquiry',enquiry_id,after={'booking_id':booking_id,'reference':bref,'element_count':len(elements)});audit(database,context,cid,'BOOKING_CREATED','booking',booking_id,after={'reference':bref,'enquiry_id':enquiry_id,'workflow_status_id':workflow_status_id,'total_amount':total});audit(database,context,cid,'BOOKING_PAYMENT_RECORDED','booking',booking_id,after={'payment_id':payment_id,'amount':amount,'payment_date':payment_date,'method':str(method['name'])})
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
        context=context_for(database,request); data=await form_data(request); require_csrf(context,data)
        return RedirectResponse(f'/operations/enquiries/{enquiry_id}/confirm',303)

    @app.get('/operations/enquiries/{enquiry_id}/confirm', response_class=HTMLResponse)
    def confirm_booking(enquiry_id: int, request: Request):
        context=context_for(database,request); cid=int(working_company(context))
        enquiry=one(database, 'SELECT e.*,c.first_name,c.last_name FROM enquiries e LEFT JOIN customer_records c ON c.id=e.customer_id AND c.company_id=e.company_id WHERE e.id=? AND e.company_id=?',(enquiry_id,cid))
        if enquiry is None: return RedirectResponse('/operations/enquiries',303)
        elements=_enquiry_elements(database,cid,enquiry_id)
        if not elements:return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error=No+bookable+Elements+saved',303)
        methods=rows(database,'SELECT * FROM payment_method_definitions WHERE company_id=? AND active=1 ORDER BY display_order,name',(cid,))
        options=''.join(f'<option value="{int(m["id"])}">{esc(m["name"])}</option>' for m in methods)
        total=sum(float(e['provisional_total'] or 0) for e in elements); first_arrival=min(str(e['arrival_date']) for e in elements); required,reason=payment_due(database,cid,total,first_arrival); customer=(str(enquiry['first_name'] or '')+' '+str(enquiry['last_name'] or '')).strip() or 'Customer'; party=int(enquiry['party_size'] or 0)
        pending='Balance Pending' if round(required,2)>=round(total,2) else 'Deposit Pending'
        method_block=f'<div><label>Payment Method</label><select name="payment_method_id" required>{options}</select></div>' if methods else '<div class="error">No active Payment Methods. Add one in Setup before taking payment.</div>'
        body=f'''<h1>Confirm Booking</h1><p><a href="/operations/enquiries/{enquiry_id}">Back to Enquiry</a></p><div class="card"><h2>{esc(customer)}</h2><p><strong>Stay:</strong> {_fmt_day(enquiry['arrival_date'])} to {_fmt_day(enquiry['departure_date'])}<br><strong>Party:</strong> {party}<br><strong>Booking total:</strong> {_money(total)}<br><strong>Payment Required Now:</strong> {_money(required)}<br><strong>Reason:</strong> {esc(reason)}<br><strong>Payment status:</strong> {pending}</p></div><div class="card"><h2>Take Payment</h2><p>Record the required payment before DBS creates the Booking.</p><form method="post" action="/operations/enquiries/{enquiry_id}/confirm-payment"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><div class="grid"><div><label>Amount</label><input name="amount" value="{required:.2f}" readonly required></div><div><label>Payment Date</label><input type="date" name="payment_date" value="{_local_today().isoformat()}" required></div>{method_block}<div><label>Reference</label><input name="reference"></div></div><label>Notes</label><input name="notes"><p><button {'disabled' if not methods else ''}>RECORD PAYMENT &amp; CONFIRM BOOKING</button></p></form></div><div class="card"><h2>Confirm without payment</h2><p>A reason is compulsory and is written to the audit trail.</p><form method="post" action="/operations/enquiries/{enquiry_id}/confirm-without-payment"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><label>Reason</label><input name="reason" required><p><button class="warning">CONFIRM WITHOUT PAYMENT</button></p></form></div>'''
        return layout('Confirm Booking',body,context)

    @app.post('/operations/enquiries/{enquiry_id}/confirm-payment')
    async def confirm_payment(enquiry_id:int,request:Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        try:
            method_id=int(data.get('payment_method_id','')); amount=round(float(str(data.get('amount','')).replace(',','.')),2)
            if amount<=0: raise ValueError
            datetime.strptime(str(data.get('payment_date','')),'%Y-%m-%d')
        except (TypeError,ValueError): return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error=Enter+valid+payment+details',303)
        method=one(database,'SELECT * FROM payment_method_definitions WHERE company_id=? AND id=? AND active=1',(cid,method_id))
        if method is None: return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error=Choose+an+active+Payment+Method',303)
        if str(method['method_type'])=='card': return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error=Card+provider+connection+is+not+configured+yet',303)
        total=sum(float(e['provisional_total'] or 0) for e in _enquiry_elements(database,cid,enquiry_id)); status=_status_named(database,cid,'Balance Paid' if round(amount,2)>=round(total,2) else 'Deposit Paid') or default_status(database,cid,'CONFIRMED')
        try: booking_id=convert_enquiry_with_payment(database,context,cid,enquiry_id,int(status['id']),amount=amount,payment_date=str(data.get('payment_date','')),method=method,reference=str(data.get('reference','')).strip(),notes=str(data.get('notes','')).strip())
        except ValueError as exc: return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error={esc(str(exc))}',303)
        return RedirectResponse(f'/operations/bookings/{booking_id}?created=1',303)

    @app.post('/operations/enquiries/{enquiry_id}/confirm-without-payment')
    async def confirm_without_payment(enquiry_id:int,request:Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data); reason=str(data.get('reason','')).strip()
        if not reason: return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error=A+reason+is+required+to+confirm+without+payment',303)
        pending=_status_named(database,cid,'Payment Pending') or default_status(database,cid,'RESERVED')
        try: booking_id=convert_enquiry(database,context,cid,enquiry_id,int(pending['id']))
        except (TypeError,ValueError) as exc: return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error={esc(str(exc))}',303)
        audit(database,context,cid,'BOOKING_CONFIRMED_WITHOUT_PAYMENT','booking',booking_id,after={'reason':reason,'enquiry_id':enquiry_id})
        return RedirectResponse(f'/operations/bookings/{booking_id}?created=1&message=Confirmed+without+payment',303)

    @app.post('/operations/enquiries/{enquiry_id}/quote-status')
    async def keep_as_quote(enquiry_id: int, request: Request):
        context = context_for(database, request); cid = int(working_company(context)); data = await form_data(request); require_csrf(context, data)
        try:
            status_id = int(data.get('workflow_status_id', ''))
        except (TypeError, ValueError):
            return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error=Choose+a+valid+Quote+Status', 303)
        status = status_by_id(database, cid, status_id)
        if status is None or not int(status['active']) or str(status['internal_state']) != 'HELD':
            return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error=Choose+a+valid+Quote+Status', 303)
        enquiry = one(database, 'SELECT * FROM enquiries WHERE id=? AND company_id=?', (enquiry_id, cid))
        if enquiry is None or str(enquiry['status']) == 'converted':
            return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error=Enquiry+cannot+be+kept+as+a+Quote', 303)
        expiry = None
        if int(status['blocks_availability']) and status['expiry_minutes'] is not None:
            expiry = (datetime.fromisoformat(iso_now()) + timedelta(minutes=int(status['expiry_minutes']))).isoformat(timespec='seconds')
        with database.connect() as c:
            c.execute('UPDATE enquiries SET workflow_status_id=?,availability_expires_at=?,updated_at=? WHERE id=? AND company_id=?',
                      (status_id, expiry, iso_now(), enquiry_id, cid))
        audit(database, context, cid, 'ENQUIRY_QUOTE_STATUS_CHANGED', 'enquiry', enquiry_id, dict(enquiry),
              {'workflow_status_id': status_id, 'status_name': status['name'], 'availability_expires_at': expiry})
        return RedirectResponse(f'/operations/enquiries/{enquiry_id}?saved=1', 303)

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
        history_rows = ''.join(f'<tr><td>{esc(_fmt_when(h["created_at"]))}</td><td>{esc(((h["first_name"] or "")+" "+(h["last_name"] or "")).strip() or h["actor_role"] or "System")}</td><td>{esc(h["action"])}</td><td>{esc(h["after_json"] or h["before_json"] or "")}</td></tr>' for h in hist) or '<tr><td colspan="4" class="muted">No history yet.</td></tr>'
        body = f'''<h1>Booking {esc(b['reference'])}</h1><p><a href="/operations/bookings">← Bookings</a> &nbsp; <a href="/availability/calendar?arrival={esc(b['arrival_date'])}&departure={esc(b['departure_date'])}">Availability Calendar</a></p>{notice}
        <div class="grid"><div class="card"><h2>Customer</h2><p><strong>{esc((str(b['first_name'] or '')+' '+str(b['last_name'] or '')).strip() or 'Customer')}</strong><br>{esc(b['email'] or '—')}<br>{esc(b['phone'] or '—')}</p></div>
        <div class="card"><h2>Stay</h2><p><strong>Arrival:</strong> {_fmt_day(b['arrival_date'])}<br><strong>Departure:</strong> {_fmt_day(b['departure_date'])}<br><strong>Total:</strong> {_money(b['total_amount'])}<br><strong>Paid:</strong> {_money(paid)}<br><strong>Outstanding:</strong> {_money(balance)}</p></div></div>
        <div class="card"><h2>Booking Status</h2><form method="post" action="/operations/bookings/{booking_id}/status"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><div class="grid"><div><select name="workflow_status_id">{opts}</select></div><div><button>Change Status</button></div></div></form></div>
        <div class="card"><h2>Frozen Booking</h2>{element_html}<p><strong>People:</strong> {person_text}</p><p><strong>Add-ons:</strong> {addon_text}</p><p class="muted">These quantities and prices are snapshots. Later Setup price changes do not alter this Booking.</p></div>
        <div class="card"><h2>Payments</h2><table><thead><tr><th>Date</th><th>Amount</th><th>Method</th><th>Reference</th><th>Notes</th></tr></thead><tbody>{payment_rows}</tbody></table><h3>Record payment</h3><form method="post" action="/operations/bookings/{booking_id}/payments"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><div class="grid"><div><label>Amount</label><input name="amount" type="number" min="0.01" step="0.01" required></div><div><label>Date</label><input name="payment_date" type="date" value="{_local_today().isoformat()}" required></div><div><label>Method</label><select name="payment_method_id" required>{''.join(f'<option value="{int(m["id"])}">{esc(m["name"])}</option>' for m in rows(database,'SELECT * FROM payment_method_definitions WHERE company_id=? AND active=1 ORDER BY display_order,name',(cid,)))}</select></div><div><label>Reference</label><input name="reference"></div></div><label>Notes</label><input name="notes"><p><button>Record Payment</button></p></form></div>
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
            if internal=='RELEASED' and b['enquiry_id']:
                c.execute("UPDATE enquiries SET status='closed',availability_expires_at=NULL,updated_at=? WHERE id=? AND company_id=? AND status='converted'",(iso_now(),int(b['enquiry_id']),cid))
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
        try: method_id=int(data.get('payment_method_id',''))
        except (TypeError,ValueError): return RedirectResponse(f'/operations/bookings/{booking_id}?message=Choose+an+active+Payment+Method',303)
        method=one(database,'SELECT * FROM payment_method_definitions WHERE company_id=? AND id=? AND active=1',(cid,method_id))
        if method is None: return RedirectResponse(f'/operations/bookings/{booking_id}?message=Choose+an+active+Payment+Method',303)
        if str(method['method_type'])=='card': return RedirectResponse(f'/operations/bookings/{booking_id}?message=Card+provider+connection+is+not+configured+yet',303)
        with database.connect() as c:
            payment_id = int(c.execute('''INSERT INTO booking_payments(company_id,booking_id,amount,payment_date,method,reference,notes,created_by_user_id,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)''', (cid, booking_id, amount, payment_date, str(method['name']), str(data.get('reference','')).strip(), str(data.get('notes','')).strip(), context['user_id'], iso_now())).lastrowid)
            total=float(c.execute('SELECT total_amount FROM bookings WHERE id=? AND company_id=?',(booking_id,cid)).fetchone()['total_amount'])
            paid=float(c.execute('SELECT COALESCE(SUM(amount),0) AS n FROM booking_payments WHERE booking_id=? AND company_id=?',(booking_id,cid)).fetchone()['n'])
            desired=_status_named(database,cid,'Balance Paid' if paid>=total else 'Deposit Paid')
            if desired: c.execute('UPDATE bookings SET workflow_status_id=?,updated_at=? WHERE id=? AND company_id=?',(int(desired['id']),iso_now(),booking_id,cid))
        audit(database, context, cid, 'BOOKING_PAYMENT_RECORDED', 'booking', booking_id, after={'payment_id': payment_id, 'amount': amount, 'payment_date': payment_date, 'method': str(method['name']), 'reference': str(data.get('reference','')).strip()})
        return RedirectResponse(f'/operations/bookings/{booking_id}?message=Payment+recorded', 303)


def enquiry_conversion_panel(database, context, enquiry_id: int) -> str:
    cid = int(working_company(context))
    existing = one(database, 'SELECT id,reference FROM bookings WHERE company_id=? AND enquiry_id=? ORDER BY id DESC LIMIT 1', (cid, enquiry_id))
    if existing:
        return f'<div class="card"><h2>Booking</h2><p>This Enquiry has been converted to <a href="/operations/bookings/{int(existing["id"])}"><strong>{esc(existing["reference"])}</strong></a>.</p></div>'
    enquiry = one(database, 'SELECT workflow_status_id FROM enquiries WHERE company_id=? AND id=?', (cid, enquiry_id))
    quote_statuses = rows(database, "SELECT * FROM booking_status_definitions WHERE company_id=? AND active=1 AND internal_state='HELD' ORDER BY display_order,id", (cid,))
    quote_html = ''
    if quote_statuses:
        current_id = int(enquiry['workflow_status_id'] or 0) if enquiry else 0
        quote_default = next((q for q in quote_statuses if int(q['id']) == current_id), quote_statuses[0])
        quote_opts = ''.join(f'<option value="{int(q["id"])}" {"selected" if int(q["id"])==int(quote_default["id"]) else ""}>{esc(q["name"])}</option>' for q in quote_statuses)
        quote_html = f'''<div class="card"><h2>Keep as Quote</h2><p>Store this priced Enquiry while it is awaiting sending or a decision from the customer. This does not create a Booking.</p><form method="post" action="/operations/enquiries/{enquiry_id}/quote-status"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><div class="grid"><div><label>Quote / Enquiry Status</label><select name="workflow_status_id">{quote_opts}</select></div><div style="align-self:end"><button>KEEP AS QUOTE</button></div></div></form></div>'''
    booking_html = f'''<div class="card"><h2>Confirm Booking</h2><p>Continue to Take Payment. The Booking is not created until payment is recorded or deliberately confirmed without payment.</p><form method="post" action="/operations/enquiries/{enquiry_id}/convert"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><p><button>CONFIRM BOOKING</button></p></form></div>'''
    return quote_html + booking_html
