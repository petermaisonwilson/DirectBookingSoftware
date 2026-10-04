from __future__ import annotations

import json

from .database import iso_now


def booking_financials(database, company_id: int, booking_id: int, *, connection=None) -> dict:
    if connection is None:
        with database.connect() as c:
            return booking_financials(database, company_id, booking_id, connection=c)
    c = connection
    booking = c.execute('SELECT total_amount,workflow_status_id FROM bookings WHERE company_id=? AND id=?', (company_id, booking_id)).fetchone()
    if booking is None:
        raise ValueError('Booking not found.')
    paid = float(c.execute('SELECT COALESCE(SUM(amount),0) AS n FROM booking_payments WHERE company_id=? AND booking_id=?', (company_id, booking_id)).fetchone()['n'])
    total = round(float(booking['total_amount'] or 0), 2)
    return {'total': total, 'paid': round(paid, 2), 'outstanding': round(max(0.0, total-paid), 2)}


def sync_payment_status(database, company_id: int, booking_id: int, *, connection=None, context=None) -> dict:
    if connection is None:
        with database.connect() as c:
            result = sync_payment_status(database, company_id, booking_id, connection=c, context=context)
        return result
    c = connection
    booking = c.execute('''SELECT b.workflow_status_id,s.internal_state
            FROM bookings b LEFT JOIN booking_status_definitions s ON s.id=b.workflow_status_id AND s.company_id=b.company_id
            WHERE b.company_id=? AND b.id=?''', (company_id, booking_id)).fetchone()
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
    status = c.execute('SELECT id FROM booking_status_definitions WHERE company_id=? AND active=1 AND system_code=? ORDER BY id LIMIT 1', (company_id, wanted)).fetchone()
    if status is not None and int(status['id']) != int(booking['workflow_status_id'] or 0):
        old_status_id = int(booking['workflow_status_id'] or 0)
        new_status_id = int(status['id'])
        now = iso_now()
        c.execute('UPDATE bookings SET workflow_status_id=?,updated_at=? WHERE company_id=? AND id=?', (new_status_id, now, company_id, booking_id))
        c.execute('''INSERT INTO audit_log(company_id,actor_user_id,actor_role,acting_company_id,action,entity_type,entity_id,before_json,after_json,created_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?)''',
                  (company_id,
                   context.get('user_id') if context else None,
                   context.get('role') if context else 'System',
                   context.get('acting_company_id') if context else None,
                   'BOOKING_STATUS_AUTOMATIC','booking',str(booking_id),
                   json.dumps({'workflow_status_id': old_status_id}),
                   json.dumps({'workflow_status_id': new_status_id, 'system_code': wanted,
                               'paid': financials['paid'], 'outstanding': financials['outstanding']}),
                   now))
    return financials
