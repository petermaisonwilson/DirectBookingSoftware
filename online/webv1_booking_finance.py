from __future__ import annotations
from .sql import execute_sql

import json

from .database import iso_now


def booking_financials(database, company_id: int, booking_id: int, *, connection=None) -> dict:
    if connection is None:
        with database.connect() as c:
            return booking_financials(database, company_id, booking_id, connection=c)
    c = connection
    from .webv1_operational_reservations import identity, reservation_financials
    master = identity(c, company_id, booking_id=booking_id)
    if master is None:
        raise ValueError('Booking has no reservation identity; complete its additive migration first.')
    return reservation_financials(c, company_id, int(master['id']))



def sync_payment_status(database, company_id: int, booking_id: int, *, connection=None, context=None) -> dict:
    if connection is None:
        with database.connect() as c:
            result = sync_payment_status(database, company_id, booking_id, connection=c, context=context)
        return result
    c = connection
    booking = execute_sql(c, 'SELECT b.workflow_status_id,s.internal_state\n            FROM bookings b LEFT JOIN booking_status_definitions s ON s.id=b.workflow_status_id AND s.company_id=b.company_id\n            WHERE b.company_id=:q590_0 AND b.id=:q590_1', (company_id, booking_id)).fetchone()
    if booking is None:
        raise ValueError('Booking not found.')
    financials = booking_financials(database, company_id, booking_id, connection=c)
    # Released/cancelled and on-site lifecycle states are not replaced by a payment label.
    # Reserved/confirmed bookings are always driven by the actual account balance.
    if str(booking['internal_state'] or '') in {'RELEASED','ON_SITE'}:
        return financials
    if financials['outstanding'] <= 0:
        wanted = 'BALANCE_PAID'
    elif financials['paid'] > 0:
        wanted = 'DEPOSIT_PAID'
    else:
        wanted = 'PAYMENT_PENDING'
    status = execute_sql(c, 'SELECT id FROM booking_status_definitions WHERE company_id=:q591_0 AND active=1 AND system_code=:q591_1 ORDER BY id LIMIT 1', (company_id, wanted)).fetchone()
    if status is not None and int(status['id']) != int(booking['workflow_status_id'] or 0):
        old_status_id = int(booking['workflow_status_id'] or 0)
        new_status_id = int(status['id'])
        now = iso_now()
        execute_sql(c, 'UPDATE bookings SET workflow_status_id=:q587_0,updated_at=:q587_1 WHERE company_id=:q587_2 AND id=:q587_3', (new_status_id, now, company_id, booking_id))
        database.write_audit(action='BOOKING_STATUS_AUTOMATIC', entity_type='booking', entity_id=booking_id,
            actor_user_id=context['user_id'] if context else None,
            actor_role=context['role'] if context else 'System', company_id=company_id,
            acting_company_id=context['acting_company_id'] if context else None,
            before={'workflow_status_id': old_status_id},
            after={'workflow_status_id': new_status_id, 'system_code': wanted,
                   'paid': financials['paid'], 'outstanding': financials['outstanding']}, connection=c)
    return financials
