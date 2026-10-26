"""Write operations for the single-record reservation system.

All mutations use the shared reservation transaction boundary. The core SQL
and legacy availability queries still require PostgreSQL compatibility work
before the full reservation workflow can run on PostgreSQL.
"""
import json

from .webv1_reservation_core import create_reservation, replace_reservation_elements, transition


def _history(connection, company_id, reservation_id, event, when, actor, *, old=None, new=None, reason='', details=None):
    connection.execute(
        'INSERT INTO reservation_history (company_id,reservation_id,event_type,old_state,new_state,actor,reason,details_json,created_at) '
        'VALUES (?,?,?,?,?,?,?,?,?)',
        (company_id, reservation_id, event, old, new, str(actor), str(reason),
         json.dumps(details or {}, separators=(',', ':')), when))


def create_enquiry(database, company_id, reference, customer_id, allocations, created_at,
                   *, currency='EUR', notes='', actor='System'):
    with database.reservation_write_transaction(company_id) as connection:
        rid = create_reservation(connection, company_id, reference, customer_id,
                                 allocations, created_at, currency=currency, notes=notes)
        _history(connection, company_id, rid, 'CREATED', created_at, actor,
                 new='ENQUIRY_HELD', details={'element_count': len(allocations)})
        return rid


def amend_reservation(database, company_id, reservation_id, allocations, updated_at, *, actor='System', reason=''):
    with database.reservation_write_transaction(company_id) as connection:
        replace_reservation_elements(connection, company_id, reservation_id, allocations, updated_at)
        _history(connection, company_id, reservation_id, 'AMENDED', updated_at, actor,
                 reason=reason, details={'element_count': len(allocations)})


def change_reservation_state(database, company_id, reservation_id, state, updated_at, *, actor='System', reason=''):
    with database.reservation_write_transaction(company_id) as connection:
        previous = connection.execute('SELECT state FROM reservations WHERE company_id=? AND id=?',
                                      (company_id, reservation_id)).fetchone()
        transition(connection, company_id, reservation_id, state, updated_at)
        _history(connection, company_id, reservation_id, 'STATUS_CHANGED', updated_at, actor,
                 old=previous['state'] if previous else None, new=state, reason=reason)


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
        history = connection.execute(
            'SELECT * FROM reservation_history WHERE company_id=? AND reservation_id=? ORDER BY id DESC',
            (company_id, reservation_id)).fetchall()
        return {'reservation': dict(record), 'elements': [dict(item) for item in elements],
                'history': [dict(item) for item in history]}


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
