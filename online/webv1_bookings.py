from __future__ import annotations
from .sql import lock_inventory, execute_sql, insert_sql, local_schema, column_rows

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, form_data, layout
from .database import iso_now
from .setup015_calculator import _addon_rule
from .setup015_core import audit, context_for, one, require_csrf, rows, working_company
from .webv1_booking_status import default_status, status_by_id, status_by_system_code
from .webv1_payment_methods import payment_rule
from .webv1_status_availability import availability_state
from .webv1_booking_amendments import amendment_quote, apply_amendment
from .webv1_booking_finance import booking_financials, sync_payment_status

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
CREATE TABLE IF NOT EXISTS booking_refunds (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 company_id INTEGER NOT NULL,
 booking_id INTEGER NOT NULL,
 amount REAL NOT NULL CHECK(amount > 0),
 refund_date TEXT NOT NULL,
 method TEXT NOT NULL,
 reference TEXT NOT NULL DEFAULT '',
 notes TEXT NOT NULL DEFAULT '',
 created_by_user_id INTEGER,
 created_at TEXT NOT NULL,
 FOREIGN KEY(booking_id) REFERENCES bookings(id)
);
CREATE INDEX IF NOT EXISTS idx_booking_refunds_booking ON booking_refunds(company_id,booking_id,id);
CREATE TABLE IF NOT EXISTS booking_cancellations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 company_id INTEGER NOT NULL,
 booking_id INTEGER NOT NULL,
 reason TEXT NOT NULL,
 refund_decision TEXT NOT NULL,
 refund_amount REAL NOT NULL DEFAULT 0,
 retained_amount REAL NOT NULL DEFAULT 0,
 financial_note TEXT NOT NULL DEFAULT '',
 created_by_user_id INTEGER,
 created_at TEXT NOT NULL,
 UNIQUE(company_id,booking_id)
);
CREATE TABLE IF NOT EXISTS booking_amendments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    booking_id INTEGER NOT NULL,
    booking_element_id INTEGER NOT NULL,
    old_arrival_date TEXT NOT NULL,
    old_departure_date TEXT NOT NULL,
    new_arrival_date TEXT NOT NULL,
    new_departure_date TEXT NOT NULL,
    old_booking_total REAL NOT NULL,
    new_booking_total REAL NOT NULL,
    manual_discount REAL NOT NULL DEFAULT 0,
    calculation_json TEXT NOT NULL,
    created_by_user_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_booking_amendments_booking ON booking_amendments(company_id, booking_id, id);
'''


def initialise_booking_workflow(database) -> None:
    with database.connect() as c:
        local_schema(c, PAYMENT_SCHEMA)
        columns={str(r['name']) for r in column_rows(c, 'booking_elements')}
        for name,kind in (('original_arrival_date','TEXT'),('original_departure_date','TEXT'),('original_total_amount','REAL')):
            if name not in columns:
                execute_sql(c, f'ALTER TABLE booking_elements ADD COLUMN {name} {kind}')
        execute_sql(c, '''UPDATE booking_elements SET
            original_arrival_date=COALESCE(original_arrival_date,arrival_date),
            original_departure_date=COALESCE(original_departure_date,departure_date),
            original_total_amount=COALESCE(original_total_amount,total_amount)''')


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
    found = rows(database, 'SELECT * FROM booking_status_definitions WHERE company_id=:q185_0 AND active=1 AND lower(name) = lower(:q185_1) ORDER BY id LIMIT 1', (cid, name))
    return found[0] if found else None


def _booking(database, cid: int, booking_id: int):
    return one(database, 'SELECT b.*,c.first_name,c.last_name,c.email,c.phone,\n        s.name AS workflow_name,s.colour,s.internal_state,s.blocks_availability\n        FROM bookings b\n        LEFT JOIN customer_records c ON c.id=b.customer_id AND c.company_id=b.company_id\n        LEFT JOIN booking_status_definitions s ON s.id=b.workflow_status_id AND s.company_id=b.company_id\n        WHERE b.id=:q186_0 AND b.company_id=:q186_1', (booking_id, cid))


def _next_reference(connection, cid: int) -> str:
    prefix = datetime.now(timezone.utc).astimezone(ZoneInfo('Europe/Paris')).strftime('DB%y')
    rows_found = execute_sql(connection, 'SELECT reference FROM bookings WHERE company_id=:q209_0 AND reference LIKE :q209_1 ORDER BY id DESC LIMIT 100', (cid, prefix + '-%')).fetchall()
    highest = 0
    for row in rows_found:
        try:
            highest = max(highest, int(str(row['reference']).rsplit('-', 1)[1]))
        except (ValueError, IndexError):
            pass
    return f'{prefix}-{highest + 1:05d}'


def _conversion_statuses(database, cid: int):
    return rows(database, "SELECT * FROM booking_status_definitions\n        WHERE company_id=:q187_0 AND active=1 AND internal_state IN ('RESERVED','CONFIRMED','ON_SITE')\n        ORDER BY CASE internal_state WHEN 'RESERVED' THEN 1 WHEN 'CONFIRMED' THEN 2 ELSE 3 END, display_order,id", (cid,))


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
    data=rows(database,'SELECT ee.*,se.name AS element_name,se.pricing_method FROM enquiry_elements ee JOIN setup_elements se ON se.id=ee.element_id AND se.company_id=ee.company_id WHERE ee.company_id=:q188_0 AND ee.enquiry_id=:q188_1 ORDER BY ee.sort_order,ee.id',(cid,enquiry_id))
    if data:return data
    legacy=one(database,"SELECT e.id AS enquiry_id,e.company_id,er.element_type,er.element_id,e.arrival_date,e.departure_date,'' AS lead_name,e.party_size,er.provisional_total,er.pricing_snapshot_json,se.name AS element_name,se.pricing_method FROM enquiries e JOIN enquiry_requests er ON er.enquiry_id=e.id AND er.company_id=e.company_id JOIN setup_elements se ON se.id=er.element_id AND se.company_id=er.company_id WHERE e.id=:q189_0 AND e.company_id=:q189_1",(enquiry_id,cid))
    return [legacy] if legacy is not None else []

def _write_booking(c,database,context,cid,enquiry,elements,workflow_status_id):
    now=iso_now();total=sum(float(e['provisional_total'] or 0) for e in elements);reference=_next_reference(c,cid)
    arrival=min(str(e['arrival_date']) for e in elements);departure=max(str(e['departure_date']) for e in elements)
    booking_id=int(insert_sql(c, 'INSERT INTO bookings (company_id,reference,customer_id,enquiry_id,status,arrival_date,departure_date,currency,total_amount,pricing_snapshot_json,notes,created_at,updated_at,workflow_status_id) VALUES (:q210_0,:q210_1,:q210_2,:q210_3,:q210_4,:q210_5,:q210_6,:q210_7,:q210_8,:q210_9,:q210_10,:q210_11,:q210_12,:q210_13)',(cid,reference,enquiry['customer_id'],enquiry['id'],'confirmed',arrival,departure,'EUR',total,json.dumps({'multi_element':len(elements)>1,'element_count':len(elements),'total':total},separators=(',',':')),enquiry['notes'] or '',now,now,workflow_status_id)).lastrowid)
    for ee in elements:
        try:snapshot=json.loads(ee['pricing_snapshot_json'] or '{}')
        except (TypeError,json.JSONDecodeError):snapshot={}
        element_amount=_snapshot_line_amount(snapshot,str(ee['element_name'] or ''))
        beid=int(insert_sql(c, 'INSERT INTO booking_elements(company_id,booking_id,element_id,arrival_date,departure_date,pricing_method_snapshot,unit_price_snapshot,total_amount,pricing_snapshot_json,lead_name) VALUES (:q214_0,:q214_1,:q214_2,:q214_3,:q214_4,:q214_5,:q214_6,:q214_7,:q214_8,:q214_9)',(cid,booking_id,ee['element_id'],ee['arrival_date'],ee['departure_date'],ee['pricing_method'] or '',0,element_amount,ee['pricing_snapshot_json'],ee['lead_name'] or '')).lastrowid)
        execute_sql(c, 'UPDATE booking_elements SET original_arrival_date=arrival_date,original_departure_date=departure_date,original_total_amount=total_amount WHERE id=:q195_0',(beid,))
        year=int(snapshot.get('year') or str(ee['arrival_date'])[:4])
        if 'id' in ee.keys():
            people_rows=execute_sql(c, 'SELECT ep.person_type_id,ep.quantity,pt.name FROM enquiry_element_people ep JOIN setup_person_types pt ON pt.id=ep.person_type_id AND pt.company_id=ep.company_id WHERE ep.enquiry_element_id=:q223_0 AND ep.company_id=:q223_1',(ee['id'],cid)).fetchall()
        else:
            people_rows=execute_sql(c, 'SELECT ep.person_type_id,ep.quantity,pt.name FROM enquiry_people ep JOIN setup_person_types pt ON pt.id=ep.person_type_id AND pt.company_id=ep.company_id WHERE ep.enquiry_id=:q224_0 AND ep.company_id=:q224_1',(enquiry['id'],cid)).fetchall()
        for p in people_rows:
            pr=execute_sql(c, 'SELECT rate FROM setup_person_prices WHERE company_id=:q225_0 AND year=:q225_1 AND element_id=:q225_2 AND person_type_id=:q225_3',(cid,year,ee['element_id'],p['person_type_id'])).fetchone();unit=float(pr['rate']) if pr else 0.0
            execute_sql(c, 'INSERT INTO booking_people(company_id,booking_element_id,person_type_id,quantity,unit_price_snapshot,total_amount) VALUES (:q211_0,:q211_1,:q211_2,:q211_3,:q211_4,:q211_5)',(cid,beid,p['person_type_id'],p['quantity'],unit,_snapshot_line_amount(snapshot,str(p['name']))))
        element=execute_sql(c, 'SELECT * FROM setup_elements WHERE company_id=:q215_0 AND id=:q215_1',(cid,ee['element_id'])).fetchone()
        for aid in [int(x) for x in (snapshot.get('selected_addons') or [])]:
            addon=execute_sql(c, 'SELECT * FROM setup_addons WHERE company_id=:q226_0 AND id=:q226_1',(cid,aid)).fetchone()
            if addon is None:continue
            qr=(execute_sql(c, 'SELECT quantity FROM enquiry_element_addons WHERE enquiry_element_id=:q229_0 AND company_id=:q229_1 AND addon_id=:q229_2',(ee['id'],cid,aid)).fetchone() if 'id' in ee.keys() else execute_sql(c, 'SELECT quantity FROM enquiry_addons WHERE enquiry_id=:q230_0 AND company_id=:q230_1 AND addon_id=:q230_2',(enquiry['id'],cid,aid)).fetchone());qty=int(qr['quantity']) if qr else 0;rule=_addon_rule(database,cid,year,element,aid);amount=_snapshot_line_amount(snapshot,str(addon['name']))
            detail={'rule':dict(rule),'when':(snapshot.get('addon_when') or {}).get(str(aid),(snapshot.get('addon_when') or {}).get(aid)),'days':(snapshot.get('addon_days') or {}).get(str(aid),(snapshot.get('addon_days') or {}).get(aid,{})),'people':(snapshot.get('addon_people') or {}).get(str(aid),(snapshot.get('addon_people') or {}).get(aid,{})),'person_days':(snapshot.get('addon_person_days') or {}).get(str(aid),(snapshot.get('addon_person_days') or {}).get(aid,{})),'frozen_amount':amount}
            execute_sql(c, 'INSERT INTO booking_addons(company_id,booking_element_id,addon_id,quantity,pricing_method_snapshot,unit_price_snapshot,total_amount,rule_snapshot_json) VALUES (:q212_0,:q212_1,:q212_2,:q212_3,:q212_4,:q212_5,:q212_6,:q212_7)',(cid,beid,aid,qty,addon['pricing_method'],float(rule.get('rate') or 0),amount,json.dumps(detail,separators=(',',':'))))
    execute_sql(c, "UPDATE enquiries SET status='converted',availability_expires_at=NULL,updated_at=:q190_0 WHERE id=:q190_1 AND company_id=:q190_2",(now,enquiry['id'],cid))
    # Availability->Enquiry normally releases basket holds when the Enquiry is
    # saved. Conversion defensively enforces the same lifecycle invariant for
    # legacy/manually-created Enquiries without disturbing unrelated basket
    # items in the operator's session.
    token=str(context['token']) if 'token' in context.keys() else ''
    if token:
        for ee in elements:
            stale_holds=execute_sql(c,
                'SELECT id FROM element_holds\n                   WHERE company_id=:q227_0 AND session_token=:q227_1 AND element_id=:q227_2\n                     AND arrival_date=:q227_3 AND departure_date=:q227_4',
                (cid,token,ee['element_id'],ee['arrival_date'],ee['departure_date'])
            ).fetchall()
            for hold in stale_holds:
                hold_id=int(hold['id'])
                execute_sql(c, 'DELETE FROM hold_requirement_people WHERE hold_id=:q216_0',(hold_id,))
                execute_sql(c, 'DELETE FROM hold_requirement_addons WHERE hold_id=:q217_0',(hold_id,))
                execute_sql(c, 'DELETE FROM element_holds WHERE id=:q218_0 AND company_id=:q218_1',(hold_id,cid))
    return booking_id,reference,total

def _conversion_enquiry(database,context,cid,enquiry_id,workflow_status_id):
    enquiry=one(database,'SELECT * FROM enquiries WHERE id=:q191_0 AND company_id=:q191_1',(enquiry_id,cid))
    if enquiry is None:raise ValueError('Enquiry not found.')
    if str(enquiry['status'])=='converted':raise ValueError('This Enquiry is already converted.')
    status=status_by_id(database,cid,workflow_status_id)
    if status is None or not int(status['active']) or str(status['internal_state']) not in {'RESERVED','CONFIRMED','ON_SITE'}:raise ValueError('Choose a valid Booking Status.')
    elements=_enquiry_elements(database,cid,enquiry_id)
    if not elements:raise ValueError('Choose at least one specific Element and stay dates before converting this Enquiry.')
    # One enquiry must never allocate the same physical Element twice for
    # overlapping nights, even though each row excludes its own enquiry when
    # checking external inventory conflicts.
    for index, first in enumerate(elements):
        for second in elements[index + 1:]:
            if int(first['element_id']) == int(second['element_id']) and str(first['arrival_date']) < str(second['departure_date']) and str(second['arrival_date']) < str(first['departure_date']):
                raise ValueError('The same Element is allocated to overlapping dates within this Enquiry. Correct the Enquiry before confirming.')
    token=str(context['token']) if 'token' in context.keys() else ''
    for ee in elements:
        if ee['provisional_total'] is None:raise ValueError('Calculate and save every Element price before converting this Enquiry.')
        state=availability_state(database,cid,int(ee['element_id']),str(ee['arrival_date']),str(ee['departure_date']),session_token=token,exclude_enquiry_id=enquiry_id)
        if not state['available']:raise ValueError(str(ee['element_name'])+' is no longer available: '+str(state['reason']))
        try:snapshot=json.loads(ee['pricing_snapshot_json'] or '{}')
        except (TypeError,json.JSONDecodeError):snapshot={}
        if not snapshot:raise ValueError(str(ee['element_name'])+' does not contain a frozen price snapshot. Recalculate it first.')
    return enquiry,elements

def _lock_and_verify_conversion(c, cid, enquiry_id, elements):
    """Serialise booking conversions and recheck blocking inventory in the write transaction."""
    from .webv1_status_availability import _booking_conflict, _enquiry_conflict
    from .webv1_reservation_core import migrated_inventory_conflict
    lock_inventory(c, cid)
    existing = execute_sql(c, 'SELECT id FROM bookings WHERE company_id=:q213_0 AND enquiry_id=:q213_1 LIMIT 1', (cid, enquiry_id)).fetchone()
    if existing:
        raise ValueError('This Enquiry has already been converted into a Booking.')
    for element in elements:
        eid = int(element['element_id'])
        arrival, departure = str(element['arrival_date']), str(element['departure_date'])
        if migrated_inventory_conflict(c, cid, eid, arrival, departure):
            raise ValueError('This Element is allocated to another reservation. Refresh the Enquiry.')
        if _booking_conflict(c, cid, eid, arrival, departure):
            raise ValueError('This Element has been booked by another reservation. Refresh the Enquiry.')
        if _enquiry_conflict(c, cid, eid, arrival, departure, exclude_enquiry_id=enquiry_id):
            raise ValueError('This Element is held by another Enquiry. Refresh the Enquiry.')


def convert_enquiry(database,context,cid,enquiry_id,workflow_status_id):
    enquiry,elements=_conversion_enquiry(database,context,cid,enquiry_id,workflow_status_id)
    with database.connect() as c:
        _lock_and_verify_conversion(c,cid,enquiry_id,elements)
        booking_id,reference,total=_write_booking(c,database,context,cid,enquiry,elements,workflow_status_id)
    audit(database,context,cid,'ENQUIRY_CONVERTED_TO_BOOKING','enquiry',enquiry_id,after={'booking_id':booking_id,'reference':reference,'element_count':len(elements)});audit(database,context,cid,'BOOKING_CREATED','booking',booking_id,after={'reference':reference,'enquiry_id':enquiry_id,'workflow_status_id':workflow_status_id,'total_amount':total})
    return booking_id

def convert_enquiry_with_payment(database,context,cid,enquiry_id,workflow_status_id,*,amount,payment_date,method,reference='',notes=''):
    enquiry,elements=_conversion_enquiry(database,context,cid,enquiry_id,workflow_status_id);total=sum(float(e['provisional_total'] or 0) for e in elements);required,_=payment_due(database,cid,total,min(str(e['arrival_date']) for e in elements))
    amount=round(float(amount),2)
    if amount < round(required,2):raise ValueError(f'Payment must be at least the required amount of {_money(required)}.')
    if amount > round(total,2):raise ValueError(f'Payment cannot exceed the booking total of {_money(total)}.')
    with database.connect() as c:
        _lock_and_verify_conversion(c,cid,enquiry_id,elements)
        booking_id,bref,total=_write_booking(c,database,context,cid,enquiry,elements,workflow_status_id)
        payment_id=int(insert_sql(c, 'INSERT INTO booking_payments(company_id,booking_id,amount,payment_date,method,reference,notes,created_by_user_id,created_at) VALUES (:q219_0,:q219_1,:q219_2,:q219_3,:q219_4,:q219_5,:q219_6,:q219_7,:q219_8)',(cid,booking_id,amount,payment_date,str(method['name']),reference,notes,context['user_id'],iso_now())).lastrowid)
    audit(database,context,cid,'ENQUIRY_CONVERTED_TO_BOOKING','enquiry',enquiry_id,after={'booking_id':booking_id,'reference':bref,'element_count':len(elements)});audit(database,context,cid,'BOOKING_CREATED','booking',booking_id,after={'reference':bref,'enquiry_id':enquiry_id,'workflow_status_id':workflow_status_id,'total_amount':total});audit(database,context,cid,'BOOKING_PAYMENT_RECORDED','booking',booking_id,after={'payment_id':payment_id,'amount':amount,'payment_date':payment_date,'method':str(method['name'])})
    return booking_id


def _history(database, cid: int, booking_id: int):
    return rows(database, "SELECT a.*,u.first_name,u.last_name FROM audit_log a\n        LEFT JOIN users u ON u.id=a.actor_user_id\n        WHERE a.company_id=:q192_0 AND a.entity_type='booking' AND a.entity_id=:q192_1\n        ORDER BY a.id DESC", (cid, str(booking_id)))


def register_booking_routes(app) -> None:
    database = app.state.database

    @app.get('/operations/bookings', response_class=HTMLResponse)
    def booking_register(request: Request):
        context = context_for(database, request); cid = int(working_company(context))
        data = rows(database, 'SELECT b.*,c.first_name,c.last_name,s.name AS workflow_name,s.colour\n            FROM bookings b LEFT JOIN customer_records c ON c.id=b.customer_id AND c.company_id=b.company_id\n            LEFT JOIN booking_status_definitions s ON s.id=b.workflow_status_id AND s.company_id=b.company_id\n            WHERE b.company_id=:q196_0 ORDER BY b.arrival_date,b.id', (cid,))
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
        enquiry=one(database, 'SELECT e.*,c.first_name,c.last_name FROM enquiries e LEFT JOIN customer_records c ON c.id=e.customer_id AND c.company_id=e.company_id WHERE e.id=:q197_0 AND e.company_id=:q197_1',(enquiry_id,cid))
        if enquiry is None: return RedirectResponse('/operations/enquiries',303)
        if _unresolved_recovery_count(database,cid,enquiry_id):
            return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error=Resolve+all+unavailable+Elements+before+confirming+this+Enquiry',303)
        elements=_enquiry_elements(database,cid,enquiry_id)
        if not elements:return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error=No+bookable+Elements+saved',303)
        methods=rows(database,'SELECT * FROM payment_method_definitions WHERE company_id=:q198_0 AND active=1 ORDER BY display_order,name',(cid,))
        options=''.join(f'<option value="{int(m["id"])}">{esc(m["name"])}</option>' for m in methods)
        total=sum(float(e['provisional_total'] or 0) for e in elements); first_arrival=min(str(e['arrival_date']) for e in elements); required,reason=payment_due(database,cid,total,first_arrival); customer=(str(enquiry['first_name'] or '')+' '+str(enquiry['last_name'] or '')).strip() or 'Customer'; party=int(enquiry['party_size'] or 0)
        pending='Balance Pending' if round(required,2)>=round(total,2) else 'Deposit Pending'
        element_cards=[]
        for e in elements:
            try: snap=json.loads(e['pricing_snapshot_json'] or '{}')
            except (TypeError,json.JSONDecodeError): snap={}
            people_bits=[]
            if 'id' in e.keys():
                people_rows=rows(database,'SELECT ep.quantity,pt.name FROM enquiry_element_people ep JOIN setup_person_types pt ON pt.id=ep.person_type_id AND pt.company_id=ep.company_id WHERE ep.enquiry_element_id=:q220_0 AND ep.company_id=:q220_1 ORDER BY pt.name',(e['id'],cid))
                people_bits=[f'{esc(p["name"])} × {int(p["quantity"])}' for p in people_rows if int(p['quantity'] or 0)>0]
            addons=[]
            for line in snap.get('lines') or []:
                item=str(line.get('item',''))
                if item and item!=str(e['element_name']) and item!='Duration discount' and not any(item==str(p['name']) for p in (people_rows if 'id' in e.keys() else [])):
                    amount=float(line.get("amount",0) or 0)
                    addons.append(esc(item) + (f' ({_money(amount)})' if abs(amount) >= 0.005 else ''))
            lead=str(e['lead_name'] or '').strip() or customer
            element_cards.append(f'<div class="frozen-element"><h3>{esc(e["element_name"])}</h3><p><strong>Stay:</strong> {_fmt_day(e["arrival_date"])} to {_fmt_day(e["departure_date"])}<br><strong>Lead Passenger:</strong> {esc(lead)}<br><strong>People:</strong> {", ".join(people_bits) or "—"}<br><strong>Add-ons:</strong> {", ".join(addons) or "—"}<br><strong>Element total:</strong> {_money(e["provisional_total"])}</p></div>')
        element_summary=''.join(element_cards)
        method_block=f'<div><label>Payment Method</label><select name="payment_method_id" required>{options}</select></div>' if methods else '<div class="error">No active Payment Methods. Add one in Setup before taking payment.</div>'
        body=f'''<h1>Confirm Booking</h1><p><a href="/operations/enquiries/{enquiry_id}">Back to Enquiry</a></p><div class="card"><h2>{esc(customer)}</h2><p><strong>Stay:</strong> {_fmt_day(enquiry['arrival_date'])} to {_fmt_day(enquiry['departure_date'])}<br><strong>Party:</strong> {party}<br><strong>Booking total:</strong> {_money(total)}<br><strong>Payment Required Now:</strong> {_money(required)}<br><strong>Reason:</strong> {esc(reason)}<br><strong>Payment status:</strong> {pending}</p></div><div class="card"><h2>Booking Elements</h2>{element_summary}</div><div class="card"><h2>Take Payment</h2><p>Minimum required now: <strong>{_money(required)}</strong>. You may take any amount from this minimum up to the full booking total.</p><form method="post" action="/operations/enquiries/{enquiry_id}/confirm-payment"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><div class="grid"><div><label>Amount</label><input name="amount" type="number" step="0.01" min="{required:.2f}" max="{total:.2f}" value="{required:.2f}" required></div><div><label>Payment Date</label><input type="date" name="payment_date" value="{_local_today().isoformat()}" required></div>{method_block}<div><label>Reference</label><input name="reference"></div></div><label>Notes</label><input name="notes"><p><button {'disabled' if not methods else ''}>CONFIRM BOOKING</button></p></form></div><div class="card"><h2>Exception: confirm without payment</h2><p>A reason is compulsory and is written to the audit trail.</p><form method="post" action="/operations/enquiries/{enquiry_id}/confirm-without-payment"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><label>Reason</label><input name="reason" required><p><button class="warning">CONFIRM WITHOUT PAYMENT</button></p></form></div>'''
        return layout('Confirm Booking',body,context)

    @app.post('/operations/enquiries/{enquiry_id}/confirm-payment')
    async def confirm_payment(enquiry_id:int,request:Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        try:
            method_id=int(data.get('payment_method_id','')); amount=round(float(str(data.get('amount','')).replace(',','.')),2)
            if amount<=0: raise ValueError
            datetime.strptime(str(data.get('payment_date','')),'%Y-%m-%d')
        except (TypeError,ValueError): return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error=Enter+valid+payment+details',303)
        method=one(database,'SELECT * FROM payment_method_definitions WHERE company_id=:q199_0 AND id=:q199_1 AND active=1',(cid,method_id))
        if method is None: return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error=Choose+an+active+Payment+Method',303)
        if str(method['method_type'])=='card': return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error=Card+provider+connection+is+not+configured+yet',303)
        total=sum(float(e['provisional_total'] or 0) for e in _enquiry_elements(database,cid,enquiry_id)); status=status_by_system_code(database,cid,'BALANCE_PAID' if round(amount,2)>=round(total,2) else 'DEPOSIT_PAID') or default_status(database,cid,'CONFIRMED')
        try: booking_id=convert_enquiry_with_payment(database,context,cid,enquiry_id,int(status['id']),amount=amount,payment_date=str(data.get('payment_date','')),method=method,reference=str(data.get('reference','')).strip(),notes=str(data.get('notes','')).strip())
        except ValueError as exc: return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error={esc(str(exc))}',303)
        return RedirectResponse(f'/operations/bookings/{booking_id}?created=1',303)

    @app.post('/operations/enquiries/{enquiry_id}/confirm-without-payment')
    async def confirm_without_payment(enquiry_id:int,request:Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data); reason=str(data.get('reason','')).strip()
        if not reason: return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error=A+reason+is+required+to+confirm+without+payment',303)
        pending=status_by_system_code(database,cid,'PAYMENT_PENDING') or default_status(database,cid,'RESERVED')
        try: booking_id=convert_enquiry(database,context,cid,enquiry_id,int(pending['id']))
        except (TypeError,ValueError) as exc: return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error={esc(str(exc))}',303)
        audit(database,context,cid,'BOOKING_CONFIRMED_WITHOUT_PAYMENT','booking',booking_id,after={'reason':reason,'enquiry_id':enquiry_id})
        return RedirectResponse(f'/operations/bookings/{booking_id}?created=1&message=Confirmed+without+payment',303)


    @app.get('/operations/bookings/{booking_id}', response_class=HTMLResponse)
    def detail(booking_id: int, request: Request, created: int = 0, message: str = ''):
        context = context_for(database, request); cid = int(working_company(context)); b = _booking(database, cid, booking_id)
        if b is None:
            return HTMLResponse(layout('Booking not found', '<div class="error">Booking not found.</div>', context), 404)
        elements = rows(database, 'SELECT be.*,se.name AS element_name,se.element_type FROM booking_elements be JOIN setup_elements se ON se.id=be.element_id AND se.company_id=be.company_id WHERE be.booking_id=:q200_0 AND be.company_id=:q200_1 ORDER BY be.id', (booking_id, cid))
        people = rows(database, 'SELECT bp.*,pt.name FROM booking_people bp JOIN booking_elements be ON be.id=bp.booking_element_id JOIN setup_person_types pt ON pt.id=bp.person_type_id AND pt.company_id=bp.company_id WHERE be.booking_id=:q201_0 AND bp.company_id=:q201_1 ORDER BY pt.name', (booking_id, cid))
        addons = rows(database, 'SELECT ba.*,a.name FROM booking_addons ba JOIN booking_elements be ON be.id=ba.booking_element_id JOIN setup_addons a ON a.id=ba.addon_id AND a.company_id=ba.company_id WHERE be.booking_id=:q202_0 AND ba.company_id=:q202_1 ORDER BY a.name', (booking_id, cid))
        payments = rows(database, 'SELECT * FROM booking_payments WHERE company_id=:q203_0 AND booking_id=:q203_1 ORDER BY payment_date,id', (cid, booking_id))
        financials = booking_financials(database, cid, booking_id)
        paid = financials['paid']; balance = financials['outstanding']
        notice = '<div class="ok">Booking created from Enquiry. Prices are now frozen.</div>' if created else (f'<div class="ok">{esc(message)}</div>' if message else '')
        amendment_rows = rows(database, 'SELECT * FROM booking_amendments WHERE company_id=:q204_0 AND booking_id=:q204_1 ORDER BY id', (cid, booking_id))
        latest_amendment = {}
        for ar in amendment_rows:
            latest_amendment[int(ar['booking_element_id'])] = ar
        manual_by_element = {eid: float(ar['manual_discount'] or 0) for eid, ar in latest_amendment.items()}
        element_cards = []
        for element in elements:
            element_people = [p for p in people if int(p["booking_element_id"]) == int(element["id"])]
            element_addons = [a for a in addons if int(a["booking_element_id"]) == int(element["id"])]
            person_text = ', '.join(f'{esc(p["name"])} × {int(p["quantity"])} ({_money(p["total_amount"])})' for p in element_people) or '—'
            addon_text = ', '.join(f'{esc(a["name"])} × {int(a["quantity"])}' + (f' ({_money(a["total_amount"])})' if abs(float(a["total_amount"] or 0)) >= 0.005 else '') for a in element_addons) or '—'
            try:
                frozen_snapshot = json.loads(element["pricing_snapshot_json"] or '{}')
            except (TypeError, json.JSONDecodeError):
                frozen_snapshot = {}
            frozen_total = float(frozen_snapshot.get("total") or frozen_snapshot.get("final_amount") or 0)
            if frozen_total <= 0:
                frozen_total = float(element["total_amount"] or 0) + sum(float(p["total_amount"] or 0) for p in element_people) + sum(float(a["total_amount"] or 0) for a in element_addons)
            lead = str(element["lead_name"] or '').strip() or '—'
            latest = latest_amendment.get(int(element['id']))
            current_total = frozen_total
            staff_adjustment = ''
            if latest is not None:
                try:
                    calc = json.loads(latest['calculation_json'] or '{}')
                except (TypeError, json.JSONDecodeError):
                    calc = {}
                current_total = float(calc.get('new_element_total') or frozen_total)
                manual = float(latest['manual_discount'] or 0)
                if manual > 0:
                    staff_adjustment = f'<br><strong>Staff discount:</strong> −{_money(manual)}'
                staff_adjustment += f'<br><strong>Current element total:</strong> {_money(current_total)}'
            element_cards.append(
                f'<div class="frozen-element"><h3>{esc(element["element_name"])}</h3>'
                f'<p><strong>Type:</strong> {esc(element["element_type"])}<br>'
                f'<strong>Arrival:</strong> {_fmt_day(element["arrival_date"])}<br>'
                f'<strong>Departure:</strong> {_fmt_day(element["departure_date"])}<br>'
                f'<strong>Lead Passenger:</strong> {esc(lead)}<br>'
                f'<strong>Frozen total:</strong> {_money(frozen_total)}{staff_adjustment}</p>'
                f'<p><strong>People:</strong> {person_text}</p>'
                f'<p><strong>Add-ons:</strong> {addon_text}</p></div>'
            )
        element_html = ''.join(element_cards) or '<p class="muted">No frozen elements.</p>'
        payment_rows = ''.join(f'<tr><td>{_fmt_day(p["payment_date"])}</td><td>{_money(p["amount"])}</td><td>{esc(p["method"] or "—")}</td><td>{esc(p["reference"] or "—")}</td><td>{esc(p["notes"] or "—")}</td></tr>' for p in payments) or '<tr><td colspan="5" class="muted">No payments recorded.</td></tr>'
        hist = _history(database, cid, booking_id)
        def history_text(h):
            try: after=json.loads(h['after_json'] or '{}')
            except (TypeError,json.JSONDecodeError): after={}
            action=str(h['action'] or '')
            if action=='BOOKING_PAYMENT_RECORDED':
                return f"Recorded payment {_money(after.get('amount',0))}" + (f" by {after.get('method')}" if after.get('method') else '')
            if action=='BOOKING_AMENDED':
                old=after.get('old_booking_total'); new=after.get('new_booking_total')
                text=f"Amended booking to {_fmt_day(after.get('new_arrival'))}–{_fmt_day(after.get('new_departure'))}"
                if old is not None and new is not None: text+=f"; total {_money(old)} → {_money(new)}"
                manual=float(after.get('manual_discount_private') or 0)
                if manual: text+=f"; staff discount {_money(manual)}"
                return text
            if action=='BOOKING_REFUND_RECORDED':
                return f"Recorded refund {_money(after.get('amount',0))} by {after.get('method','')} (reference {after.get('reference','')})"
            if action=='BOOKING_CANCELLED':
                return f"Cancelled permanently: {after.get('reason','')}; refund decision {after.get('refund_decision','')}; planned refund {_money(after.get('refund_amount',0))}; retained {_money(after.get('retained_amount',0))}"
            if action=='BOOKING_CREATED': return f"Created booking {after.get('reference','')}".strip()
            if action=='BOOKING_CONFIRMED_WITHOUT_PAYMENT': return 'Confirmed booking without payment' + (f": {after.get('reason')}" if after.get('reason') else '')
            labels={'BOOKING_STATUS_CHANGED':'Booking status changed','BOOKING_STATUS_AUTOMATIC':'Booking status updated automatically'}
            return labels.get(action, action.replace('_',' ').title())
        history_rows = ''.join(f'<tr><td>{esc(_fmt_when(h["created_at"]))}</td><td>{esc(((h["first_name"] or "")+" "+(h["last_name"] or "")).strip() or h["actor_role"] or "System")}</td><td>{esc(history_text(h))}</td></tr>' for h in hist) or '<tr><td colspan="3" class="muted">No history yet.</td></tr>'
        cancelled = str(b['status'] or '').lower() == 'cancelled' or str(b['internal_state'] or '') == 'RELEASED'
        cancellation = one(database, 'SELECT * FROM booking_cancellations WHERE company_id=:q205_0 AND booking_id=:q205_1', (cid, booking_id))
        if cancelled:
            cancellation_panel = (f'<div class="card"><h2>Cancelled — permanent</h2>'
                f'<p>Accommodation released. Customer, original booking, payment and history records remain accessible.</p>'
                f'<p><strong>Reason:</strong> {esc(cancellation["reason"]) if cancellation else "Legacy cancellation"}<br>'
                f'<strong>Refund decision:</strong> {esc(cancellation["refund_decision"]) if cancellation else "Not recorded"}<br>'
                f'<strong>Planned refund:</strong> {_money(cancellation["refund_amount"]) if cancellation else "Not recorded"}<br>'
                f'<strong>Retained:</strong> {_money(cancellation["retained_amount"]) if cancellation else "Not recorded"}</p>'
                f'<p>Refund decisions do not issue refunds. Financial settlement requires a separate recorded action.</p></div>')
        else:
            cancellation_panel = (f'<div class="card"><h2>Cancel Booking</h2>'
                f'<p>Permanent action: all reserved Elements will be released and cannot be reinstated.</p>'
                f'<p><a href="/operations/bookings/{booking_id}/cancel">Review Cancellation</a></p></div>')
        refunds = rows(database, 'SELECT * FROM booking_refunds WHERE company_id=:q206_0 AND booking_id=:q206_1 ORDER BY id', (cid,booking_id))
        refunded = round(sum(float(x['amount']) for x in refunds),2)
        refund_rows = ''.join(f'<tr><td>{esc(x["refund_date"])}</td><td>{_money(x["amount"])}</td><td>{esc(x["method"])}</td><td>{esc(x["reference"])}</td><td>{esc(x["notes"])}</td></tr>' for x in refunds) or '<tr><td colspan="5">No refunds recorded.</td></tr>'
        refundable = round(max(0.0,float(cancellation['refund_amount'])-refunded),2) if cancellation else 0.0
        refund_panel = (f'<div class="card"><h2>Refund Settlement</h2><p>Approved refund: {_money(cancellation["refund_amount"])}; recorded as refunded: {_money(refunded)}; remaining to refund: {_money(refundable)}.</p>'
            f'<table><thead><tr><th>Date</th><th>Amount</th><th>Method</th><th>Reference</th><th>Notes</th></tr></thead><tbody>{refund_rows}</tbody></table>'
            + (f'<form method="post" action="/operations/bookings/{booking_id}/refunds"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}">'
               f'<label>Amount actually refunded (€)</label><input type="number" name="amount" min="0.01" max="{refundable:.2f}" step="0.01" value="{refundable:.2f}" required>'
               f'<label>Refund date</label><input type="date" name="refund_date" value="{_local_today().isoformat()}" required>'
               f'<label>Refund method</label><input name="method" required><label>Transaction reference</label><input name="reference" required>'
               f'<label>Notes</label><textarea name="notes"></textarea>'
               f'<label><input type="checkbox" name="acknowledge" value="yes" required> I confirm this refund has already been made outside DBS.</label>'
               f'<p><button>Record Completed Refund</button></p></form>' if refundable>0 else '<p>No further approved refund remains to record.</p>')+'</div>') if cancellation else ''
        body = f'''<h1>Booking {esc(b['reference'])}</h1><p><a href="/operations/bookings">← Bookings</a> &nbsp; <a href="/availability/calendar-v2?start={esc(b['arrival_date'])}">Return to Master Calendar</a></p>{notice}
        <div class="grid"><div class="card"><h2>Customer</h2><p><strong>{esc((str(b['first_name'] or '')+' '+str(b['last_name'] or '')).strip() or 'Customer')}</strong><br>{esc(b['email'] or '—')}<br>{esc(b['phone'] or '—')}</p></div>
        <div class="card"><h2>Stay</h2><p><strong>Arrival:</strong> {_fmt_day(b['arrival_date'])}<br><strong>Departure:</strong> {_fmt_day(b['departure_date'])}<br><strong>Total:</strong> {_money(b['total_amount'])}<br><strong>Paid:</strong> {_money(paid)}<br><strong>Outstanding:</strong> {_money(balance)}</p></div></div>
        <div class="card"><h2>Booking Status</h2><p><strong>{esc(b['workflow_name'] or b['status'])}</strong></p><p class="muted">Status is controlled automatically by DBS from the booking lifecycle and account balance.</p></div>
        {cancellation_panel}
        {refund_panel}
        {'<div class="card"><h2>Accommodation locked</h2><p>This booking is permanently cancelled. Financial records and history remain accessible.</p></div>' if cancelled else '<div class="card"><h2>Amend Booking</h2>'}<p class="muted">Preview the price before applying. Original frozen booking values remain in history.</p>{''.join(f'<form method="post" action="/operations/bookings/{booking_id}/amend-preview"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="booking_element_id" value="{int(e["id"])}"><h3>{esc(e["element_name"])}</h3><div class="grid"><div><label>Arrival</label><input type="date" name="arrival_date" value="{esc(e["arrival_date"])}" required></div><div><label>Departure</label><input type="date" name="departure_date" value="{esc(e["departure_date"])}" required></div><div><label>Staff discount</label><input type="number" name="manual_discount" min="0" step="0.01" value="{manual_by_element.get(int(e["id"]),0):.2f}"></div></div><p><button>Preview Amendment</button></p></form>' for e in elements)}{'' if cancelled else '</div>'}
        <div class="card"><h2>Frozen Booking</h2>{element_html}<p class="muted">Each element is shown from its own frozen snapshot. Later Setup price changes do not alter this Booking.</p></div>
        <div class="card"><h2>Payments</h2><table><thead><tr><th>Date</th><th>Amount</th><th>Method</th><th>Reference</th><th>Notes</th></tr></thead><tbody>{payment_rows}</tbody></table><h3>Record payment</h3><form method="post" action="/operations/bookings/{booking_id}/payments"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><div class="grid"><div><label>Amount</label><input name="amount" type="number" min="0.01" step="0.01" value="{balance:.2f}" required></div><div><label>Date</label><input name="payment_date" type="date" value="{_local_today().isoformat()}" required></div><div><label>Method</label><select name="payment_method_id" required>{''.join(f'<option value="{int(m["id"])}">{esc(m["name"])}</option>' for m in rows(database,'SELECT * FROM payment_method_definitions WHERE company_id=:q235_0 AND active=1 ORDER BY display_order,name',(cid,)))}</select></div><div><label>Reference</label><input name="reference"></div></div><label>Notes</label><input name="notes"><p><button>Record Payment</button></p></form></div>
        <div class="card"><h2>Booking History</h2><table><thead><tr><th>When</th><th>Who</th><th>What happened</th></tr></thead><tbody>{history_rows}</tbody></table></div>'''
        return layout(f'Booking {b["reference"]}', body, context)



    @app.get('/operations/bookings/{booking_id}/cancel', response_class=HTMLResponse)
    def cancellation_review(booking_id: int, request: Request):
        context=context_for(database,request); cid=int(working_company(context))
        b=_booking(database,cid,booking_id)
        if b is None: return HTMLResponse(layout('Not found','<p>Booking not found.</p>',context),404)
        if str(b['status'] or '').lower()=='cancelled' or str(b['internal_state'] or '')=='RELEASED':
            return RedirectResponse(f'/operations/bookings/{booking_id}?message=Already+cancelled',303)
        f=booking_financials(database,cid,booking_id)
        paid=round(float(f['paid']),2)
        body=f'''<h1>Permanent Booking Cancellation — {esc(b['reference'])}</h1>
        <div class="card"><p><strong>Customer:</strong> {esc(str(b['first_name'] or '')+' '+str(b['last_name'] or ''))}<br>
        <strong>Stay:</strong> {_fmt_day(b['arrival_date'])} to {_fmt_day(b['departure_date'])}<br>
        <strong>Booking total:</strong> {_money(b['total_amount'])}<br>
        <strong>Payments received:</strong> {_money(paid)}<br>
        <strong>Outstanding:</strong> {_money(f['outstanding'])}</p>
        <p><strong>WARNING: Cancellation is permanent and cannot be reversed. All Elements will be released.</strong></p>
        <form method="post" action="/operations/bookings/{booking_id}/cancel">
        <input type="hidden" name="csrf" value="{esc(context['csrf_token'])}">
        <label>Cancellation reason (required)</label><textarea name="reason" required></textarea>
        <label>Decision for payments already received</label>
        <select name="refund_decision" required>
        <option value="">Choose...</option><option value="full_refund">Full refund</option>
        <option value="partial_refund">Partial refund / cancellation fee</option>
        <option value="no_refund">No refund — retain payments</option></select>
        <label>Refund to be processed later (€)</label><input name="refund_amount" type="number" min="0" max="{paid:.2f}" step="0.01" required value="0.00">
        <label>Financial explanation</label><textarea name="financial_note" required></textarea>
        <label style="display:inline-flex;align-items:center;justify-content:flex-start;gap:8px;width:auto;max-width:100%;cursor:pointer"><input type="checkbox" name="acknowledge" value="yes" required style="width:22px;height:22px;min-width:22px;flex:0 0 22px;margin:0;cursor:pointer"> <span>I authorise cancellation and release of all Elements</span></label>
        <p><button>Confirm Permanent Cancellation</button> <a href="/operations/bookings/{booking_id}">Go back</a></p>
        </form></div>'''
        return layout('Cancel Booking',body,context)

    @app.post('/operations/bookings/{booking_id}/cancel')
    async def cancellation_apply(booking_id: int, request: Request):
        context=context_for(database,request); cid=int(working_company(context))
        data=await form_data(request); require_csrf(context,data)
        reason=str(data.get('reason','')).strip()
        note=str(data.get('financial_note','')).strip()
        decision=str(data.get('refund_decision','')).strip()
        if not reason or not note or data.get('acknowledge')!='yes' or decision not in ('full_refund','partial_refund','no_refund'):
            return HTMLResponse(layout('Cancellation not authorised','<p>Reason, financial explanation, refund decision and authorisation are all required.</p>',context),400)
        try: refund=round(float(str(data.get('refund_amount','')).replace(',','.')),2)
        except (ValueError,TypeError): refund=-1
        try:
            with database.connect() as c:
                lock_inventory(c, cid)
                b=execute_sql(c, 'SELECT b.*,s.internal_state FROM bookings b LEFT JOIN booking_status_definitions s\n                    ON s.id=b.workflow_status_id AND s.company_id=b.company_id\n                    WHERE b.id=:q231_0 AND b.company_id=:q231_1',(booking_id,cid)).fetchone()
                if b is None or str(b['status'] or '').lower()=='cancelled' or str(b['internal_state'] or '')=='RELEASED':
                    raise ValueError('Booking is missing or already cancelled.')
                paid=round(float(execute_sql(c, 'SELECT COALESCE(SUM(amount),0) AS paid FROM booking_payments WHERE company_id=:q238_0 AND booking_id=:q238_1',(cid,booking_id)).fetchone()['paid']),2)
                if refund<0 or refund>paid: raise ValueError('Refund must be between zero and the total payments received.')
                if (decision=='full_refund' and refund!=paid) or (decision=='no_refund' and refund!=0) or (decision=='partial_refund' and (refund<=0 or refund>=paid)):
                    raise ValueError('Refund amount does not match the selected refund decision.')
                status=execute_sql(c, "SELECT id FROM booking_status_definitions WHERE company_id=:q232_0 AND internal_state='RELEASED' AND blocks_availability=0 AND active=1 ORDER BY id LIMIT 1",(cid,)).fetchone()
                if status is None: raise ValueError('No active non-blocking cancellation status configured.')
                now=iso_now()
                execute_sql(c, 'INSERT INTO booking_cancellations(company_id,booking_id,reason,refund_decision,refund_amount,retained_amount,financial_note,created_by_user_id,created_at)\n                    VALUES(:q221_0,:q221_1,:q221_2,:q221_3,:q221_4,:q221_5,:q221_6,:q221_7,:q221_8)',(cid,booking_id,reason,decision,refund,round(paid-refund,2),note,context['user_id'],now))
                execute_sql(c, 'UPDATE bookings SET status=:q222_0,workflow_status_id=:q222_1,updated_at=:q222_2 WHERE company_id=:q222_3 AND id=:q222_4',
                          ('cancelled',int(status['id']),now,cid,booking_id))
        except ValueError as exc:
            return HTMLResponse(layout('Cancellation not completed',f'<p>{esc(str(exc))}</p><a href="/operations/bookings/{booking_id}/cancel">Return</a>',context),400)
        audit(database,context,cid,'BOOKING_CANCELLED','booking',booking_id,after={'reason':reason,'refund_decision':decision,'refund_amount':refund,'retained_amount':round(paid-refund,2),'financial_note':note})
        return RedirectResponse(f'/operations/bookings/{booking_id}?message=Booking+cancelled+permanently',303)

    @app.post('/operations/bookings/{booking_id}/amend-preview', response_class=HTMLResponse)
    async def amend_preview(booking_id: int, request: Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        try:
            beid=int(data.get('booking_element_id','')); manual=float(str(data.get('manual_discount','0')).replace(',','.'))
            quote=amendment_quote(database,cid,booking_id,beid,str(data.get('arrival_date','')),str(data.get('departure_date','')),manual)
        except (TypeError,ValueError) as exc:
            return RedirectResponse(f'/operations/bookings/{booking_id}?message='+str(exc).replace(' ','+'),303)
        rule=quote.get('duration_rule') or {}
        discount_line=(f"<p><strong>Duration discount:</strong> {_money(quote['duration_discount'])} — {esc(rule.get('name',''))}</p>" if quote['duration_discount'] else '<p><strong>Duration discount:</strong> €0.00</p>')
        private_note=(f"<p><strong>Private staff discount:</strong> {_money(quote['manual_discount'])}</p>" if quote['manual_discount'] else '')
        payload=esc(json.dumps(quote,separators=(',',':')))
        body=f'''<h1>Preview Booking Amendment</h1><div class="card"><p><strong>Current stay:</strong> {_fmt_day(quote['old_arrival'])} → {_fmt_day(quote['old_departure'])}<br>
        <strong>New stay:</strong> {_fmt_day(quote['new_arrival'])} → {_fmt_day(quote['new_departure'])}<br>
        <strong>Historical portion retained:</strong> {quote['retained_nights']} night(s) — {_money(quote['retained_historic_base'])}<br>
        <strong>New nights at current pricing:</strong> {quote['added_nights']} — {_money(quote['added_current_element']+quote['added_recurring'])}</p>
        {discount_line}{private_note}<p><strong>Old booking total:</strong> {_money(quote['old_booking_total'])}<br><strong>New booking total:</strong> {_money(quote['new_booking_total'])}</p>
        <form method="post" action="/operations/bookings/{booking_id}/amend-apply"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><input type="hidden" name="quote" value="{payload}"><button>Apply Amendment</button> <a href="/operations/bookings/{booking_id}">Cancel</a></form></div>'''
        return layout('Preview Booking Amendment',body,context)

    @app.post('/operations/bookings/{booking_id}/amend-apply')
    async def amend_apply(booking_id: int, request: Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        try:
            submitted=json.loads(str(data.get('quote','{}')))
            if int(submitted.get('booking_id',0))!=booking_id: raise ValueError('Invalid amendment.')
            quote=amendment_quote(database,cid,booking_id,int(submitted['booking_element_id']),str(submitted['new_arrival']),str(submitted['new_departure']),float(submitted.get('manual_discount',0)))
            amendment_id=apply_amendment(database,context,quote)
        except (KeyError,TypeError,ValueError,json.JSONDecodeError) as exc:
            return RedirectResponse(f'/operations/bookings/{booking_id}?message='+str(exc).replace(' ','+'),303)
        sync_payment_status(database,cid,booking_id)
        private_detail=dict(quote); private_detail['amendment_id']=amendment_id; private_detail['manual_discount_private']=private_detail.pop('manual_discount',0)
        audit(database,context,cid,'BOOKING_AMENDED','booking',booking_id,
              before={'arrival_date':quote['old_arrival'],'departure_date':quote['old_departure'],'booking_total':quote['old_booking_total']},
              after=private_detail)
        return RedirectResponse(f'/operations/bookings/{booking_id}?message=Booking+amended',303)


    @app.post('/operations/bookings/{booking_id}/refunds')
    async def record_refund(booking_id: int, request: Request):
        context=context_for(database,request); cid=int(working_company(context))
        data=await form_data(request); require_csrf(context,data)
        try:
            amount=round(float(str(data.get('amount','')).replace(',','.')),2)
            date=str(data.get('refund_date','')).strip()
            datetime.strptime(date,'%Y-%m-%d')
            method=str(data.get('method','')).strip()
            reference=str(data.get('reference','')).strip()
            if amount<=0 or not method or not reference or data.get('acknowledge')!='yes':
                raise ValueError('Refund amount, method, reference and confirmation are required.')
            with database.connect() as c:
                lock_inventory(c, cid)
                cancellation=execute_sql(c, 'SELECT refund_amount FROM booking_cancellations WHERE company_id=:q233_0 AND booking_id=:q233_1',(cid,booking_id)).fetchone()
                if cancellation is None: raise ValueError('Only cancelled bookings with an approved refund decision can record refunds.')
                previous=round(float(execute_sql(c, 'SELECT COALESCE(SUM(amount),0) AS n FROM booking_refunds WHERE company_id=:q239_0 AND booking_id=:q239_1',(cid,booking_id)).fetchone()['n']),2)
                if amount>round(float(cancellation['refund_amount'])-previous,2):
                    raise ValueError('Refund exceeds the amount authorised at cancellation.')
                refund_id=int(insert_sql(c, 'INSERT INTO booking_refunds(company_id,booking_id,amount,refund_date,method,reference,notes,created_by_user_id,created_at)\n                    VALUES(:q234_0,:q234_1,:q234_2,:q234_3,:q234_4,:q234_5,:q234_6,:q234_7,:q234_8)',(cid,booking_id,amount,date,method,reference,str(data.get('notes','')).strip(),context['user_id'],iso_now())).lastrowid)
        except (ValueError,TypeError) as exc:
            return HTMLResponse(layout('Refund not recorded',f'<p>{esc(str(exc))}</p><a href="/operations/bookings/{booking_id}">Return to booking</a>',context),400)
        audit(database,context,cid,'BOOKING_REFUND_RECORDED','booking',booking_id,after={'refund_id':refund_id,'amount':amount,'method':method,'reference':reference,'refund_date':date})
        return RedirectResponse(f'/operations/bookings/{booking_id}?message=Refund+recorded',303)

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
        method=one(database,'SELECT * FROM payment_method_definitions WHERE company_id=:q207_0 AND id=:q207_1 AND active=1',(cid,method_id))
        if method is None: return RedirectResponse(f'/operations/bookings/{booking_id}?message=Choose+an+active+Payment+Method',303)
        if str(method['method_type'])=='card': return RedirectResponse(f'/operations/bookings/{booking_id}?message=Card+provider+connection+is+not+configured+yet',303)
        with database.reservation_write_transaction(cid) as c:
            payment_id = int(insert_sql(c, 'INSERT INTO booking_payments(company_id,booking_id,amount,payment_date,method,reference,notes,created_by_user_id,created_at)\n                VALUES (:q228_0,:q228_1,:q228_2,:q228_3,:q228_4,:q228_5,:q228_6,:q228_7,:q228_8)', (cid, booking_id, amount, payment_date, str(method['name']), str(data.get('reference','')).strip(), str(data.get('notes','')).strip(), context['user_id'], iso_now())).lastrowid)
            total=float(execute_sql(c, 'SELECT total_amount FROM bookings WHERE id=:q236_0 AND company_id=:q236_1',(booking_id,cid)).fetchone()['total_amount'])
            paid=float(execute_sql(c, 'SELECT COALESCE(SUM(amount),0) AS n FROM booking_payments WHERE booking_id=:q237_0 AND company_id=:q237_1',(booking_id,cid)).fetchone()['n'])
            sync_payment_status(database,cid,booking_id,connection=c,context=context)
        audit(database, context, cid, 'BOOKING_PAYMENT_RECORDED', 'booking', booking_id, after={'payment_id': payment_id, 'amount': amount, 'payment_date': payment_date, 'method': str(method['name']), 'reference': str(data.get('reference','')).strip()})
        return RedirectResponse(f'/operations/bookings/{booking_id}?message=Payment+recorded', 303)


def _unresolved_recovery_count(database,cid:int,enquiry_id:int)->int:
    try:
        row=one(database,"SELECT COUNT(*) AS n FROM enquiry_elements WHERE company_id=:q208_0 AND enquiry_id=:q208_1 AND COALESCE(recovery_state,'held')='needs_replacement'",(cid,enquiry_id))
        return int(row['n'] or 0) if row else 0
    except Exception:
        return 0


def enquiry_conversion_panel(database, context, enquiry_id: int) -> str:
    cid = int(working_company(context))
    existing = one(database, 'SELECT id,reference FROM bookings WHERE company_id=:q193_0 AND enquiry_id=:q193_1 ORDER BY id DESC LIMIT 1', (cid, enquiry_id))
    if existing:
        return f'<div class="card"><h2>Booking</h2><p>This Enquiry has been converted to <a href="/operations/bookings/{int(existing["id"])}"><strong>{esc(existing["reference"])}</strong></a>.</p></div>'
    unresolved=_unresolved_recovery_count(database,cid,enquiry_id)
    if unresolved:
        return f'<div class="card" style="border:2px solid #b42318"><h2>Confirm Booking</h2><p><strong>{unresolved} Element(s) require replacement.</strong> Resolve the red unavailable Element(s) above before this Enquiry can be confirmed.</p><button disabled>CONFIRM BOOKING</button></div>'
    booking_html = f'''<div class="card"><h2>Confirm Booking</h2><p>Continue to Take Payment. The Booking is not created until payment is recorded or deliberately confirmed without payment.</p><form method="post" action="/operations/enquiries/{enquiry_id}/convert"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><p><button>CONFIRM BOOKING</button></p></form></div>'''
    return booking_html
