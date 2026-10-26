"""Unified reservation lifecycle contract for Build 302 v3."""

STATES = ('ENQUIRY_HELD', 'ENQUIRY_RELEASED', 'CONFIRMED', 'CANCELLED')
TRANSITIONS = {
    'ENQUIRY_HELD': ('ENQUIRY_RELEASED', 'CONFIRMED', 'CANCELLED'),
    'ENQUIRY_RELEASED': ('ENQUIRY_HELD', 'CONFIRMED', 'CANCELLED'),
    'CONFIRMED': ('CANCELLED',),
    'CANCELLED': (),
}


def allowed_transition(source, target):
    return target in TRANSITIONS.get(source, ())


class ReservationError(ValueError):
    """Reservation action violates the lifecycle or inventory rules."""


def inventory_conflict(connection, company_id, element_id, arrival, departure, exclude_reservation_id=None):
    """Check canonical allocations using explicit engine-compatible parameters."""
    sql = ("SELECT r.id FROM reservation_elements e "
           "JOIN reservations r ON r.id=e.reservation_id AND r.company_id=e.company_id "
           "WHERE e.company_id=:company_id AND e.element_id=:element_id "
           "AND r.state IN ('ENQUIRY_HELD','CONFIRMED') "
           "AND e.arrival_date<:departure AND e.departure_date>:arrival")
    params = {'company_id': company_id, 'element_id': element_id,
              'departure': departure, 'arrival': arrival}
    if exclude_reservation_id is not None:
        sql += " AND r.id<>:exclude_id"
        params['exclude_id'] = exclude_reservation_id
    # sqlite3 accepts named placeholders directly; SQLAlchemy requires text().
    if hasattr(connection, 'dialect'):
        from sqlalchemy import text
        return connection.execute(text(sql + " LIMIT 1"), params).fetchone()
    return connection.execute(sql + " LIMIT 1", params).fetchone()

def validate_allocations(connection, company_id, allocations, exclude_reservation_id=None):
    from datetime import date
    seen = []
    for allocation in allocations:
        allocation = _mapping(allocation)
        element_id = int(allocation['element_id'])
        arrival, departure = str(allocation['arrival_date']), str(allocation['departure_date'])
        try:
            if date.fromisoformat(arrival) >= date.fromisoformat(departure):
                raise ValueError()
        except ValueError as exc:
            raise ReservationError('Invalid arrival or departure dates.') from exc
        element_sql = ('SELECT id FROM setup_elements WHERE company_id=:company_id '
                       'AND id=:element_id AND active=1')
        element_params = {'company_id': company_id, 'element_id': element_id}
        if hasattr(connection, 'dialect'):
            from sqlalchemy import text
            element = connection.execute(text(element_sql), element_params).fetchone()
        else:
            element = connection.execute(element_sql, element_params).fetchone()
        if element is None:
            raise ReservationError('Element does not belong to this client or is inactive.')
        if any(element_id == eid and arrival < end and departure > start for eid, start, end in seen):
            raise ReservationError('Overlapping allocation of the same Element within this reservation.')
        seen.append((element_id, arrival, departure))
        if inventory_conflict(connection, company_id, element_id, arrival, departure, exclude_reservation_id):
            raise ReservationError('Element is already allocated to another reservation.')


def _execute(connection, sql, params):
    """Execute a named-parameter statement on sqlite3 or SQLAlchemy."""
    if hasattr(connection, 'dialect'):
        from sqlalchemy import text
        return connection.execute(text(sql), params)
    return connection.execute(sql, params)


def _mapping(row):
    """Read rows by column name on both supported connection types."""
    return row._mapping if hasattr(row, '_mapping') else row


def transition(connection, company_id, reservation_id, target, updated_at, *, check_legacy=True):
    """Caller must hold the write transaction before checking and updating."""
    reservation = _execute(connection,
                           'SELECT state FROM reservations WHERE company_id=:company_id AND id=:reservation_id',
                           {'company_id': company_id, 'reservation_id': reservation_id}).fetchone()
    if reservation is None:
        raise ReservationError('Reservation not found for this client.')
    if not allowed_transition(str(_mapping(reservation)['state']), target):
        raise ReservationError('Reservation lifecycle transition is not permitted.')
    if target in ('ENQUIRY_HELD', 'CONFIRMED'):
        allocations = _execute(connection,
                               'SELECT element_id,arrival_date,departure_date FROM reservation_elements '
                               'WHERE company_id=:company_id AND reservation_id=:reservation_id',
                               {'company_id': company_id, 'reservation_id': reservation_id}).fetchall()
        if not allocations:
            raise ReservationError('A held or confirmed reservation must contain an Element.')
        validate_allocations(connection, company_id, allocations, reservation_id)
        if check_legacy:
            from .webv1_status_availability import _booking_conflict, _enquiry_conflict
            for item in allocations:
                eid = int(_mapping(item)['element_id'])
                arrival, departure = str(_mapping(item)['arrival_date']), str(_mapping(item)['departure_date'])
                if _booking_conflict(connection, company_id, eid, arrival, departure):
                    raise ReservationError('Element is already booked.')
                if _enquiry_conflict(connection, company_id, eid, arrival, departure):
                    raise ReservationError('Element is already held by another Enquiry.')
    _execute(connection,
             'UPDATE reservations SET state=:state,updated_at=:updated_at '
             'WHERE company_id=:company_id AND id=:reservation_id',
             {'state': target, 'updated_at': updated_at,
              'company_id': company_id, 'reservation_id': reservation_id})


def create_reservation(connection, company_id, reference, customer_id, allocations, created_at,
                       *, state='ENQUIRY_HELD', currency='EUR', notes=''):
    """Create one canonical reservation and its allocations in the caller's write transaction.

    Caller must begin a serialised write transaction and check any remaining
    legacy inventory before calling while both models coexist.
    """
    if state not in ('ENQUIRY_HELD', 'ENQUIRY_RELEASED'):
        raise ReservationError('New reservations must begin as an Enquiry.')
    if not allocations:
        raise ReservationError('An Enquiry must contain at least one Element.')
    if not str(reference).strip():
        raise ReservationError('An Enquiry reference is required.')
    if not str(currency).strip() or len(str(currency)) != 3:
        raise ReservationError('A valid three-letter currency is required.')
    if customer_id is not None:
        customer = _execute(connection,
            'SELECT id FROM customer_records WHERE company_id=:company_id AND id=:customer_id',
            {'company_id': company_id, 'customer_id': customer_id}).fetchone()
        if customer is None:
            raise ReservationError('Customer does not belong to this client.')
    validate_allocations(connection, company_id, allocations)
    from .webv1_status_availability import _booking_conflict, _enquiry_conflict
    for item in allocations:
        element_id = int(item['element_id'])
        arrival = str(item['arrival_date'])
        departure = str(item['departure_date'])
        if _booking_conflict(connection, company_id, element_id, arrival, departure):
            raise ReservationError('Element is already booked.')
        if _enquiry_conflict(connection, company_id, element_id, arrival, departure):
            raise ReservationError('Element is already held by another Enquiry.')
    total = sum(float(item.get('total_amount') or 0) for item in allocations)
    insert_sql = (
        'INSERT INTO reservations (company_id,reference,customer_id,state,currency,total_amount,notes,created_at,updated_at) '
        'VALUES (:company_id,:reference,:customer_id,:state,:currency,:total_amount,:notes,:created_at,:updated_at)')
    insert_params = {'company_id': company_id, 'reference': reference, 'customer_id': customer_id,
                     'state': state, 'currency': currency, 'total_amount': total,
                     'notes': notes, 'created_at': created_at, 'updated_at': created_at}
    if hasattr(connection, 'dialect') and connection.dialect.name == 'postgresql':
        cursor = _execute(connection, insert_sql + ' RETURNING id', insert_params)
        reservation_id = int(cursor.scalar_one())
    else:
        cursor = _execute(connection, insert_sql, insert_params)
        reservation_id = int(cursor.lastrowid)
    _insert_elements(connection, company_id, reservation_id, allocations)
    return reservation_id


def replace_reservation_elements(connection, company_id, reservation_id, allocations, updated_at,
                                 *, check_legacy=True):
    """Replace all Elements of an editable master record within the caller's transaction."""
    params = {'company_id': company_id, 'reservation_id': reservation_id}
    row = _execute(connection,
        'SELECT state FROM reservations WHERE company_id=:company_id AND id=:reservation_id',
        params).fetchone()
    if row is None:
        raise ReservationError('Reservation not found for this client.')
    if str(_mapping(row)['state']) not in ('ENQUIRY_HELD', 'ENQUIRY_RELEASED', 'CONFIRMED'):
        raise ReservationError('Cancelled reservations cannot be amended.')
    if not allocations:
        raise ReservationError('A reservation must contain at least one Element.')
    validate_allocations(connection, company_id, allocations, reservation_id)
    if check_legacy:
        from .webv1_status_availability import _booking_conflict, _enquiry_conflict
        for item in allocations:
            eid = int(item['element_id'])
            start, end = str(item['arrival_date']), str(item['departure_date'])
            if _booking_conflict(connection, company_id, eid, start, end):
                raise ReservationError('Element is already booked.')
            if _enquiry_conflict(connection, company_id, eid, start, end):
                raise ReservationError('Element is already held by another Enquiry.')
    _execute(connection, 'DELETE FROM reservation_elements WHERE company_id=:company_id AND reservation_id=:reservation_id', params)
    _insert_elements(connection, company_id, reservation_id, allocations)
    total = sum(float(item.get('total_amount') or 0) for item in allocations)
    _execute(connection,
             'UPDATE reservations SET total_amount=:total,updated_at=:updated_at '
             'WHERE company_id=:company_id AND id=:reservation_id',
             dict(params, total=total, updated_at=updated_at))


def _insert_elements(connection, company_id, reservation_id, allocations):
    for item in allocations:
        _execute(connection,
            'INSERT INTO reservation_elements (company_id,reservation_id,element_id,arrival_date,departure_date,'
            'total_amount,pricing_snapshot_json,lead_name) '
            'VALUES (:company_id,:reservation_id,:element_id,:arrival,:departure,:total,:snapshot,:lead_name)',
            {'company_id': company_id, 'reservation_id': reservation_id,
             'element_id': int(item['element_id']), 'arrival': str(item['arrival_date']),
             'departure': str(item['departure_date']), 'total': float(item.get('total_amount') or 0),
             'snapshot': str(item.get('pricing_snapshot_json') or '{}'),
             'lead_name': str(item.get('lead_name') or '')})
