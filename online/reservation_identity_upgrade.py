"""Idempotent, additive adoption of existing operational records.

This imports identities and history, never re-prices, renumbers or deletes source
records. Ambiguous legacy relationships abort the caller's transaction for review.
"""
from .sql import execute_sql
from .webv1_operational_reservations import (
    identity, save_enquiry, save_booking, _create, append_operational_history,
)


def adopt_company(connection, company_id):
    for table in ('booking_payments', 'booking_refunds', 'booking_cancellations', 'booking_amendments'):
        orphan = execute_sql(connection,
            f'SELECT f.id FROM {table} f LEFT JOIN bookings b ON b.company_id=f.company_id AND b.id=f.booking_id '
            'WHERE f.company_id=:tenant AND b.id IS NULL LIMIT 1', (company_id,)).fetchone()
        if orphan is not None:
            raise ValueError(f'{table} record {orphan["id"]}: missing or cross-tenant booking owner; source records were preserved')
    imported = {'enquiries': 0, 'bookings': 0, 'history': 0}
    adopted = set()
    enquiries = execute_sql(connection, 'SELECT * FROM enquiries WHERE company_id=:tenant ORDER BY id', (company_id,)).fetchall()
    for enquiry in enquiries:
        master = identity(connection, company_id, enquiry_id=enquiry['id'])
        if master is None:
            adopted.add(save_enquiry(connection, company_id, enquiry['id']))
            imported['enquiries'] += 1
    bookings = execute_sql(connection, 'SELECT * FROM bookings WHERE company_id=:tenant ORDER BY id', (company_id,)).fetchall()
    for source in bookings:
        source = dict(source)
        master = identity(connection, company_id, booking_id=source['id'])
        if master is None:
            enquiry_id = source['enquiry_id']
            if enquiry_id is not None:
                master = identity(connection, company_id, enquiry_id=enquiry_id)
                if master is None or master['booking_id'] is not None:
                    raise ValueError(f'Booking {source["id"]}: missing, cross-tenant or ambiguous enquiry relationship')
                rid = int(master['id'])
            else:
                rid = _create(connection, company_id, source, booking_id=source['id'])
                adopted.add(rid)
            execute_sql(connection, 'UPDATE reservations SET booking_id=:booking WHERE company_id=:tenant AND id=:reservation',
                        (source['id'], company_id, rid))
            # Match only the recorded original element/date identity, never a
            # price, customer name or guessed chronological relationship.
            original = execute_sql(connection,
                'SELECT * FROM reservation_elements WHERE company_id=:tenant AND reservation_id=:reservation',
                (company_id, rid)).fetchall()
            used = set()
            for item in execute_sql(connection, 'SELECT * FROM booking_elements WHERE company_id=:tenant AND booking_id=:booking ORDER BY id',
                                    (company_id, source['id'])).fetchall():
                start = item['original_arrival_date'] or item['arrival_date']
                end = item['original_departure_date'] or item['departure_date']
                matches = [allocation for allocation in original if int(allocation['element_id']) == int(item['element_id'])
                           and str(allocation['arrival_date']) == str(start) and str(allocation['departure_date']) == str(end)]
                if original and (len(matches) != 1 or int(matches[0]['id']) in used):
                    raise ValueError(f'Booking {source["id"]}: original element mapping is ambiguous; source records were preserved')
                if matches:
                    allocation = int(matches[0]['id'])
                    used.add(allocation)
                    execute_sql(connection,
                        'UPDATE reservation_elements SET booking_element_id=:element WHERE company_id=:tenant AND id=:allocation',
                        (item['id'], company_id, allocation))
            if original and len(used) != len(original):
                raise ValueError(f'Booking {source["id"]}: incomplete original element mapping; source records were preserved')
            save_booking(connection, company_id, source['id'])
            imported['bookings'] += 1
        else:
            rid = int(master['id'])
        for table in ('booking_payments', 'booking_refunds', 'booking_cancellations', 'booking_amendments'):
            mismatched = execute_sql(connection,
                f'SELECT id FROM {table} WHERE company_id=:tenant AND booking_id=:booking '
                'AND reservation_id IS NOT NULL AND reservation_id<>:reservation LIMIT 1',
                (company_id, source['id'], rid)).fetchone()
            if mismatched is not None:
                raise ValueError('Financial record already belongs to another reservation')
            execute_sql(connection,
                f'UPDATE {table} SET reservation_id=:reservation WHERE company_id=:tenant AND booking_id=:booking AND reservation_id IS NULL',
                (rid, company_id, source['id']))
    events = execute_sql(connection,
        "SELECT a.* FROM audit_log a WHERE a.company_id=:tenant AND a.entity_id IS NOT NULL AND a.entity_type IN ('enquiry','booking') "
        'AND NOT EXISTS (SELECT 1 FROM reservation_history h WHERE h.company_id=a.company_id AND h.audit_log_id=a.id) ORDER BY a.id',
        (company_id,)).fetchall()
    for event in events:
        data = dict(event, audit_log_id=event['id'])
        if identity(connection, company_id, **{'enquiry_id' if data['entity_type'] == 'enquiry' else 'booking_id': data['entity_id']}) is not None:
            append_operational_history(connection, data)
            imported['history'] += 1
    from .database import iso_now
    from .webv1_reservation_service import _history
    for rid in sorted(adopted):
        master = execute_sql(connection, 'SELECT * FROM reservations WHERE company_id=:tenant AND id=:reservation', (company_id, rid)).fetchone()
        _history(connection, company_id, rid, 'IDENTITY_ADOPTED', iso_now(), 'System', new=master['state'],
                 details={'enquiry_id': master['enquiry_id'], 'booking_id': master['booking_id'],
                          'source_created_at': master['created_at'], 'total_amount': master['total_amount'],
                          'source_records_preserved': True})
    return imported
