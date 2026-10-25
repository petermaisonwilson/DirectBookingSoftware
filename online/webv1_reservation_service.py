"""Write operations for the single-record reservation system.

All changes use one SQLite write transaction, so a failed availability check
cannot leave behind half a reservation.
"""
from .webv1_reservation_core import create_reservation, replace_reservation_elements, transition


def create_enquiry(database, company_id, reference, customer_id, allocations, created_at,
                   *, currency='EUR', notes=''):
    with database.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        return create_reservation(connection, company_id, reference, customer_id,
                                  allocations, created_at, currency=currency, notes=notes)


def amend_reservation(database, company_id, reservation_id, allocations, updated_at):
    with database.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        replace_reservation_elements(connection, company_id, reservation_id, allocations, updated_at)


def change_reservation_state(database, company_id, reservation_id, state, updated_at):
    with database.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        transition(connection, company_id, reservation_id, state, updated_at)


def reservation_summary(database, company_id, reservation_id):
    """Return the master record and its Elements, scoped to one client."""
    with database.connect() as connection:
        record = connection.execute(
            'SELECT * FROM reservations WHERE company_id=? AND id=?',
            (company_id, reservation_id)).fetchone()
        if record is None:
            return None
        elements = connection.execute(
            'SELECT re.*, se.name AS element_name FROM reservation_elements re '
            'LEFT JOIN setup_elements se ON se.id=re.element_id AND se.company_id=re.company_id '
            'WHERE re.company_id=? AND re.reservation_id=? ORDER BY re.id',
            (company_id, reservation_id)).fetchall()
        return {'reservation': dict(record), 'elements': [dict(item) for item in elements]}


def list_reservations(database, company_id, *, state=None):
    """List master records for the current client only."""
    with database.connect() as connection:
        sql = 'SELECT * FROM reservations WHERE company_id=?'
        params = [company_id]
        if state is not None:
            sql += ' AND state=?'
            params.append(state)
        sql += ' ORDER BY created_at DESC,id DESC'
        return [dict(row) for row in connection.execute(sql, params).fetchall()]
