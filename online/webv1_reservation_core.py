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
    """Check one company's canonical reservation allocations."""
    sql = ("SELECT r.id FROM reservation_elements e "
           "JOIN reservations r ON r.id=e.reservation_id AND r.company_id=e.company_id "
           "WHERE e.company_id=? AND e.element_id=? "
           "AND r.state IN ('ENQUIRY_HELD','CONFIRMED') "
           "AND e.arrival_date<? AND e.departure_date>?")
    params = [company_id, element_id, departure, arrival]
    if exclude_reservation_id is not None:
        sql += " AND r.id<>?"
        params.append(exclude_reservation_id)
    return connection.execute(sql + " LIMIT 1", params).fetchone()


def validate_allocations(connection, company_id, allocations, exclude_reservation_id=None):
    from datetime import date
    seen = []
    for allocation in allocations:
        element_id = int(allocation['element_id'])
        arrival, departure = str(allocation['arrival_date']), str(allocation['departure_date'])
        try:
            if date.fromisoformat(arrival) >= date.fromisoformat(departure):
                raise ValueError()
        except ValueError as exc:
            raise ReservationError('Invalid arrival or departure dates.') from exc
        element = connection.execute('SELECT id FROM setup_elements WHERE company_id=? AND id=? AND active=1',
                                     (company_id, element_id)).fetchone()
        if element is None:
            raise ReservationError('Element does not belong to this client or is inactive.')
        if any(element_id == eid and arrival < end and departure > start for eid, start, end in seen):
            raise ReservationError('Overlapping allocation of the same Element within this reservation.')
        seen.append((element_id, arrival, departure))
        if inventory_conflict(connection, company_id, element_id, arrival, departure, exclude_reservation_id):
            raise ReservationError('Element is already allocated to another reservation.')


def transition(connection, company_id, reservation_id, target, updated_at):
    """Caller must hold the write transaction before checking and updating."""
    reservation = connection.execute('SELECT state FROM reservations WHERE company_id=? AND id=?',
                                     (company_id, reservation_id)).fetchone()
    if reservation is None:
        raise ReservationError('Reservation not found for this client.')
    if not allowed_transition(str(reservation['state']), target):
        raise ReservationError('Reservation lifecycle transition is not permitted.')
    if target in ('ENQUIRY_HELD', 'CONFIRMED'):
        allocations = connection.execute('SELECT element_id,arrival_date,departure_date FROM reservation_elements '
                                         'WHERE company_id=? AND reservation_id=?', (company_id, reservation_id)).fetchall()
        if not allocations:
            raise ReservationError('A held or confirmed reservation must contain an Element.')
        validate_allocations(connection, company_id, allocations, reservation_id)
    connection.execute('UPDATE reservations SET state=?,updated_at=? WHERE company_id=? AND id=?',
                       (target, updated_at, company_id, reservation_id))


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
    validate_allocations(connection, company_id, allocations)
    total = sum(float(item.get('total_amount') or 0) for item in allocations)
    cursor = connection.execute(
        'INSERT INTO reservations (company_id,reference,customer_id,state,currency,total_amount,notes,created_at,updated_at) '
        'VALUES (?,?,?,?,?,?,?,?,?)',
        (company_id, reference, customer_id, state, currency, total, notes, created_at, created_at))
    reservation_id = int(cursor.lastrowid)
    for item in allocations:
        connection.execute(
            'INSERT INTO reservation_elements (company_id,reservation_id,element_id,arrival_date,departure_date,'
            'total_amount,pricing_snapshot_json,lead_name) VALUES (?,?,?,?,?,?,?,?)',
            (company_id, reservation_id, int(item['element_id']), str(item['arrival_date']),
             str(item['departure_date']), float(item.get('total_amount') or 0),
             str(item.get('pricing_snapshot_json') or '{}'), str(item.get('lead_name') or '')))
    return reservation_id
