from __future__ import annotations

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
    is_day = str(pricing_method or '').strip().lower() == 'per day'
    visible_end = end - timedelta(days=1) if is_day else end
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
            for r in rows(database, '''SELECT h.id,h.arrival_date,h.departure_date,e.pricing_method
                                      FROM element_holds h
                                      JOIN setup_elements e ON e.id=h.element_id AND e.company_id=h.company_id
                                      WHERE h.company_id=? AND h.session_token=? AND h.expires_at>datetime('now')''',
                          (company_id, token)):
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

        elif path.startswith('/operations/bookings/') and path.rstrip('/').split('/')[-1].isdigit():
            booking_id = int(path.rstrip('/').split('/')[-1])
            items = rows(database, '''SELECT be.arrival_date,be.departure_date,se.name AS element_name,se.element_type,
                                             COALESCE(NULLIF(be.pricing_method_snapshot,''),se.pricing_method) AS pricing_method
                                      FROM booking_elements be
                                      JOIN setup_elements se ON se.id=be.element_id AND se.company_id=be.company_id
                                      WHERE be.company_id=? AND be.booking_id=? ORDER BY be.id''', (company_id, booking_id))
            for item in items:
                old = f'''<strong>{item['element_name']}</strong> ({item['element_type']}) — {_fmt(item['arrival_date'])} to {_fmt(item['departure_date'])}'''
                new = f'''<strong>{item['element_name']}</strong> ({item['element_type']}) — {_label(item['arrival_date'], item['departure_date'], item['pricing_method'])}'''
                text = text.replace(old, new, 1)

        elif path.startswith('/operations/enquiries/') and path.rstrip('/').split('/')[-1].isdigit():
            enquiry_id = int(path.rstrip('/').split('/')[-1])
            item = rows(database, '''SELECT e.arrival_date,e.departure_date,se.pricing_method
                                     FROM enquiries e
                                     LEFT JOIN enquiry_requests er ON er.enquiry_id=e.id AND er.company_id=e.company_id
                                     LEFT JOIN setup_elements se ON se.id=er.element_id AND se.company_id=er.company_id
                                     WHERE e.company_id=? AND e.id=? LIMIT 1''', (company_id, enquiry_id))
            if item and item[0]['arrival_date'] and item[0]['departure_date'] and item[0]['pricing_method']:
                r = item[0]
                marker = f'''<strong>Departure:</strong> {_fmt(r['departure_date'])}'''
                replacement = marker + f'''<br><strong>Duration:</strong> {_label(r['arrival_date'], r['departure_date'], r['pricing_method'])}'''
                text = text.replace(marker, replacement, 1)

        headers = {k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'content-type'}}
        return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
