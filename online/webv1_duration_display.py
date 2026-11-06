from __future__ import annotations
from .database import iso_now

import json
from datetime import date, timedelta

from fastapi.responses import Response

from .app import COOKIE_NAME
from .setup015_core import rows


def _parse_day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _fmt(value) -> str:
    day = _parse_day(value)
    return day.strftime('%d/%m/%Y') if day else str(value or '—')


def visible_end_date(value, pricing_method):
    end = _parse_day(value)
    if end and str(pricing_method or '').strip().lower() == 'per day':
        return (end-timedelta(days=1)).isoformat()
    return str(value or '')


def _duration(start_value, end_value, pricing_method: str) -> tuple[str, str, int]:
    """Return semantic start/end display plus the user's day/night duration.

    Storage is end-exclusive for both bases. For Per Night the stored end is the
    departure morning and is therefore displayed as-is. For Per Day the user's
    last booked day is one day before the stored exclusive end.
    """
    start = _parse_day(start_value)
    end = _parse_day(end_value)
    if not start or not end or end <= start:
        return _fmt(start_value), _fmt(end_value), 0
    units = (end - start).days
    visible_end = _parse_day(visible_end_date(end_value,pricing_method))
    return start.strftime('%d/%m/%Y'), visible_end.strftime('%d/%m/%Y'), units


def _label(start_value, end_value, pricing_method: str) -> str:
    start, end, units = _duration(start_value, end_value, pricing_method)
    noun = 'day' if str(pricing_method or '').strip().lower() == 'per day' else 'night'
    if units != 1:
        noun += 's'
    return f'Start date {start} · End date {end} · {units} {noun}'


def _progress_label(start_value, end_value, pricing_method: str) -> str:
    _, _, units = _duration(start_value, end_value, pricing_method)
    noun = 'day' if str(pricing_method or '').strip().lower() == 'per day' else 'night'
    if units != 1:
        noun += 's'
    return f'{units} {noun}'


def install_duration_display(app) -> None:
    """Display duration anywhere current booking/enquiry date pairs are shown.

    This is presentation-only. Database dates and availability calculations stay
    end-exclusive ISO values.
    """
    database = app.state.database

    @app.middleware('http')
    async def duration_display(request, call_next):
        response = await call_next(request)
        if request.url.path != '/availability/calendar-v2' or request.query_params.get('edit_reservation'):
            return response
        if response.status_code >= 400 or 'text/html' not in response.headers.get('content-type', ''):
            return response

        token = request.cookies.get(COOKIE_NAME, '')
        context = database.session_context(token) if token else None
        company_id = None
        if context:
            company_id = context['acting_company_id'] if context['role'] == 'supervisor' else context['company_id']
        if not company_id:
            return response
        company_id = int(company_id)

        body = b''
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else str(chunk).encode('utf-8')
        text = body.decode('utf-8')
        path = request.url.path

        # Availability Calendar / Booking in progress. Dates are already visible
        # on the calendar, so show only the useful stay length here.
        if path == '/availability/calendar-v2':
            hold_map = {}
            for r in rows(database, "SELECT h.id,h.arrival_date,h.departure_date,e.pricing_method\n                                      FROM element_holds h\n                                      JOIN setup_elements e ON e.id=h.element_id AND e.company_id=h.company_id\n                                      WHERE h.company_id=:q645_0 AND h.session_token=:q645_1 AND h.expires_at>:utc_now",
                          (company_id, token, iso_now())):
                hold_map[str(int(r['id']))] = _progress_label(r['arrival_date'], r['departure_date'], r['pricing_method'])
            script = f'''<style id="duration-display-style">.duration-summary{{display:block;color:#4f5d6b;font-size:11px;line-height:1.25;margin-top:3px}}</style>
<script id="duration-display-script">(()=>{{
 const labels={json.dumps(hold_map)};
 document.querySelectorAll('.progress-row').forEach(row=>{{
   const remove=row.querySelector('.progress-remove[data-hold]'); if(!remove)return;
   const label=labels[String(remove.dataset.hold)]; if(!label)return;
   const name=row.querySelector('.progress-name'); if(!name||name.querySelector('.duration-summary'))return;
   const s=document.createElement('small'); s.className='duration-summary'; s.textContent=label; name.appendChild(s);
 }});
}})();</script>'''
            text = text.replace('</body>', script + '</body>', 1)

        headers = {k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'content-type'}}
        return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
