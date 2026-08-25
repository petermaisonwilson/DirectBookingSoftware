from __future__ import annotations

from datetime import date
from urllib.parse import quote_plus

from fastapi import Request
from fastapi.responses import RedirectResponse

from .app import form_data
from .setup015_core import audit, context_for, require_csrf, working_company


def register_flexible_season_routes(app) -> None:
    database = app.state.database

    @app.post('/setup/maintenance/seasons/save')
    async def season_save_flexible(request: Request):
        context = context_for(database, request)
        cid = working_company(context)
        data = await form_data(request)
        require_csrf(context, data)
        try:
            sid = int(data.get('id', ''))
            start = date.fromisoformat(data.get('start_date', ''))
            end = date.fromisoformat(data.get('end_date', ''))
        except ValueError:
            return RedirectResponse(
                '/setup/pricing?message=' + quote_plus('Enter valid Season dates.'), 303
            )
        name = data.get('name', '').strip()
        with database.connect() as c:
            before = c.execute(
                'SELECT * FROM setup_seasons WHERE company_id=? AND id=?', (cid, sid)
            ).fetchone()
            if before is None:
                return RedirectResponse(
                    '/setup/pricing?message=' + quote_plus('Season was not found.'), 303
                )
            year = int(before['year'])
            if not name or end < start:
                return RedirectResponse(
                    f'/setup/pricing?year={year}&message=' + quote_plus(
                        'Enter a name and an end date on or after the start date.'
                    ),
                    303,
                )
            if start.year != year or end.year != year:
                return RedirectResponse(
                    f'/setup/pricing?year={year}&message=' + quote_plus(
                        f'Season dates for {year} must stay within {year}.'
                    ),
                    303,
                )
            c.execute(
                '''UPDATE setup_seasons
                   SET name=?,start_date=?,end_date=?
                   WHERE company_id=? AND id=?''',
                (name, start.isoformat(), end.isoformat(), cid, sid),
            )
        # Existing Enquiries and Bookings retain their frozen price snapshots.
        # Keeping the same Season ID means any extension automatically inherits
        # that Season's existing Element prices for the newly-covered dates.
        audit(
            database,
            context,
            cid,
            'SEASON_SAVED',
            'season',
            sid,
            dict(before),
            {
                'year': year,
                'name': name,
                'start_date': start.isoformat(),
                'end_date': end.isoformat(),
            },
        )
        return RedirectResponse(f'/setup/pricing?year={year}', 303)
