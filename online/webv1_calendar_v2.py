from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import quote_plus

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from .app import COOKIE_NAME, esc, layout
from .setup015_core import context_for, rows, working_company
from .webv1_availability import operating_window
from .webv1_status_availability import available_elements
from .webv1_booking_status import default_status

CALENDAR_DAYS = 28
MAX_CALENDAR_DAYS = 366


def _fmt(value) -> str:
    try:
        return date.fromisoformat(str(value)).strftime('%d/%m/%Y')
    except (TypeError, ValueError):
        return str(value or '')


def _day(value: str, fallback: date) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return fallback


def _overlap(start_value, end_value, day: date) -> bool:
    try:
        start = date.fromisoformat(str(start_value))
        end = date.fromisoformat(str(end_value))
    except ValueError:
        return False
    return start < day + timedelta(days=1) and end > day


def _cols(start_value, end_value, visible_start: date, visible_end: date):
    try:
        start = max(date.fromisoformat(str(start_value)), visible_start)
        end = min(date.fromisoformat(str(end_value)), visible_end)
    except ValueError:
        return None
    if end <= start:
        return None
    return (start - visible_start).days + 2, (end - visible_start).days + 2


def _session_context(database, request: Request):
    context = database.session_context(request.cookies.get(COOKIE_NAME))
    if context is None:
        raise HTTPException(status_code=401, detail='Login required')
    cid = context['acting_company_id'] if context['role'] == 'supervisor' else context['company_id']
    if not cid:
        raise HTTPException(status_code=403, detail='Select a Client first')
    return context, int(cid)


def _records(database, cid: int, element_id: int, start: date, end: date):
    with database.connect() as c:
        bookings = c.execute('''
          SELECT be.arrival_date,be.departure_date,b.id AS booking_id,b.reference,b.status,b.workflow_status_id,
                 cr.first_name,cr.last_name,
                 s.name AS workflow_name,s.short_name,s.colour,s.blocks_availability,s.internal_state
          FROM booking_elements be
          JOIN bookings b ON b.id=be.booking_id AND b.company_id=be.company_id
          LEFT JOIN customer_records cr ON cr.id=b.customer_id AND cr.company_id=b.company_id
          LEFT JOIN booking_status_definitions s ON s.id=b.workflow_status_id AND s.company_id=b.company_id
          WHERE be.company_id=? AND be.element_id=?
            AND date(be.arrival_date)<date(?) AND date(be.departure_date)>date(?)
          ORDER BY be.arrival_date
        ''', (cid, element_id, end.isoformat(), start.isoformat())).fetchall()
        enquiries = c.execute('''
          SELECT e.id AS enquiry_id,e.arrival_date,e.departure_date,e.availability_expires_at,
                 cr.first_name,cr.last_name,s.name AS workflow_name,s.short_name,s.colour,s.blocks_availability,s.internal_state
          FROM enquiries e
          JOIN enquiry_requests er ON er.enquiry_id=e.id AND er.company_id=e.company_id
          LEFT JOIN customer_records cr ON cr.id=e.customer_id AND cr.company_id=e.company_id
          JOIN booking_status_definitions s ON s.id=e.workflow_status_id AND s.company_id=e.company_id
          WHERE e.company_id=? AND er.element_id=? AND e.status NOT IN ('closed','converted')
            AND s.blocks_availability=1
            AND (e.availability_expires_at IS NULL OR e.availability_expires_at>datetime('now'))
            AND date(e.arrival_date)<date(?) AND date(e.departure_date)>date(?)
          ORDER BY e.arrival_date
        ''', (cid, element_id, end.isoformat(), start.isoformat())).fetchall()
        closures = c.execute('''SELECT * FROM element_closures WHERE company_id=? AND element_id=?
                                AND date(start_date)<date(?) AND date(end_date)>date(?) ORDER BY start_date''',
                             (cid, element_id, end.isoformat(), start.isoformat())).fetchall()
        holds = c.execute('''SELECT * FROM element_holds WHERE company_id=? AND element_id=? AND expires_at>datetime('now')
                             AND date(arrival_date)<date(?) AND date(departure_date)>date(?) ORDER BY arrival_date''',
                          (cid, element_id, end.isoformat(), start.isoformat())).fetchall()
    return bookings, enquiries, closures, holds


def _blocking_booking(row) -> bool:
    if row['blocks_availability'] is not None:
        return bool(int(row['blocks_availability']))
    return str(row['status']) != 'cancelled'


def _bar(label: str, colour: str, columns, *, css='status', href='', title='') -> str:
    if not columns:
        return ''
    a, b = columns
    text = esc(label)
    title_attr = f' title="{esc(title)}"' if title else ''
    inner = f'<a href="{esc(href)}"{title_attr}>{text}</a>' if href else f'<span{title_attr}>{text}</span>'
    return f'<div class="cal-bar {css}" style="grid-column:{a}/{b};grid-row:1;background:{esc(colour)}">{inner}</div>'


def register_calendar_v2_routes(app) -> None:
    database = app.state.database

    @app.get('/availability/calendar-v2', response_class=HTMLResponse)
    def calendar(request: Request, element_type: str = '', start: str = '', arrival: str = '', departure: str = ''):
        context, cid = _session_context(database, request)
        staff = str(context['role']) in {'operator', 'supervisor'}
        types = [str(r['name']) for r in rows(database, 'SELECT name FROM setup_element_types WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (cid,))]
        selected_type = element_type if element_type in types else (types[0] if types else '')

        arrival_day = None
        departure_day = None
        try:
            if arrival:
                arrival_day = date.fromisoformat(arrival)
            if departure:
                departure_day = date.fromisoformat(departure)
        except ValueError:
            arrival_day = None
            departure_day = None

        visible_start = _day(start, arrival_day or date.today())
        display_days = CALENDAR_DAYS
        if departure_day and departure_day > visible_start:
            display_days = max(display_days, (departure_day - visible_start).days + 3)
        display_days = min(display_days, MAX_CALENDAR_DAYS)
        visible_end = visible_start + timedelta(days=display_days)

        token = request.cookies.get(COOKIE_NAME, '')
        exact = []
        exact_message = ''
        if selected_type and arrival and departure:
            try:
                a = date.fromisoformat(arrival)
                d = date.fromisoformat(departure)
                if d <= a:
                    exact_message = 'Departure must be after arrival.'
                else:
                    exact = available_elements(database, cid, selected_type, arrival, departure, session_token=token)
                    if not exact:
                        exact_message = f'No {selected_type} Elements are available for {_fmt(arrival)} to {_fmt(departure)}.'
            except ValueError:
                exact_message = 'Enter valid arrival and departure dates.'

        options = ''.join(f'<option value="{esc(t)}" {"selected" if t == selected_type else ""}>{esc(t)}</option>' for t in types)
        preserve = f'element_type={quote_plus(selected_type)}&arrival={quote_plus(arrival)}&departure={quote_plus(departure)}'
        body = f'''<h1>Availability Calendar</h1>
        <div class="card"><form id="availability-form" method="get" action="/availability/calendar-v2"><div class="grid">
          <div><label>Element Type</label><select id="element-type" name="element_type">{options}</select></div>
          <div><label>Arrival</label><input id="arrival-date" type="date" name="arrival" value="{esc(arrival)}"></div>
          <div><label>Departure</label><input id="departure-date" type="date" name="departure" value="{esc(departure)}"></div>
          <div><label>Calendar starts</label><input id="calendar-start" type="date" name="start" value="{visible_start.isoformat()}"></div>
        </div><p>
        <a class="button secondary" href="/availability/calendar-v2?{preserve}&start={(visible_start - timedelta(days=14)).isoformat()}">← Previous 14 days</a>
        <a class="button secondary" href="/availability/calendar-v2?{preserve}&start={(visible_start + timedelta(days=14)).isoformat()}">Next 14 days →</a>
        {'<a class="button secondary" href="/setup/booking-statuses">Booking Statuses</a>' if staff else ''}</p></form></div>'''

        if staff:
            status_rows = rows(database, 'SELECT * FROM booking_status_definitions WHERE company_id=? AND active=1 ORDER BY display_order,name', (cid,))
            legend = ''.join(f'<span class="legend" style="background:{esc(r["colour"])}">{esc(r["name"])}</span>' for r in status_rows)
            body += f'<div class="card"><div class="legend-row"><strong>Key:</strong>{legend}<span class="legend closed">Closed</span><span>Available = clear</span></div></div>'
        else:
            body += '<div class="card"><div class="legend-row"><strong>Key:</strong><span class="legend booked">Booked</span><span class="legend held">Held</span><span class="legend closed">Closed</span><span>Available = clear</span></div></div>'

        dates = [visible_start + timedelta(days=i) for i in range(display_days)]
        header = '<div class="cal-row cal-head"><div class="cal-name" style="grid-column:1;grid-row:1">Element</div>'
        for i, d in enumerate(dates):
            selected_class = ' selected-date' if arrival_day and departure_day and arrival_day <= d < departure_day else (' selected-start' if arrival_day == d else '')
            header += f'<div class="cal-date{selected_class}" data-date="{d.isoformat()}" style="grid-column:{i + 2};grid-row:1"><strong>{d.strftime("%d")}</strong><small>{d.strftime("%b")}</small></div>'
        header += '</div>'

        rows_html = ''
        elements = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1 AND element_type=? ORDER BY name COLLATE NOCASE', (cid, selected_type)) if selected_type else []
        held_status = default_status(database, cid, 'HELD')
        held_colour = str(held_status['colour']) if held_status else '#FFE39A'
        held_name = str(held_status['name']) if held_status else 'Enquiry / Held'
        for element in elements:
            eid = int(element['id'])
            bookings, enquiries, closures, holds = _records(database, cid, eid, visible_start, visible_end)
            windows = {d.year: operating_window(database, cid, d.year) for d in dates}
            cells = ''
            for i, d in enumerate(dates):
                window = windows[d.year]
                state = 'available'
                if window is None or d < window[0] or d + timedelta(days=1) > window[1]:
                    state = 'out'
                elif any(_overlap(r['arrival_date'], r['departure_date'], d) and _blocking_booking(r) for r in bookings):
                    state = 'booked'
                elif any(_overlap(r['arrival_date'], r['departure_date'], d) for r in enquiries):
                    state = 'held'
                elif any(_overlap(r['start_date'], r['end_date'], d) for r in closures):
                    state = 'closed'
                elif any(_overlap(r['arrival_date'], r['departure_date'], d) for r in holds):
                    state = 'held'
                col = i + 2
                selected_class = ' selected-date' if arrival_day and departure_day and arrival_day <= d < departure_day else (' selected-start' if arrival_day == d else '')
                if state == 'available':
                    cells += f'<button type="button" class="cal-cell available date-pick{selected_class}" style="grid-column:{col};grid-row:1" data-date="{d.isoformat()}" title="Available {d.strftime("%d/%m/%Y")}"></button>'
                else:
                    cells += f'<span class="cal-cell {state}{selected_class}" style="grid-column:{col};grid-row:1" data-date="{d.isoformat()}"></span>'

            bars = ''
            for b in bookings:
                if not staff and not _blocking_booking(b):
                    continue
                cols = _cols(b['arrival_date'], b['departure_date'], visible_start, visible_end)
                if staff:
                    customer = (f"{b['first_name']} {b['last_name']}").strip() or 'Customer'
                    status_name = str(b['workflow_name'] or 'Booked')
                    colour = str(b['colour'] or '#F3C5C9')
                    bars += _bar(f'{customer} · {b["reference"]} · {status_name}', colour, cols, href=f'/operations/bookings/{int(b["booking_id"])}', title=f'{status_name}: {_fmt(b["arrival_date"])} to {_fmt(b["departure_date"])}')
                else:
                    bars += _bar('Booked', '#F3C5C9', cols, title='Unavailable')
            for e in enquiries:
                cols = _cols(e['arrival_date'], e['departure_date'], visible_start, visible_end)
                if staff:
                    customer = (f"{e['first_name']} {e['last_name']}").strip() or 'Customer'
                    colour = str(e['colour'] or held_colour)
                    label = f'{customer} · Enquiry #{int(e["enquiry_id"])} · {e["workflow_name"] or held_name}'
                    bars += _bar(label, colour, cols, href=f'/operations/enquiries/{int(e["enquiry_id"])}', title=f'Expires {e["availability_expires_at"] or "when released"}')
                else:
                    bars += _bar('Held', held_colour, cols, title='Temporarily unavailable')
            for c in closures:
                label = str(c['reason'] or 'Closed') if staff else 'Closed'
                bars += _bar(label, '#CFD4DA', _cols(c['start_date'], c['end_date'], visible_start, visible_end), css='closed')
            for h in holds:
                own = str(h['session_token']) == token
                label = 'Held by you' if own else (held_name if staff else 'Held')
                bars += _bar(label, held_colour, _cols(h['arrival_date'], h['departure_date'], visible_start, visible_end), css='held')
            rows_html += f'<div class="cal-row"><div class="cal-name" style="grid-column:1;grid-row:1"><strong>{esc(element["name"])}</strong></div>{cells}{bars}</div>'

        if not rows_html:
            rows_html = '<div class="card"><p>No active Elements exist for this Element Type.</p></div>'
        body += f'<div class="card"><div id="calendar-scroll" class="calendar-scroll"><div class="calendar-grid" style="--days:{display_days}">{header}{rows_html}</div></div><p class="muted">Choose dates in the boxes above, or click a clear calendar day for Arrival and a second later clear day for Departure.</p></div>'

        if arrival and departure:
            body += '<div class="card"><h2>Available for your selected dates</h2>'
            if exact_message:
                body += f'<div class="error"><strong>{esc(exact_message)}</strong><br>Try different dates or another Element Type.</div>'
            else:
                body += f'<p><strong>{esc(selected_type)}</strong> — {_fmt(arrival)} to {_fmt(departure)}</p>'
                for item in exact:
                    chips = ''.join(f'<span class="addon-chip {"yes" if a["available"] else "no"}">{"✓" if a["available"] else "✕"} {esc(a["name"])}</span>' for a in item['addons'])
                    body += f'<div class="availability-result"><div><h3>{esc(item["name"])}</h3><div class="addon-list">{chips}</div></div><button type="button" class="hold-button" data-element="{int(item["id"])}" data-name="{esc(item["name"])}">Select &amp; hold</button></div>'
            body += '</div>'

        body += f'''<div class="card"><h2>Held Elements</h2><div id="hold-list" class="muted">No Elements currently held.</div></div>
        <div id="hold-modal" class="hold-modal" hidden><div class="hold-dialog"><h2>Still want to hold these Elements?</h2><p id="hold-names"></p><p>If Yes is not clicked before the hold expires, everything is released automatically.</p><p><button id="hold-yes" type="button">Yes — keep holding</button> <button id="hold-release" type="button" class="secondary">Release now</button></p></div></div>
        <style>
        .calendar-scroll{{overflow:auto;max-height:520px;border:1px solid #d6dde5;border-radius:8px;position:relative;scrollbar-gutter:stable both-edges}}
        .calendar-grid{{min-width:calc(170px + (var(--days) * 48px));background:white}}
        .cal-row{{display:grid;grid-template-columns:170px repeat(var(--days),48px);position:relative;min-height:48px;border-bottom:1px solid #e1e6eb}}
        .cal-head{{min-height:52px;background:#f4f6f8;position:sticky;top:0;z-index:10}}
        .cal-name{{padding:10px 8px;position:sticky;left:0;z-index:6;background:white;border-right:1px solid #d6dde5}}
        .cal-head .cal-name{{background:#f4f6f8;z-index:12}}
        .cal-date{{padding:6px 2px;text-align:center;border-right:1px solid #e5e9ee;font-size:12px;background:#f4f6f8}}
        .cal-date small{{display:block;color:#66717f}}
        .cal-cell{{min-height:48px;border:0;border-right:1px solid #eef1f4;display:block;z-index:1;padding:0;margin:0;border-radius:0;cursor:default}}
        button.cal-cell.available{{background:white;cursor:pointer}}
        button.cal-cell.available:hover{{outline:2px solid #5f7893;outline-offset:-2px}}
        .cal-cell.out{{background:#eef1f4}} .cal-cell.closed{{background:#e4e7eb}} .cal-cell.held{{background:#fff0c2}} .cal-cell.booked{{background:#f8d7da}}
        .cal-cell.selected-date,.cal-date.selected-date{{box-shadow:inset 0 0 0 2px #6d8196}}
        .cal-cell.selected-start,.cal-date.selected-start{{box-shadow:inset 0 0 0 3px #405b75}}
        .cal-bar{{z-index:4;align-self:center;height:30px;margin:0 2px;border-radius:5px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;display:flex;align-items:center}}
        .cal-bar a,.cal-bar span{{display:block;padding:6px 8px;color:#26313d;text-decoration:none;width:100%;overflow:hidden;text-overflow:ellipsis}}
        .legend-row{{display:flex;gap:10px;flex-wrap:wrap;align-items:center}} .legend{{padding:4px 9px;border-radius:4px;border:1px solid rgba(0,0,0,.08)}} .legend.closed{{background:#cfd4da}} .legend.booked{{background:#f3c5c9}} .legend.held{{background:#ffe39a}}
        .availability-result{{display:flex;justify-content:space-between;gap:16px;align-items:center;border-top:1px solid #e1e6eb;padding:14px 0}} .addon-list{{display:flex;gap:7px;flex-wrap:wrap}} .addon-chip{{padding:5px 8px;border-radius:5px;background:#edf2f7}} .addon-chip.no{{text-decoration:none;color:#8a2731}}
        .hold-modal{{position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:100;display:flex;align-items:center;justify-content:center}} .hold-modal[hidden]{{display:none}} .hold-dialog{{background:white;padding:24px;border-radius:10px;max-width:520px;width:90%}}
        </style>
        <script>
        const csrf={json.dumps(str(context['csrf_token']))};
        const arr={json.dumps(arrival)};
        const dep={json.dumps(departure)};
        const form=document.getElementById('availability-form');
        const elementType=document.getElementById('element-type');
        const arrivalInput=document.getElementById('arrival-date');
        const departureInput=document.getElementById('departure-date');
        const startInput=document.getElementById('calendar-start');
        const scrollBox=document.getElementById('calendar-scroll');

        function goToSelection(){{
          const a=arrivalInput.value, d=departureInput.value;
          if(!a||!d)return;
          if(d<=a){{departureInput.setCustomValidity('Departure must be after arrival.');departureInput.reportValidity();return;}}
          departureInput.setCustomValidity('');
          const q=new URLSearchParams();q.set('element_type',elementType.value);q.set('arrival',a);q.set('departure',d);q.set('start',a);
          window.location='/availability/calendar-v2?'+q.toString();
        }}
        elementType.addEventListener('change',()=>{{const q=new URLSearchParams();q.set('element_type',elementType.value);if(arrivalInput.value)q.set('arrival',arrivalInput.value);if(departureInput.value)q.set('departure',departureInput.value);q.set('start',arrivalInput.value||startInput.value);window.location='/availability/calendar-v2?'+q.toString();}});
        arrivalInput.addEventListener('change',()=>{{departureInput.setCustomValidity('');if(arrivalInput.value&&departureInput.value)goToSelection();}});
        departureInput.addEventListener('change',goToSelection);
        startInput.addEventListener('change',()=>form.submit());

        document.querySelectorAll('.date-pick').forEach(cell=>cell.addEventListener('click',()=>{{
          const chosen=cell.dataset.date;
          if(!arrivalInput.value || departureInput.value){{arrivalInput.value=chosen;departureInput.value='';document.querySelectorAll('.selected-date,.selected-start').forEach(x=>x.classList.remove('selected-date','selected-start'));document.querySelectorAll('[data-date="'+chosen+'"]').forEach(x=>x.classList.add('selected-start'));return;}}
          if(chosen<=arrivalInput.value){{arrivalInput.value=chosen;departureInput.value='';document.querySelectorAll('.selected-date,.selected-start').forEach(x=>x.classList.remove('selected-date','selected-start'));document.querySelectorAll('[data-date="'+chosen+'"]').forEach(x=>x.classList.add('selected-start'));return;}}
          departureInput.value=chosen;
          goToSelection();
        }}));

        if(arr && scrollBox){{const target=document.querySelector('.cal-date[data-date="'+arr+'"]');if(target)scrollBox.scrollLeft=Math.max(0,target.offsetLeft-170-96);}}

        async function post(url,data={{}}){{const fd=new FormData();fd.append('csrf',csrf);Object.entries(data).forEach(([k,v])=>fd.append(k,v));return fetch(url,{{method:'POST',body:fd}})}}
        async function refreshHolds(){{const r=await fetch('/availability/holds');if(!r.ok)return;const data=await r.json();const list=document.getElementById('hold-list');if(!data.holds.length){{list.textContent='No Elements currently held.';document.getElementById('hold-modal').hidden=true;return;}} list.innerHTML=data.holds.map(h=>'<strong>'+h.element_name+'</strong> '+h.arrival_date+' → '+h.departure_date).join('<br>'); if(data.holds.some(h=>h.needs_confirmation)){{document.getElementById('hold-names').textContent=data.holds.map(h=>h.element_name).join(', ');document.getElementById('hold-modal').hidden=false;}}}}
        document.querySelectorAll('.hold-button').forEach(btn=>btn.addEventListener('click',async()=>{{if(!arr||!dep){{alert('Choose Arrival and Departure first.');return;}}const r=await post('/availability/hold',{{element_id:btn.dataset.element,arrival_date:arr,departure_date:dep}});const d=await r.json();if(!r.ok)alert(d.error||'Unable to hold that Element');await refreshHolds();}}));
        document.getElementById('hold-yes').addEventListener('click',async()=>{{await post('/availability/holds/renew');document.getElementById('hold-modal').hidden=true;await refreshHolds();}});
        document.getElementById('hold-release').addEventListener('click',async()=>{{await post('/availability/holds/release');document.getElementById('hold-modal').hidden=true;await refreshHolds();}});
        refreshHolds();setInterval(refreshHolds,5000);
        </script>'''
        return HTMLResponse(layout('Availability Calendar', body, context))

    @app.get('/operations/bookings/{booking_id}', response_class=HTMLResponse)
    def booking_detail(request: Request, booking_id: int):
        context = context_for(database, request)
        cid = int(working_company(context))
        with database.connect() as c:
            b = c.execute('''SELECT b.*,cr.first_name,cr.last_name,cr.email,s.name AS workflow_name,s.colour,s.internal_state,s.blocks_availability FROM bookings b LEFT JOIN customer_records cr ON cr.id=b.customer_id AND cr.company_id=b.company_id LEFT JOIN booking_status_definitions s ON s.id=b.workflow_status_id AND s.company_id=b.company_id WHERE b.id=? AND b.company_id=?''', (booking_id, cid)).fetchone()
            elements = c.execute('''SELECT be.*,e.name AS element_name FROM booking_elements be JOIN setup_elements e ON e.id=be.element_id WHERE be.booking_id=? AND be.company_id=? ORDER BY be.arrival_date''', (booking_id, cid)).fetchall()
        if b is None:
            return HTMLResponse(layout('Booking', '<h1>Booking not found</h1>', context), 404)
        active = rows(database, 'SELECT * FROM booking_status_definitions WHERE company_id=? AND active=1 ORDER BY display_order,name', (cid,))
        options = ''.join(f'<option value="{int(s["id"])}" {"selected" if b["workflow_status_id"] == s["id"] else ""}>{esc(s["name"])}</option>' for s in active)
        customer = (f"{b['first_name']} {b['last_name']}").strip() or 'Customer'
        body = f'''<h1>Booking {esc(b['reference'])}</h1><div class="card"><p><strong>{esc(customer)}</strong><br>{esc(b['email'] or '')}</p><p>Status: <span style="padding:5px 9px;border-radius:4px;background:{esc(b['colour'] or '#F3C5C9')}"><strong>{esc(b['workflow_name'] or b['status'])}</strong></span></p><p>Arrival {_fmt(b['arrival_date'])} · Departure {_fmt(b['departure_date'])}</p></div>
        <div class="card"><h2>Change Booking Status</h2><form method="post" action="/operations/bookings/status"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><input type="hidden" name="booking_id" value="{booking_id}"><label>Status</label><select name="status_id">{options}</select><p><button>Change Status</button></p></form></div>
        <div class="card"><h2>Elements</h2><table><thead><tr><th>Element</th><th>Arrival</th><th>Departure</th><th>Total</th></tr></thead><tbody>'''
        for e in elements:
            body += f'<tr><td>{esc(e["element_name"])}</td><td>{_fmt(e["arrival_date"])}</td><td>{_fmt(e["departure_date"])}</td><td>€{float(e["total_amount"]):.2f}</td></tr>'
        body += '</tbody></table><p><a class="button secondary" href="/availability/calendar-v2">Back to Availability Calendar</a></p></div>'
        return HTMLResponse(layout(f'Booking {b["reference"]}', body, context))