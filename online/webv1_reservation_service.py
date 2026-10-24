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
