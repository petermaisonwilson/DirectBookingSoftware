from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .setup015_calculator import _addon_rule
from .setup015_core import rows
from .setup015_readiness import element_available_setup_ready
from . import webv1_availability as legacy


def _booking_conflict(connection, company_id: int, element_id: int, start: str, end: str, exclude_booking_id: int | None = None):
    sql = '''
        SELECT b.id,b.reference,b.status,b.workflow_status_id,be.arrival_date,be.departure_date,
               s.name AS workflow_name,s.colour,s.blocks_availability,s.internal_state
        FROM booking_elements be
        JOIN bookings b ON b.id=be.booking_id AND b.company_id=be.company_id
        LEFT JOIN booking_status_definitions s ON s.id=b.workflow_status_id AND s.company_id=b.company_id
        WHERE be.company_id=? AND be.element_id=?
          AND COALESCE(s.blocks_availability, CASE WHEN b.status='cancelled' THEN 0 ELSE 1 END)=1
          AND date(be.arrival_date)<date(?) AND date(be.departure_date)>date(?)
    '''
    params: list[Any] = [company_id, element_id, end, start]
    if exclude_booking_id is not None:
        sql += ' AND b.id<>?'; params.append(exclude_booking_id)
    sql += ' ORDER BY be.arrival_date LIMIT 1'
    return connection.execute(sql, params).fetchone()


def _enquiry_conflict(connection, company_id: int, element_id: int, start: str, end: str, exclude_enquiry_id: int | None = None):
    sql = '''
        SELECT e.id,e.customer_id,e.status,e.arrival_date,e.departure_date,e.availability_expires_at,
               s.id AS workflow_status_id,s.name AS workflow_name,s.colour,s.blocks_availability,s.internal_state
        FROM enquiries e
        JOIN enquiry_requests er ON er.enquiry_id=e.id AND er.company_id=e.company_id
        JOIN booking_status_definitions s ON s.id=e.workflow_status_id AND s.company_id=e.company_id
        WHERE e.company_id=? AND er.element_id=?
          AND e.status NOT IN ('closed','converted')
          AND s.blocks_availability=1
          AND (e.availability_expires_at IS NULL OR datetime(e.availability_expires_at)>datetime('now'))
          AND date(e.arrival_date)<date(?) AND date(e.departure_date)>date(?)
    '''
    params: list[Any] = [company_id, element_id, end, start]
    if exclude_enquiry_id is not None:
        sql += ' AND e.id<>?'; params.append(exclude_enquiry_id)
    sql += ' ORDER BY e.arrival_date LIMIT 1'
    return connection.execute(sql, params).fetchone()


def availability_state(database, company_id: int, element_id: int, arrival: str, departure: str, *, session_token: str = '', exclude_booking_id: int | None = None, exclude_enquiry_id: int | None = None) -> dict[str, Any]:
    try:
        start, end = date.fromisoformat(arrival), date.fromisoformat(departure)
    except ValueError:
        return {'available': False, 'state': 'INVALID', 'reason': 'Enter valid arrival and departure dates.'}
    if end <= start:
        return {'available': False, 'state': 'INVALID', 'reason': 'Departure must be after arrival.'}
    if start.year != (end - timedelta(days=1)).year:
        return {'available': False, 'state': 'OUT_OF_SEASON', 'reason': 'The stay must remain within one pricing year.'}
    window = legacy.operating_window(database, company_id, start.year)
    if window is None:
        return {'available': False, 'state': 'OUT_OF_SEASON', 'reason': 'No operating season is configured for these dates.'}
    if start < window[0] or end > window[1]:
        return {'available': False, 'state': 'OUT_OF_SEASON', 'reason': 'The selected stay is outside the configured operating season.'}
    with database.connect() as c:
        element = c.execute('SELECT * FROM setup_elements WHERE id=? AND company_id=?', (element_id, company_id)).fetchone()
        if element is None or not int(element['active']):
            return {'available': False, 'state': 'INACTIVE', 'reason': 'Element is inactive.'}

    # Setup is a gate before inventory. Existing Seasons may be extended safely
    # because their stored Season price continues to apply to the new dates.
    # A brand-new Season has no Element rates, so only those new dates remain
    # off sale until pricing is completed.
    setup_ready, setup_reason = element_available_setup_ready(
        database, company_id, element_id, start, end
    )
    if not setup_ready:
        return {'available': False, 'state': 'SETUP_INCOMPLETE', 'reason': setup_reason}

    with database.connect() as c:
        closed = legacy._closure_conflict(c, company_id, element_id, arrival, departure)
        if closed:
            return {'available': False, 'state': 'CLOSED', 'reason': str(closed['reason'] or 'Closed'), 'closure_id': int(closed['id'])}
        booked = _booking_conflict(c, company_id, element_id, arrival, departure, exclude_booking_id)
        if booked:
            return {'available': False, 'state': 'BOOKED', 'reason': str(booked['workflow_name'] or f"Booked: {booked['reference']}"), 'booking_id': int(booked['id']), 'booking_reference': str(booked['reference'])}
        enquiry = _enquiry_conflict(c, company_id, element_id, arrival, departure, exclude_enquiry_id)
        if enquiry:
            return {'available': False, 'state': 'ENQUIRY', 'reason': str(enquiry['workflow_name'] or 'Enquiry / Held'), 'enquiry_id': int(enquiry['id']), 'expires_at': enquiry['availability_expires_at']}
        legacy._purge_expired_holds(c)
        held = c.execute('''SELECT * FROM element_holds WHERE company_id=? AND element_id=?
                            AND date(arrival_date)<date(?) AND date(departure_date)>date(?)
                            ORDER BY expires_at DESC LIMIT 1''', (company_id, element_id, departure, arrival)).fetchone()
        if held:
            own = bool(session_token and str(held['session_token']) == session_token)
            return {'available': own, 'state': 'HELD_BY_YOU' if own else 'HELD', 'reason': 'Held in your basket' if own else 'Temporarily held', 'hold_id': int(held['id']), 'expires_at': str(held['expires_at']), 'renewal_required_at': str(held['renewal_required_at'])}
    return {'available': True, 'state': 'AVAILABLE', 'reason': ''}


def available_elements(database, company_id: int, element_type: str, arrival: str, departure: str, *, session_token: str = '') -> list[dict[str, Any]]:
    try:
        year = date.fromisoformat(arrival).year
    except ValueError:
        return []
    result: list[dict[str, Any]] = []
    for element in rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1 AND element_type=? ORDER BY name COLLATE NOCASE', (company_id, element_type)):
        state = availability_state(database, company_id, int(element['id']), arrival, departure, session_token=session_token)
        if not state['available']:
            continue
        addons = []
        for addon in rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (company_id,)):
            rule = _addon_rule(database, company_id, year, element, int(addon['id']))
            addons.append({'id': int(addon['id']), 'name': str(addon['name']), 'available': bool(rule['allowed'])})
        result.append({'id': int(element['id']), 'name': str(element['name']), 'element_type': str(element['element_type']), 'state': state['state'], 'addons': addons})
    return result


def install_status_aware_availability() -> None:
    # Keep the proven availability/hold/closure routes, but make every one of
    # them resolve inventory conflicts and Setup readiness through this shared
    # status-aware path.
    legacy._booking_conflict = _booking_conflict
    legacy.availability_state = availability_state
    legacy.available_elements = available_elements
