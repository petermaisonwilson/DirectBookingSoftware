"""Atomic reservation writes and tenant-scoped reads on SQLite and PostgreSQL."""
import json

from .webv1_reservation_core import create_reservation, replace_reservation_elements, transition, _execute, _mapping, ReservationError


def _history(connection, company_id, reservation_id, event, when, actor, *, old=None, new=None, reason='', details=None):
    _execute(connection,
        'INSERT INTO reservation_history (company_id,reservation_id,event_type,old_state,new_state,actor,reason,details_json,created_at) '
        'VALUES (:company_id,:reservation_id,:event,:old,:new,:actor,:reason,:details,:when)',
        {'company_id': company_id, 'reservation_id': reservation_id, 'event': event,
         'old': old, 'new': new, 'actor': str(actor), 'reason': str(reason),
         'details': json.dumps(details or {}, separators=(',', ':')), 'when': when})


def _check_closures_and_holds(connection, company_id, allocations):
    """Recheck live inventory guards under the reservation write lock."""
    from .database import iso_now
    from datetime import date
    now = iso_now()
    for item in allocations:
        item = _mapping(item)
        arrival, departure = str(item['arrival_date']), str(item['departure_date'])
        try:
            first, last = date.fromisoformat(arrival), date.fromisoformat(departure)
            if first.isoformat() != arrival or last.isoformat() != departure or first >= last:
                raise ValueError()
        except ValueError as exc:
            raise ReservationError('Invalid arrival or departure dates.') from exc
        params = {'company_id': company_id, 'element_id': int(item['element_id']),
                  'arrival': str(item['arrival_date']), 'departure': str(item['departure_date']),
                  'now': now}
        closed = _execute(connection,
            'SELECT id FROM element_closures WHERE company_id=:company_id AND element_id=:element_id '
            'AND date(start_date)<date(:departure) AND date(end_date)>date(:arrival) LIMIT 1', params).fetchone()
        if closed is not None:
            raise ReservationError('Element is closed for the selected dates.')
        held = _execute(connection,
            'SELECT id FROM element_holds WHERE company_id=:company_id AND element_id=:element_id '
            'AND date(arrival_date)<date(:departure) AND date(departure_date)>date(:arrival) '
            'AND expires_at>:now LIMIT 1', params).fetchone()
        if held is not None:
            raise ReservationError('Element is temporarily held in a basket.')


def create_enquiry(database, company_id, reference, customer_id, allocations, created_at,
                   *, currency='EUR', notes='', actor='System'):
    with database.reservation_write_transaction(company_id) as connection:
        _check_closures_and_holds(connection, company_id, allocations)
        rid = create_reservation(connection, company_id, reference, customer_id,
                                 allocations, created_at, currency=currency, notes=notes)
        _history(connection, company_id, rid, 'CREATED', created_at, actor,
                 new='ENQUIRY_HELD', details={'element_count': len(allocations)})
        return rid


def _require_standalone_command(connection, company_id, reservation_id):
    """A raw allocation/state edit cannot bypass operational finance or pricing."""
    owner = _execute(connection,
        'SELECT enquiry_id,booking_id FROM reservations WHERE company_id=:tenant AND id=:reservation',
        {'tenant': company_id, 'reservation': reservation_id}).fetchone()
    if owner is not None:
        owner = _mapping(owner)
        if owner['enquiry_id'] is not None or owner['booking_id'] is not None:
            raise ReservationError('Use the operational reservation command so pricing, finance and history are validated together.')


def amend_reservation(database, company_id, reservation_id, allocations, updated_at, *, actor='System', reason=''):
    with database.reservation_write_transaction(company_id) as connection:
        _require_standalone_command(connection, company_id, reservation_id)
        _check_closures_and_holds(connection, company_id, allocations)
        replace_reservation_elements(connection, company_id, reservation_id, allocations, updated_at)
        _history(connection, company_id, reservation_id, 'AMENDED', updated_at, actor,
                 reason=reason, details={'element_count': len(allocations)})


def change_reservation_state(database, company_id, reservation_id, state, updated_at, *, actor='System', reason=''):
    with database.reservation_write_transaction(company_id) as connection:
        _require_standalone_command(connection, company_id, reservation_id)
        previous = _execute(connection,
            'SELECT state FROM reservations WHERE company_id=:company_id AND id=:reservation_id',
            {'company_id': company_id, 'reservation_id': reservation_id}).fetchone()
        if state in ('ENQUIRY_HELD', 'CONFIRMED'):
            allocations = _execute(connection,
                'SELECT element_id,arrival_date,departure_date FROM reservation_elements '
                'WHERE company_id=:company_id AND reservation_id=:reservation_id',
                {'company_id': company_id, 'reservation_id': reservation_id}).fetchall()
            _check_closures_and_holds(connection, company_id, allocations)
        transition(connection, company_id, reservation_id, state, updated_at)
        _history(connection, company_id, reservation_id, 'STATUS_CHANGED', updated_at, actor,
                 old=_mapping(previous)['state'] if previous else None, new=state, reason=reason)


def _read_rows(database, sql, parameters):
    """Read reservation rows through the configured database engine.

    SQLAlchemy uses named parameters on both SQLite and PostgreSQL. Legacy
    SQLite uses its native connection; PostgreSQL uses SQLAlchemy text().
    """
    from sqlalchemy import text

    if database.is_sqlite:
        with database.connect() as connection:
            return [dict(row) for row in connection.execute(
                sql, parameters).fetchall()]
    with database.sql_connection() as connection:
        return [dict(row) for row in connection.execute(
            text(sql), parameters).mappings().all()]


def reservation_summary(database, company_id, reservation_id):
    """Return the master record, Elements and history for one tenant."""
    params = {'company_id': company_id, 'reservation_id': reservation_id}
    records = _read_rows(
        database, 'SELECT * FROM reservations WHERE company_id=:company_id AND id=:reservation_id', params)
    if not records:
        return None
    elements = _read_rows(
        database,
        'SELECT re.*, se.name AS element_name FROM reservation_elements re '
        'LEFT JOIN setup_elements se ON se.id=re.element_id AND se.company_id=re.company_id '
        'WHERE re.company_id=:company_id AND re.reservation_id=:reservation_id ORDER BY re.id',
        params)
    history = _read_rows(
        database,
        'SELECT * FROM reservation_history WHERE company_id=:company_id '
        'AND reservation_id=:reservation_id ORDER BY id DESC', params)
    return {'reservation': records[0], 'elements': elements, 'history': history}


def list_reservations(database, company_id, *, state=None):
    """List master records for the current tenant on either engine."""
    sql = 'SELECT * FROM reservations WHERE company_id=:company_id'
    params = {'company_id': company_id}
    if state is not None:
        sql += ' AND state=:state'
        params['state'] = state
    return _read_rows(database, sql + ' ORDER BY created_at DESC,id DESC', params)
