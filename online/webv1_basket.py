from __future__ import annotations
from .sql import execute_sql

from datetime import date, timedelta

from fastapi import Request
from fastapi.responses import JSONResponse

from .app import COOKIE_NAME, form_data
from .database import iso_now
from .setup015_core import audit, require_csrf
from . import webv1_availability as availability
from .webv1_booking_requirements import _saved_requirements
from .webv1_booking_requirements_core import element_reasons


def _basket_row(connection, company_id: int, token: str, hold_id: int):
    return execute_sql(connection,
        'SELECT h.*,e.name AS element_name,e.element_type\n           FROM element_holds h\n           JOIN setup_elements e ON e.id=h.element_id AND e.company_id=h.company_id\n           WHERE h.id=:q713_0 AND h.company_id=:q713_1 AND h.session_token=:q713_2',
        (hold_id, company_id, token),
    ).fetchone()


def _purge_expired_basket_rows(connection, company_id: int, token: str) -> int:
    expired = [int(r['id']) for r in execute_sql(connection,
        'SELECT id FROM element_holds WHERE company_id=:q718_0 AND session_token=:q718_1 AND expires_at<=:q718_2',
        (company_id, token, iso_now()),
    ).fetchall()]
    for hold_id in expired:
        execute_sql(connection, 'DELETE FROM hold_requirement_people WHERE hold_id=:q710_0', (hold_id,))
        execute_sql(connection, 'DELETE FROM hold_requirement_addons WHERE hold_id=:q711_0', (hold_id,))
    if expired:
        execute_sql(connection, 'DELETE FROM element_holds WHERE company_id=:q712_0 AND session_token=:q712_1 AND expires_at<=:q712_2', (company_id, token, iso_now()))
    return len(expired)


def _snapshot_current_requirements(connection, company_id: int, token: str, hold_id: int) -> None:
    execute_sql(connection, 'DELETE FROM hold_requirement_people WHERE hold_id=:q706_0', (hold_id,))
    execute_sql(connection, 'DELETE FROM hold_requirement_addons WHERE hold_id=:q707_0', (hold_id,))
    execute_sql(connection,
        'INSERT INTO hold_requirement_people(hold_id,company_id,person_type_id,quantity,ages_json)\n           SELECT :q708_0,company_id,person_type_id,quantity,ages_json\n           FROM booking_requirement_people WHERE company_id=:q708_1 AND session_token=:q708_2',
        (hold_id, company_id, token),
    )
    execute_sql(connection,
        'INSERT INTO hold_requirement_addons(hold_id,company_id,addon_id,quantity)\n           SELECT :q709_0,company_id,addon_id,quantity\n           FROM booking_requirement_addons WHERE company_id=:q709_1 AND session_token=:q709_2',
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
            held = execute_sql(c,
                'SELECT h.*, e.name AS element_name, e.element_type\n                   FROM element_holds h\n                   JOIN setup_elements e ON e.id=h.element_id AND e.company_id=h.company_id\n                   WHERE h.company_id=:q719_0 AND h.session_token=:q719_1 AND h.expires_at>:q719_2\n                   ORDER BY h.created_at, h.id',
                (company_id, token, iso_now()),
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
        with database.reservation_write_transaction(company_id) as c:
            _purge_expired_basket_rows(c, company_id, token)
            row = _basket_row(c, company_id, token, hold_id)
            if row is None:
                return JSONResponse({'ok': False, 'error': 'That basket item has already expired or been removed.'}, status_code=404)
            before = dict(row)
            execute_sql(c, 'DELETE FROM hold_requirement_people WHERE hold_id=:q714_0', (hold_id,))
            execute_sql(c, 'DELETE FROM hold_requirement_addons WHERE hold_id=:q715_0', (hold_id,))
            execute_sql(c, 'DELETE FROM element_holds WHERE id=:q716_0 AND company_id=:q716_1 AND session_token=:q716_2', (hold_id, company_id, token))
            audit(database, context, company_id, 'ELEMENT_HOLD_REMOVED', 'element_hold', hold_id, before, {'removed': True}, connection=c)
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
        arrival = str(data.get('arrival_date', '') or '')
        departure = str(data.get('departure_date', '') or '')
        try:
            arrival_day = date.fromisoformat(arrival)
            departure_day = date.fromisoformat(departure)
        except ValueError:
            return JSONResponse({'ok': False, 'error': 'Enter valid arrival and departure dates.'}, status_code=400)
        if departure_day <= arrival_day:
            return JSONResponse({'ok': False, 'error': 'Departure must be after arrival.'}, status_code=400)

        people, requirements, ready, _, _ = _saved_requirements(database, company_id, token)

        with database.reservation_write_transaction(company_id) as c:
            _purge_expired_basket_rows(c, company_id, token)
            current = _basket_row(c, company_id, token, hold_id)
            if current is None:
                return JSONResponse({'ok': False, 'error': 'That booking item has expired or been removed.'}, status_code=404)
            before = dict(current)
            element = execute_sql(c,
                'SELECT * FROM setup_elements WHERE id=:q720_0 AND company_id=:q720_1 AND active=1',
                (element_id, company_id),
            ).fetchone()
            if element is None:
                return JSONResponse({'ok': False, 'error': 'That Element is no longer active.'}, status_code=409)
            if ready:
                reasons = element_reasons(database, company_id, arrival_day.year, element, people, requirements)
                if reasons:
                    return JSONResponse({'ok': False, 'error': 'That Element is not suitable: ' + ' · '.join(reasons)}, status_code=409)
            competing_hold = execute_sql(c,
                'SELECT id FROM element_holds\n                   WHERE company_id=:q721_0 AND element_id=:q721_1 AND id<>:q721_2\n                     AND date(arrival_date)<date(:q721_3) AND date(departure_date)>date(:q721_4)\n                     AND expires_at>:q721_5 LIMIT 1',
                (company_id, element_id, hold_id, departure, arrival, iso_now()),
            ).fetchone()
            booking_conflict = availability._booking_conflict(c, company_id, element_id, arrival, departure)
            closure_conflict = availability._closure_conflict(c, company_id, element_id, arrival, departure)
            from .webv1_reservation_core import migrated_inventory_conflict
            from .webv1_status_availability import _enquiry_conflict
            canonical = migrated_inventory_conflict(c, company_id, element_id, arrival, departure)
            enquiry = _enquiry_conflict(c, company_id, element_id, arrival, departure)
            if canonical or enquiry or competing_hold or booking_conflict or closure_conflict:
                return JSONResponse({'ok': False, 'error': 'That Element is already unavailable or held. Please choose another.'}, status_code=409)

            settings = execute_sql(c, 'SELECT hold_seconds,grace_seconds FROM company_hold_settings WHERE company_id=:q722_0', (company_id,)).fetchone()
            hold_seconds = int(settings['hold_seconds']) if settings else 600
            grace_seconds = int(settings['grace_seconds']) if settings else 60
            working = execute_sql(c, 'SELECT lead_name FROM booking_requirement_sessions WHERE company_id=:q723_0 AND session_token=:q723_1', (company_id, token)).fetchone()
            lead_name = str(working['lead_name'] or '') if working else str(current['lead_name'] or '')
            now = availability._now()
            prompt = now + timedelta(seconds=hold_seconds)
            expires = prompt + timedelta(seconds=grace_seconds)
            execute_sql(c,
                'UPDATE element_holds SET element_id=:q717_0,holder_user_id=:q717_1,arrival_date=:q717_2,departure_date=:q717_3,lead_name=:q717_4,\n                   renewal_required_at=:q717_5,expires_at=:q717_6,updated_at=:q717_7\n                   WHERE id=:q717_8 AND company_id=:q717_9 AND session_token=:q717_10',
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
            audit(database, context, company_id, 'ELEMENT_HOLD_UPDATED', 'element_hold', hold_id, before, after, connection=c)
        return JSONResponse({'ok': True, 'hold_id': hold_id, 'item': after})
