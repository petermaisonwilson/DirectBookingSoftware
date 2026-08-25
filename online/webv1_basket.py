from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from .app import COOKIE_NAME, form_data
from .setup015_core import audit, require_csrf
from . import webv1_availability as availability


def register_basket_routes(app) -> None:
    database = app.state.database

    @app.get('/availability/basket')
    def basket_status(request: Request):
        context, company_id = availability._session_company(database, request)
        token = request.cookies.get(COOKIE_NAME, '')
        now = availability._now()
        with database.connect() as c:
            availability._purge_expired_holds(c)
            held = c.execute(
                '''SELECT h.*, e.name AS element_name, e.element_type
                   FROM element_holds h
                   JOIN setup_elements e ON e.id=h.element_id AND e.company_id=h.company_id
                   WHERE h.company_id=? AND h.session_token=?
                   ORDER BY h.created_at, e.name COLLATE NOCASE''',
                (company_id, token),
            ).fetchall()
        items = []
        for row in held:
            items.append({
                'id': int(row['id']),
                'element_id': int(row['element_id']),
                'element_name': str(row['element_name']),
                'element_type': str(row['element_type']),
                'arrival_date': str(row['arrival_date']),
                'departure_date': str(row['departure_date']),
                'renewal_required_at': str(row['renewal_required_at']),
                'expires_at': str(row['expires_at']),
                'needs_confirmation': now >= availability._utc(str(row['renewal_required_at'])),
            })
        return JSONResponse({'items': items, 'count': len(items)})

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
            availability._purge_expired_holds(c)
            row = c.execute(
                '''SELECT h.*,e.name AS element_name,e.element_type
                   FROM element_holds h
                   JOIN setup_elements e ON e.id=h.element_id AND e.company_id=h.company_id
                   WHERE h.id=? AND h.company_id=? AND h.session_token=?''',
                (hold_id, company_id, token),
            ).fetchone()
            if row is None:
                return JSONResponse({'ok': False, 'error': 'That basket item has already expired or been removed.'}, status_code=404)
            before = dict(row)
            c.execute('DELETE FROM element_holds WHERE id=? AND company_id=? AND session_token=?', (hold_id, company_id, token))
        audit(
            database,
            context,
            company_id,
            'ELEMENT_HOLD_REMOVED',
            'element_hold',
            hold_id,
            before,
            {'removed': True},
        )
        return JSONResponse({'ok': True, 'removed': 1, 'hold_id': hold_id})
