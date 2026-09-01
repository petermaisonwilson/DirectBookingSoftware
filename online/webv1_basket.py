from __future__ import annotations

from datetime import timedelta

from fastapi import Request
from fastapi.responses import JSONResponse

from .app import COOKIE_NAME, form_data
from .database import iso_now
from .setup015_core import audit, require_csrf
from . import webv1_availability as availability


def _basket_row(connection, company_id: int, token: str, hold_id: int):
    return connection.execute(
        '''SELECT h.*,e.name AS element_name,e.element_type
           FROM element_holds h
           JOIN setup_elements e ON e.id=h.element_id AND e.company_id=h.company_id
           WHERE h.id=? AND h.company_id=? AND h.session_token=?''',
        (hold_id, company_id, token),
    ).fetchone()


def _purge_expired_basket_rows(connection, company_id: int, token: str) -> int:
    expired = [int(r['id']) for r in connection.execute(
        'SELECT id FROM element_holds WHERE company_id=? AND session_token=? AND expires_at<=?',
        (company_id, token, iso_now()),
    ).fetchall()]
    for hold_id in expired:
        connection.execute('DELETE FROM hold_requirement_people WHERE hold_id=?', (hold_id,))
        connection.execute('DELETE FROM hold_requirement_addons WHERE hold_id=?', (hold_id,))
    if expired:
        connection.execute('DELETE FROM element_holds WHERE company_id=? AND session_token=? AND expires_at<=?', (company_id, token, iso_now()))
    return len(expired)


def _snapshot_current_requirements(connection, company_id: int, token: str, hold_id: int) -> None:
    connection.execute('DELETE FROM hold_requirement_people WHERE hold_id=?', (hold_id,))
    connection.execute('DELETE FROM hold_requirement_addons WHERE hold_id=?', (hold_id,))
    connection.execute(
        '''INSERT INTO hold_requirement_people(hold_id,company_id,person_type_id,quantity,ages_json)
           SELECT ?,company_id,person_type_id,quantity,ages_json
           FROM booking_requirement_people WHERE company_id=? AND session_token=?''',
        (hold_id, company_id, token),
    )
    connection.execute(
        '''INSERT INTO hold_requirement_addons(hold_id,company_id,addon_id,quantity)
           SELECT ?,company_id,addon_id,quantity
           FROM booking_requirement_addons WHERE company_id=? AND session_token=?''',
        (hold_id, company_id, token),
    )


def register_basket_routes(app) -> None:
    database = app.state.database

    @app.get('/availability/basket')
    def basket_status(request: Request):
        context, company_id = availability._session_company(database, request)
        token = request.cookies.get(COOKIE_NAME, '')
        now = availability._now()
        with database.connect() as c:
            _purge_expired_basket_rows(c, company_id, token)
            held = c.execute(
                '''SELECT h.*, e.name AS element_name, e.element_type
                   FROM element_holds h
                   JOIN setup_elements e ON e.id=h.element_id AND e.company_id=h.company_id
                   WHERE h.company_id=? AND h.session_token=?
                   ORDER BY h.created_at, h.id''',
                (company_id, token),
            ).fetchall()
        items = []
        for row in held:
            items.append({
                'id': int(row['id']),
                'element_id': int(row['element_id']),
                'element_name': str(row['element_name']),
                'element_type': str(row['element_type']),
                'lead_name': str(row['lead_name'] or ''),
                'arrival_date': str(row['arrival_date']),
                'departure_date': str(row['departure_date']),
                'renewal_required_at': str(row['renewal_required_at']),
                'expires_at': str(row['expires_at']),
                'needs_confirmation': now >= availability._utc(str(row['renewal_required_at'])),
            })
        anchor = None
        if items:
            anchor = {'arrival_date': items[0]['arrival_date'], 'departure_date': items[0]['departure_date']}
        return JSONResponse({'items': items, 'count': len(items), 'anchor': anchor})

    @app.post('/availability/basket/remove')
    async def basket_remove(request: Request):
        context, company_id = availability._session_company(database, request)
        data = await form_data(request)
        require_csrf(context, data)
        token = request.cookies.get(COOKIE_NAME, '')
        try:
            hold_id = int(data.get('hold_id', ''))
        except (TypeError, ValueError):
            return JSONResponse({'ok': False, 'error': 'Invalid basket item.'}, status_code=400)
        with database.connect() as c:
            _purge_expired_basket_rows(c, company_id, token)
            row = _basket_row(c, company_id, token, hold_id)
            if row is None:
                return JSONResponse({'ok': False, 'error': 'That basket item has already expired or been removed.'}, status_code=404)
            before = dict(row)
            c.execute('DELETE FROM hold_requirement_people WHERE hold_id=?', (hold_id,))
            c.execute('DELETE FROM hold_requirement_addons WHERE hold_id=?', (hold_id,))
            c.execute('DELETE FROM element_holds WHERE id=? AND company_id=? AND session_token=?', (hold_id, company_id, token))
        audit(database, context, company_id, 'ELEMENT_HOLD_REMOVED', 'element_hold', hold_id, before, {'removed': True})
        return JSONResponse({'ok': True, 'removed': 1, 'hold_id': hold_id})

    @app.post('/availability/basket/update')
    async def basket_update(request: Request):
        context, company_id = availability._session_company(database, request)
        data = await form_data(request)
        require_csrf(context, data)
        token = request.cookies.get(COOKIE_NAME, '')
        try:
            hold_id = int(data.get('hold_id', ''))
            element_id = int(data.get('element_id', ''))
        except (TypeError, ValueError):
            return JSONResponse({'ok': False, 'error': 'Invalid booking item.'}, status_code=400)
        arrival = data.get('arrival_date', '')
        departure = data.get('departure_date', '')

        with database.connect() as c:
            _purge_expired_basket_rows(c, company_id, token)
            current = _basket_row(c, company_id, token, hold_id)
            if current is None:
                return JSONResponse({'ok': False, 'error': 'That booking item has expired or been removed.'}, status_code=404)
            before = dict(current)
            element = c.execute(
                'SELECT name,element_type FROM setup_elements WHERE id=? AND company_id=? AND active=1',
                (element_id, company_id),
            ).fetchone()
            if element is None:
                return JSONResponse({'ok': False, 'error': 'That Element is no longer active.'}, status_code=409)
            competing_hold = c.execute(
                '''SELECT id FROM element_holds
                   WHERE company_id=? AND element_id=? AND id<>?
                     AND date(arrival_date)<date(?) AND date(departure_date)>date(?)
                     AND expires_at>? LIMIT 1''',
                (company_id, element_id, hold_id, departure, arrival, iso_now()),
            ).fetchone()
            booking_conflict = availability._booking_conflict(c, company_id, element_id, arrival, departure)
            closure_conflict = availability._closure_conflict(c, company_id, element_id, arrival, departure)
            if competing_hold or booking_conflict or closure_conflict:
                return JSONResponse({'ok': False, 'error': 'That Element is already unavailable or held. Please choose another.'}, status_code=409)

            settings = c.execute('SELECT hold_seconds,grace_seconds FROM company_hold_settings WHERE company_id=?', (company_id,)).fetchone()
            hold_seconds = int(settings['hold_seconds']) if settings else 600
            grace_seconds = int(settings['grace_seconds']) if settings else 60
            working = c.execute('SELECT lead_name FROM booking_requirement_sessions WHERE company_id=? AND session_token=?', (company_id, token)).fetchone()
            lead_name = str(working['lead_name'] or '') if working else str(current['lead_name'] or '')
            now = availability._now()
            prompt = now + timedelta(seconds=hold_seconds)
            expires = prompt + timedelta(seconds=grace_seconds)
            c.execute(
                '''UPDATE element_holds SET element_id=?,holder_user_id=?,arrival_date=?,departure_date=?,lead_name=?,
                   renewal_required_at=?,expires_at=?,updated_at=?
                   WHERE id=? AND company_id=? AND session_token=?''',
                (element_id, context['user_id'], arrival, departure, lead_name, prompt.isoformat(timespec='seconds'), expires.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'), hold_id, company_id, token),
            )
            _snapshot_current_requirements(c, company_id, token, hold_id)

        after = {
            'element_id': element_id,
            'element_name': str(element['name']),
            'element_type': str(element['element_type']),
            'lead_name': lead_name,
            'arrival_date': arrival,
            'departure_date': departure,
            'expires_at': expires.isoformat(timespec='seconds'),
        }
        audit(database, context, company_id, 'ELEMENT_HOLD_UPDATED', 'element_hold', hold_id, before, after)
        return JSONResponse({'ok': True, 'hold_id': hold_id, 'item': after})
