"""Reservation identity repository used by the operational workflow.

Callers own the tenant inventory transaction. Source IDs retain compatibility
with existing URLs; they are never used as a substitute for reservation identity.
No function starts another connection or commits a caller's transaction.
"""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from .setup015_calculator import _addon_rule

from .reservation_money import finite_amount
from .database import iso_now
from .sql import execute_sql, insert_sql


def identity(connection, company_id, *, enquiry_id=None, booking_id=None):
    if (enquiry_id is None) == (booking_id is None):
        raise ValueError('Supply one operational identity')
    column, value = ('enquiry_id', enquiry_id) if enquiry_id is not None else ('booking_id', booking_id)
    row = execute_sql(connection, f'SELECT * FROM reservations WHERE company_id=:tenant AND {column}=:source',
                      (int(company_id), int(value))).fetchone()
    return None if row is None else dict(row)


def _create(connection, company_id, source, *, enquiry_id=None, booking_id=None):
    company = execute_sql(connection, 'SELECT currency FROM companies WHERE id=:tenant', (company_id,)).fetchone()
    reference = f'Enquiry-{enquiry_id}' if enquiry_id is not None else f'Booking-{booking_id}'
    from .webv1_reservation_core import _insert_header
    return _insert_header(connection, company_id, reference, source['customer_id'],
        state='ENQUIRY_HELD', currency=str(company['currency'] or 'EUR'), total_amount=0,
        notes=source['notes'] or '', created_at=source['created_at'], updated_at=source['updated_at'],
        enquiry_id=enquiry_id, booking_id=booking_id)



def _allocation(connection, company_id, reservation_id, item, *, enquiry_element_id=None, booking_element_id=None):
    existing = None
    for column, value in (('booking_element_id', booking_element_id), ('enquiry_element_id', enquiry_element_id)):
        if value is not None:
            existing = execute_sql(connection,
                f'SELECT id,reservation_id FROM reservation_elements WHERE company_id=:tenant AND {column}=:source',
                (company_id, value)).fetchone()
            if existing is not None:
                break
    if enquiry_element_id is None and booking_element_id is None:
        old = execute_sql(connection,
            'SELECT id,reservation_id FROM reservation_elements WHERE company_id=:tenant AND reservation_id=:reservation '
            'AND enquiry_element_id IS NULL AND booking_element_id IS NULL', (company_id, reservation_id)).fetchall()
        if len(old) > 1:
            raise ValueError('Ambiguous single-element enquiry allocation')
        existing = old[0] if old else None
    if existing is not None and int(existing['reservation_id']) != int(reservation_id):
        raise ValueError('Element belongs to another reservation')
    if booking_element_id is not None:
        snapshot = json.loads(item.get('pricing_snapshot_json') or '{}')
        latest = execute_sql(connection,
            'SELECT calculation_json FROM booking_amendments WHERE company_id=:tenant AND booking_element_id=:element ORDER BY id DESC LIMIT 1',
            (company_id, booking_element_id)).fetchone()
        if latest is not None:
            calculation = json.loads(latest['calculation_json'])
            snapshot = dict(snapshot, total=calculation['new_element_total'], amendment=calculation)
        item = dict(item, total_amount=snapshot.get('total', item.get('total_amount', 0)),
                    pricing_snapshot_json=json.dumps(snapshot, separators=(',', ':')))
    values = {'tenant': company_id, 'reservation': reservation_id, 'element': item['element_id'],
              'arrival': str(item['arrival_date']), 'departure': str(item['departure_date']),
              'total': finite_amount(item.get('provisional_total', item.get('total_amount')) or 0),
              'snapshot': item.get('pricing_snapshot_json') or '{}', 'lead': item.get('lead_name') or '',
              'recovery': item.get('recovery_state') or 'held',
              'enquiry': enquiry_element_id, 'booking': booking_element_id}
    if existing is None:
        from .webv1_reservation_core import _insert_allocation
        return _insert_allocation(connection, company_id, reservation_id, values['element'], values['arrival'], values['departure'],
            total_amount=values['total'], pricing_snapshot_json=values['snapshot'], lead_name=values['lead'],
            recovery_state=values['recovery'], enquiry_element_id=enquiry_element_id, booking_element_id=booking_element_id)
    values['id'] = existing['id']
    execute_sql(connection,
        'UPDATE reservation_elements SET element_id=:element,arrival_date=:arrival,departure_date=:departure,'
        'total_amount=:total,pricing_snapshot_json=:snapshot,lead_name=:lead,recovery_state=:recovery,'
        'enquiry_element_id=COALESCE(:enquiry,enquiry_element_id),booking_element_id=COALESCE(:booking,booking_element_id) '
        'WHERE company_id=:tenant AND reservation_id=:reservation AND id=:id', values)
    return int(existing['id'])


def save_enquiry(connection, company_id, enquiry_id):
    source = execute_sql(connection,
        'SELECT e.*,s.blocks_availability FROM enquiries e LEFT JOIN booking_status_definitions s '
        'ON s.company_id=e.company_id AND s.id=e.workflow_status_id WHERE e.company_id=:tenant AND e.id=:enquiry',
        (company_id, enquiry_id)).fetchone()
    if source is None:
        raise ValueError('Enquiry not found')
    source = dict(source)
    master = identity(connection, company_id, enquiry_id=enquiry_id)
    if master is not None and master['state'] in ('CONFIRMED', 'CANCELLED'):
        raise ValueError('Confirmed reservation requirements must be changed through its booking')
    rid = int(master['id']) if master else _create(connection, company_id, source, enquiry_id=enquiry_id)
    elements = [dict(row) for row in execute_sql(connection,
        'SELECT * FROM enquiry_elements WHERE company_id=:tenant AND enquiry_id=:enquiry ORDER BY sort_order,id',
        (company_id, enquiry_id)).fetchall()]
    if not elements and source['arrival_date'] and source['departure_date']:
        legacy = execute_sql(connection,
            'SELECT element_id,provisional_total,pricing_snapshot_json FROM enquiry_requests '
            'WHERE company_id=:tenant AND enquiry_id=:enquiry AND element_id IS NOT NULL', (company_id, enquiry_id)).fetchone()
        if legacy is not None:
            elements = [dict(legacy, arrival_date=source['arrival_date'], departure_date=source['departure_date'])]
    for row in elements:
        _allocation(connection, company_id, rid, row, enquiry_element_id=row.get('id'))
    state = 'ENQUIRY_RELEASED' if source['status'] == 'closed' or source['blocks_availability'] == 0 else 'ENQUIRY_HELD'
    execute_sql(connection,
        'UPDATE reservations SET state=:state,customer_id=:customer,notes=:notes,total_amount=:total,updated_at=:updated '
        'WHERE company_id=:tenant AND id=:reservation',
        (state, source['customer_id'], source['notes'] or '', finite_amount(sum(finite_amount(e['provisional_total'] or 0) for e in elements)),
         source['updated_at'], company_id, rid))
    return rid


def confirm_enquiry(connection, company_id, enquiry_id, booking_id, element_pairs):
    """Attach the booking to the SAME identity, preserving each allocation ID."""
    master = identity(connection, company_id, enquiry_id=enquiry_id)
    if master is None:
        raise ValueError('Enquiry has no reservation identity')
    if master['state'] not in ('ENQUIRY_HELD', 'ENQUIRY_RELEASED') or master['booking_id'] is not None:
        raise ValueError('Reservation has already been confirmed or cancelled')
    rid = int(master['id'])
    execute_sql(connection,
        'UPDATE reservations SET booking_id=:booking WHERE company_id=:tenant AND id=:reservation',
        (booking_id, company_id, rid))
    for enquiry_element_id, booking_element_id in element_pairs:
        if enquiry_element_id is not None:
            result = execute_sql(connection,
                'UPDATE reservation_elements SET booking_element_id=:booking_element '
                'WHERE company_id=:tenant AND reservation_id=:reservation AND enquiry_element_id=:enquiry_element',
                (booking_element_id, company_id, rid, enquiry_element_id))
            if result.rowcount != 1:
                raise ValueError('Original enquiry allocation is missing')
        elif len(element_pairs) == 1:
            execute_sql(connection,
                'UPDATE reservation_elements SET booking_element_id=:booking_element '
                'WHERE company_id=:tenant AND reservation_id=:reservation AND enquiry_element_id IS NULL AND booking_element_id IS NULL',
                (booking_element_id, company_id, rid))
    return save_booking(connection, company_id, booking_id)


def save_booking(connection, company_id, booking_id):
    source = execute_sql(connection, 'SELECT * FROM bookings WHERE company_id=:tenant AND id=:booking',
                         (company_id, booking_id)).fetchone()
    if source is None:
        raise ValueError('Booking not found')
    source = dict(source)
    master = identity(connection, company_id, booking_id=booking_id)
    if master is None:
        raise ValueError('Booking has no reservation identity; migrate it before changing it')
    rid = int(master['id'])
    state = 'CANCELLED' if source['status'] == 'cancelled' else 'CONFIRMED'
    if master['state'] == 'CANCELLED' and state != 'CANCELLED':
        raise ValueError('Cancelled reservations cannot be reinstated')
    for row in execute_sql(connection,
            'SELECT * FROM booking_elements WHERE company_id=:tenant AND booking_id=:booking ORDER BY id',
            (company_id, booking_id)).fetchall():
        _allocation(connection, company_id, rid, dict(row), booking_element_id=row['id'])
    execute_sql(connection,
        'UPDATE reservations SET state=:state,customer_id=:customer,currency=:currency,total_amount=:total,notes=:notes,'
        'updated_at=:updated WHERE company_id=:tenant AND id=:reservation',
        (state, source['customer_id'], source['currency'], finite_amount(source['total_amount']), source['notes'] or '',
         source['updated_at'], company_id, rid))
    return rid


def reservation_financials(connection, company_id, reservation_id):
    master = execute_sql(connection, 'SELECT total_amount FROM reservations WHERE company_id=:tenant AND id=:reservation',
                         (company_id, reservation_id)).fetchone()
    if master is None:
        raise ValueError('Reservation not found')
    paid = finite_amount(execute_sql(connection,
        'SELECT COALESCE(SUM(amount),0) FROM booking_payments WHERE company_id=:tenant AND reservation_id=:reservation',
        (company_id, reservation_id)).fetchone()[0])
    total = round(finite_amount(master['total_amount'] or 0), 2)
    return {'total': total, 'paid': round(paid, 2), 'outstanding': round(max(0, total-paid), 2)}


_FINANCIAL_FIELDS = {
    'booking_payments': ('amount', 'payment_date', 'method', 'reference', 'notes', 'created_by_user_id', 'created_at'),
    'booking_refunds': ('amount', 'refund_date', 'method', 'reference', 'notes', 'created_by_user_id', 'created_at'),
    'booking_cancellations': ('reason', 'refund_decision', 'refund_amount', 'retained_amount', 'financial_note', 'created_by_user_id', 'created_at'),
    'booking_amendments': ('booking_element_id', 'old_arrival_date', 'old_departure_date', 'new_arrival_date', 'new_departure_date',
                           'old_booking_total', 'new_booking_total', 'manual_discount', 'calculation_json', 'created_by_user_id', 'created_at'),
}


def insert_financial(connection, table, company_id, booking_id, *values):
    """One write boundary for finance records; every row owns its reservation ID."""
    fields = _FINANCIAL_FIELDS.get(table)
    if fields is None or len(fields) != len(values):
        raise ValueError('Invalid reservation financial record')
    master = identity(connection, company_id, booking_id=booking_id)
    if master is None:
        raise ValueError('Booking has no reservation identity')
    data = dict(zip(fields, values))
    for field in ('amount', 'refund_amount', 'retained_amount', 'old_booking_total', 'new_booking_total', 'manual_discount'):
        if field in data:
            data[field] = finite_amount(data[field], field.replace('_', ' '))
    data.update(company_id=company_id, booking_id=booking_id, reservation_id=master['id'])
    columns = ('company_id', 'booking_id', 'reservation_id', *fields)
    return int(insert_sql(connection, f'INSERT INTO {table}(' + ','.join(columns) + ') VALUES (' +
                          ','.join(':' + column for column in columns) + ')', data).lastrowid)


def append_operational_history(connection, audit):
    """Append the event under its permanent identity, without changing domain state."""
    if audit['entity_type'] not in ('enquiry', 'booking') or audit['company_id'] is None or audit['entity_id'] is None:
        return
    kwargs = {'enquiry_id' if audit['entity_type'] == 'enquiry' else 'booking_id': int(audit['entity_id'])}
    master = identity(connection, audit['company_id'], **kwargs)
    if master is None:
        return
    details = {'source_type': audit['entity_type'], 'source_id': audit['entity_id'],
               'actor_user_id': audit['actor_user_id'], 'actor_role': audit['actor_role'],
               'before': json.loads(audit['before_json']) if audit['before_json'] else None,
               'after': json.loads(audit['after_json']) if audit['after_json'] else None}
    actor = execute_sql(connection, 'SELECT first_name,last_name FROM users WHERE id=:staff',
                        (audit['actor_user_id'],)).fetchone() if audit['actor_user_id'] is not None else None
    name = ' '.join(str(actor[key] or '') for key in ('first_name', 'last_name')).strip() if actor else 'System'
    execute_sql(connection,
        'INSERT INTO reservation_history(company_id,reservation_id,event_type,actor,reason,details_json,created_at,audit_log_id) '
        'VALUES (:tenant,:reservation,:event,:actor,:reason,:details,:created,:audit)',
        (audit['company_id'], master['id'], audit['action'], name, '', json.dumps(details, separators=(',', ':')), audit['created_at'], audit['audit_log_id']))


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


def _snapshot_line_amount(snapshot: dict, name: str) -> float:
    for line in snapshot.get('lines') or []:
        if str(line.get('item', '')) == name:
            try:
                return float(line.get('amount', 0) or 0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def write_confirmation_details(c,database,context,cid,enquiry,elements,workflow_status_id):
    save_enquiry(c, cid, int(enquiry['id']))
    element_pairs = []
    now=iso_now();total=finite_amount(sum(finite_amount(e['provisional_total'] or 0) for e in elements));reference=_next_reference(c,cid)
    company = execute_sql(c, 'SELECT currency FROM companies WHERE id=:tenant', (cid,)).fetchone()
    currency = str(company['currency'] or 'EUR').upper() if company else 'EUR'
    arrival=min(str(e['arrival_date']) for e in elements);departure=max(str(e['departure_date']) for e in elements)
    booking_id=int(insert_sql(c, 'INSERT INTO bookings (company_id,reference,customer_id,enquiry_id,status,arrival_date,departure_date,currency,total_amount,pricing_snapshot_json,notes,created_at,updated_at,workflow_status_id) VALUES (:q210_0,:q210_1,:q210_2,:q210_3,:q210_4,:q210_5,:q210_6,:q210_7,:q210_8,:q210_9,:q210_10,:q210_11,:q210_12,:q210_13)',(cid,reference,enquiry['customer_id'],enquiry['id'],'confirmed',arrival,departure,currency,total,json.dumps({'multi_element':len(elements)>1,'element_count':len(elements),'total':total},separators=(',',':')),enquiry['notes'] or '',now,now,workflow_status_id)).lastrowid)
    for ee in elements:
        try:snapshot=json.loads(ee['pricing_snapshot_json'] or '{}')
        except (TypeError,json.JSONDecodeError):snapshot={}
        element_amount=_snapshot_line_amount(snapshot,str(ee['element_name'] or ''))
        beid=int(insert_sql(c, 'INSERT INTO booking_elements(company_id,booking_id,element_id,arrival_date,departure_date,pricing_method_snapshot,unit_price_snapshot,total_amount,pricing_snapshot_json,lead_name) VALUES (:q214_0,:q214_1,:q214_2,:q214_3,:q214_4,:q214_5,:q214_6,:q214_7,:q214_8,:q214_9)',(cid,booking_id,ee['element_id'],ee['arrival_date'],ee['departure_date'],ee['pricing_method'] or '',0,element_amount,ee['pricing_snapshot_json'],ee['lead_name'] or '')).lastrowid)
        element_pairs.append((ee['id'] if 'id' in ee.keys() else None, beid))
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
    confirm_enquiry(c, cid, int(enquiry['id']), booking_id, element_pairs)
    return booking_id,reference,total


def require_staff_company(context, company_id):
    from .setup015_core import working_company
    active = working_company(context)
    if active is None or int(active) != int(company_id):
        raise ValueError('Select the authorised company before changing this reservation.')
