"""Reservation identity repository used by the operational workflow.

Callers own the tenant inventory transaction. Source IDs retain compatibility
with existing URLs; they are never used as a substitute for reservation identity.
No function starts another connection or commits a caller's transaction.
"""
import json

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
    return int(insert_sql(connection,
        'INSERT INTO reservations(company_id,reference,customer_id,state,currency,total_amount,notes,created_at,updated_at,enquiry_id,booking_id) '
        'VALUES (:tenant,:reference,:customer,:state,:currency,:total,:notes,:created,:updated,:enquiry,:booking)',
        (company_id, reference, source['customer_id'], 'ENQUIRY_HELD',
         str(company['currency'] or 'EUR'), 0, source['notes'] or '', source['created_at'],
         source['updated_at'], enquiry_id, booking_id)).lastrowid)


def _allocation(connection, company_id, reservation_id, item, *, enquiry_element_id=None, booking_element_id=None):
    existing = None
    for column, value in (('booking_element_id', booking_element_id), ('enquiry_element_id', enquiry_element_id)):
        if value is not None:
            existing = execute_sql(connection,
                f'SELECT id,reservation_id FROM reservation_elements WHERE company_id=:tenant AND {column}=:source',
                (company_id, value)).fetchone()
            if existing is not None:
                break
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
              'total': float(item.get('provisional_total', item.get('total_amount')) or 0),
              'snapshot': item.get('pricing_snapshot_json') or '{}', 'lead': item.get('lead_name') or '',
              'recovery': item.get('recovery_state') or 'held',
              'enquiry': enquiry_element_id, 'booking': booking_element_id}
    if existing is None:
        return int(insert_sql(connection,
            'INSERT INTO reservation_elements(company_id,reservation_id,element_id,arrival_date,departure_date,total_amount,'
            'pricing_snapshot_json,lead_name,recovery_state,enquiry_element_id,booking_element_id) '
            'VALUES (:tenant,:reservation,:element,:arrival,:departure,:total,:snapshot,:lead,:recovery,:enquiry,:booking)', values).lastrowid)
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
    elements = execute_sql(connection,
        'SELECT * FROM enquiry_elements WHERE company_id=:tenant AND enquiry_id=:enquiry ORDER BY sort_order,id',
        (company_id, enquiry_id)).fetchall()
    for row in elements:
        _allocation(connection, company_id, rid, dict(row), enquiry_element_id=row['id'])
    state = 'ENQUIRY_RELEASED' if source['status'] == 'closed' or source['blocks_availability'] == 0 else 'ENQUIRY_HELD'
    execute_sql(connection,
        'UPDATE reservations SET state=:state,customer_id=:customer,notes=:notes,total_amount=:total,updated_at=:updated '
        'WHERE company_id=:tenant AND id=:reservation',
        (state, source['customer_id'], source['notes'] or '', sum(float(e['provisional_total'] or 0) for e in elements),
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
        (state, source['customer_id'], source['currency'], source['total_amount'], source['notes'] or '',
         source['updated_at'], company_id, rid))
    return rid


def reservation_financials(connection, company_id, reservation_id):
    master = execute_sql(connection, 'SELECT total_amount FROM reservations WHERE company_id=:tenant AND id=:reservation',
                         (company_id, reservation_id)).fetchone()
    if master is None:
        raise ValueError('Reservation not found')
    paid = float(execute_sql(connection,
        'SELECT COALESCE(SUM(amount),0) FROM booking_payments WHERE company_id=:tenant AND reservation_id=:reservation',
        (company_id, reservation_id)).fetchone()[0])
    total = round(float(master['total_amount'] or 0), 2)
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
