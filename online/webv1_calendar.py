from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import quote_plus

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from .app import COOKIE_NAME, esc, layout
from .setup015_core import context_for, rows, working_company
from .webv1_availability import available_elements, operating_window

CALENDAR_DAYS = 28


def _day(value: str, fallback: date) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return fallback


def _fmt(value: str | date) -> str:
    try:
        d = value if isinstance(value, date) else date.fromisoformat(str(value))
        return d.strftime('%d/%m/%Y')
    except (TypeError, ValueError):
        return str(value or '')


def _overlaps(start_a: str, end_a: str, start_b: date, end_b: date) -> bool:
    try:
        a = date.fromisoformat(str(start_a)); b = date.fromisoformat(str(end_a))
    except ValueError:
        return False
    return a < end_b and b > start_b


def _clip_columns(start_value: str, end_value: str, visible_start: date, visible_end: date) -> tuple[int, int] | None:
    try:
        start = max(date.fromisoformat(str(start_value)), visible_start)
        end = min(date.fromisoformat(str(end_value)), visible_end)
    except ValueError:
        return None
    if end <= start:
        return None
    # +2 because grid column 1 is the sticky Element-name column.
    return (start - visible_start).days + 2, (end - visible_start).days + 2


def _calendar_records(database, company_id: int, element_id: int, visible_start: date, visible_end: date, session_token: str):
    with database.connect() as c:
        bookings = c.execute('''
            SELECT be.arrival_date,be.departure_date,b.id AS booking_id,b.reference,b.status,
                   c.first_name,c.last_name
            FROM booking_elements be
            JOIN bookings b ON b.id=be.booking_id AND b.company_id=be.company_id
            LEFT JOIN customer_records c ON c.id=b.customer_id AND c.company_id=b.company_id
            WHERE be.company_id=? AND be.element_id=? AND b.status<>'cancelled'
              AND date(be.arrival_date)<date(?) AND date(be.departure_date)>date(?)
            ORDER BY be.arrival_date
        ''', (company_id, element_id, visible_end.isoformat(), visible_start.isoformat())).fetchall()
        closures = c.execute('''
            SELECT * FROM element_closures
            WHERE company_id=? AND element_id=?
              AND date(start_date)<date(?) AND date(end_date)>date(?)
            ORDER BY start_date
        ''', (company_id, element_id, visible_end.isoformat(), visible_start.isoformat())).fetchall()
        holds = c.execute('''
            SELECT * FROM element_holds
            WHERE company_id=? AND element_id=? AND expires_at>datetime('now')
              AND date(arrival_date)<date(?) AND date(departure_date)>date(?)
            ORDER BY arrival_date
        ''', (company_id, element_id, visible_end.isoformat(), visible_start.isoformat())).fetchall()
    return bookings, closures, holds


def _cell_state(day: date, bookings, closures, holds, session_token: str, window) -> str:
    next_day = day + timedelta(days=1)
    if window is None or day < window[0] or next_day > window[1]:
        return 'out'
    for row in bookings:
        if _overlaps(row['arrival_date'], row['departure_date'], day, next_day):
            return 'booked'
    for row in closures:
        if _overlaps(row['start_date'], row['end_date'], day, next_day):
            return 'closed'
    for row in holds:
        if _overlaps(row['arrival_date'], row['departure_date'], day, next_day):
            return 'held-own' if str(row['session_token']) == session_token else 'held'
    return 'available'


def _bar(label: str, css_class: str, columns: tuple[int, int] | None, *, href: str = '', title: str = '') -> str:
    if columns is None:
        return ''
    start_col, end_col = columns
    content = esc(label)
    title_attr = f' title="{esc(title)}"' if title else ''
    inner = f'<a href="{esc(href)}"{title_attr}>{content}</a>' if href else f'<span{title_attr}>{content}</span>'
    return f'<div class="cal-bar {css_class}" style="grid-column:{start_col}/{end_col}">{inner}</div>'


def register_calendar_routes(app) -> None:
    database = app.state.database

    @app.get('/availability/calendar', response_class=HTMLResponse)
    def availability_calendar(request: Request, element_type: str = '', start: str = '', arrival: str = '', departure: str = ''):
        context = context_for(database, request)
        cid = working_company(context)
        if not cid:
            raise HTTPException(status_code=403, detail='Select a Client first')
        cid = int(cid)
        staff = str(context['role']) in {'operator', 'supervisor'}
        type_rows = rows(database, 'SELECT name FROM setup_element_types WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (cid,))
        types = [str(r['name']) for r in type_rows]
        selected_type = element_type if element_type in types else (types[0] if types else '')
        today = date.today()
        visible_start = _day(start, _day(arrival, today))
        visible_end = visible_start + timedelta(days=CALENDAR_DAYS)
        session_token = request.cookies.get(COOKIE_NAME, '')

        selected_arrival = arrival.strip()
        selected_departure = departure.strip()
        exact_results = []
        exact_message = ''
        if selected_type and selected_arrival and selected_departure:
            try:
                a = date.fromisoformat(selected_arrival); d = date.fromisoformat(selected_departure)
                if d <= a:
                    exact_message = 'Departure must be after arrival.'
                else:
                    exact_results = available_elements(database, cid, selected_type, selected_arrival, selected_departure, session_token=session_token)
                    if not exact_results:
                        exact_message = f'No {selected_type} Elements are available for {_fmt(a)} to {_fmt(d)}.'
            except ValueError:
                exact_message = 'Enter valid arrival and departure dates.'

        type_options = ''.join(f'<option value="{esc(t)}" {"selected" if t == selected_type else ""}>{esc(t)}</option>' for t in types)
        prev_start = (visible_start - timedelta(days=14)).isoformat()
        next_start = (visible_start + timedelta(days=14)).isoformat()
        preserve = f'element_type={quote_plus(selected_type)}&arrival={quote_plus(selected_arrival)}&departure={quote_plus(selected_departure)}'

        body = f'''<h1>Availability Calendar</h1>
        <div class="card"><form method="get" action="/availability/calendar">
          <div class="grid">
            <div><label>Element Type</label><select name="element_type" id="calendar-type">{type_options}</select></div>
            <div><label>Arrival</label><input type="date" name="arrival" id="calendar-arrival" value="{esc(selected_arrival)}"></div>
            <div><label>Departure</label><input type="date" name="departure" id="calendar-departure" value="{esc(selected_departure)}"></div>
            <div><label>Calendar starts</label><input type="date" name="start" value="{visible_start.isoformat()}"></div>
          </div>
          <p><button>Check availability</button> <a class="button secondary" href="/availability/calendar?{preserve}&start={prev_start}">← Previous 14 days</a> <a class="button secondary" href="/availability/calendar?{preserve}&start={next_start}">Next 14 days →</a></p>
        </form></div>'''

        body += '''<div class="card"><div style="display:flex;gap:18px;flex-wrap:wrap;align-items:center"><strong>Key:</strong><span class="legend booked">Booked</span><span class="legend closed">Closed</span><span class="legend held">Held</span><span>Available = clear</span></div></div>'''

        if selected_type:
            elements = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1 AND element_type=? ORDER BY name COLLATE NOCASE', (cid, selected_type))
            dates = [visible_start + timedelta(days=i) for i in range(CALENDAR_DAYS)]
            header = '<div class="cal-row cal-head"><div class="cal-name">Element</div>' + ''.join(f'<div class="cal-date"><strong>{d.strftime("%d")}</strong><small>{d.strftime("%b")}</small></div>' for d in dates) + '</div>'
            rows_html = ''
            for element in elements:
                eid = int(element['id'])
                bookings, closures, holds = _calendar_records(database, cid, eid, visible_start, visible_end, session_token)
                # Use the same configured operating-season bounds as the availability engine.
                windows = {d.year: operating_window(database, cid, d.year) for d in dates}
                cells = ''
                for d in dates:
                    state = _cell_state(d, bookings, closures, holds, session_token, windows[d.year])
                    if state == 'available':
                        href = f'/availability/calendar?element_type={quote_plus(selected_type)}&start={visible_start.isoformat()}&arrival={d.isoformat()}&departure={(d+timedelta(days=1)).isoformat()}'
                        cells += f'<a class="cal-cell available" href="{href}" title="Available {d.strftime("%d/%m/%Y")}"></a>'
                    else:
                        cells += f'<span class="cal-cell {state}" title="{esc(state.replace("-", " ").title())}"></span>'
                bars = ''
                for b in bookings:
                    cols = _clip_columns(b['arrival_date'], b['departure_date'], visible_start, visible_end)
                    if staff:
                        customer = (f"{b['first_name']} {b['last_name']}").strip() or 'Customer'
                        label = f"{customer} · {b['reference']}"
                        bars += _bar(label, 'booked', cols, href=f'/operations/bookings/{int(b["booking_id"])}', title=f'{label} — {_fmt(b["arrival_date"])} to {_fmt(b["departure_date"])}')
                    else:
                        bars += _bar('Booked', 'booked', cols, title='Unavailable')
                for c in closures:
                    bars += _bar(str(c['reason'] or 'Closed'), 'closed', _clip_columns(c['start_date'], c['end_date'], visible_start, visible_end), title=f'Closed {_fmt(c["start_date"])} to {_fmt(c["end_date"])}')
                for h in holds:
                    own = str(h['session_token']) == session_token
                    bars += _bar('Held by you' if own else 'Held', 'held-own' if own else 'held', _clip_columns(h['arrival_date'], h['departure_date'], visible_start, visible_end), title='Temporary hold')
                rows_html += f'<div class="cal-row"><div class="cal-name"><strong>{esc(element["name"])}</strong></div>{cells}{bars}</div>'
            if not elements:
                rows_html = '<div class="card"><p>No active Elements exist for this Element Type.</p></div>'
            body += f'''<div class="card"><div class="calendar-scroll"><div class="calendar-grid" style="--days:{CALENDAR_DAYS}">{header}{rows_html}</div></div><p class="muted">Click any clear day to use it as a one-night starting point, then adjust Arrival/Departure above if required.</p></div>'''

        if selected_arrival and selected_departure:
            body += '<div class="card"><h2>Available for your selected dates</h2>'
            if exact_message:
                body += f'<div class="error"><strong>{esc(exact_message)}</strong><br>Use the calendar above to try nearby dates or choose another Element Type.</div>'
            else:
                body += f'<p><strong>{esc(selected_type)}</strong> — {_fmt(selected_arrival)} to {_fmt(selected_departure)}</p>'
                for item in exact_results:
                    addon_html = ''.join(f'<span class="addon-chip {"yes" if a["available"] else "no"}">{"✓" if a["available"] else "✕"} {esc(a["name"])}</span>' for a in item['addons'])
                    body += f'''<div class="availability-result"><div><h3>{esc(item['name'])}</h3><div class="addon-list">{addon_html or '<span class="muted">No Add-ons configured</span>'}</div></div><button type="button" class="hold-button" data-element="{int(item['id'])}" data-name="{esc(item['name'])}">Select &amp; hold</button></div>'''
            body += '</div>'

        body += f'''<div class="card" id="hold-basket"><h2>Held Elements</h2><div id="hold-list" class="muted">No Elements currently held.</div></div>
        <div id="hold-modal" class="hold-modal" hidden><div class="hold-dialog"><h2>Still want to hold these Elements?</h2><p id="hold-modal-names"></p><p>If you do not click Yes within <strong><span id="hold-modal-seconds">60</span> seconds</strong>, everything will be released.</p><p><button type="button" id="hold-yes">Yes — keep holding</button> <button type="button" class="secondary" id="hold-release">Release now</button></p></div></div>
        <style>
        .calendar-scroll{{overflow-x:auto;border:1px solid #d6dde5;border-radius:8px}}
        .calendar-grid{{min-width:calc(170px + (var(--days) * 48px));background:white}}
        .cal-row{{display:grid;grid-template-columns:170px repeat(var(--days),48px);position:relative;min-height:48px;border-bottom:1px solid #e1e6eb}}
        .cal-head{{min-height:52px;position:sticky;top:0;z-index:8;background:#f4f6f8}}
        .cal-name{{grid-column:1;padding:10px 8px;position:sticky;left:0;z-index:6;background:white;border-right:1px solid #d6dde5}}
        .cal-head .cal-name{{background:#f4f6f8}}
        .cal-date{{padding:6px 2px;text-align:center;border-right:1px solid #e5e9ee;font-size:12px}}
        .cal-date small{{display:block;color:#66717f}}
        .cal-cell{{min-height:48px;border-right:1px solid #eef1f4;display:block;z-index:1}}
        .cal-cell.available{{background:white}}
        .cal-cell.out{{background:#eef1f4}}
        .cal-cell.closed{{background:#e4e7eb}}
        .cal-cell.held,.cal-cell.held-own{{background:#fff0c2}}
        .cal-cell.booked{{background:#f8d7da}}
        .cal-bar{{z-index:4;align-self:center;height:30px;margin:0 2px;border-radius:5px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;display:flex;align-items:center}}
        .cal-bar a,.cal-bar span{{display:block;padding:6px 8px;color:#26313d;text-decoration:none;width:100%;overflow:hidden;text-overflow:ellipsis}}
        .cal-bar.booked,.legend.booked{{background:#f3c5c9}}
        .cal-bar.closed,.legend.closed{{background:#cfd4da}}
        .cal-bar.held,.cal-bar.held-own,.legend.held{{background:#ffe39a}}
        .legend{{padding:4px 9px;border-radius:4px}}
        .availability-result{{display:flex;justify-content:space-between;gap:16px;align-items:center;border-top:1px solid #e1e6eb;padding:14px 0}}
        .availability-result:first-of-type{{border-top:0}}
        .availability-result h3{{margin:0 0 7px}}
        .addon-list{{display:flex;gap:7px;flex-wrap:wrap}}
        .addon-chip{{padding:4px 8px;border-radius:12px;font-size:13px;background:#eef1f4}}
        .addon-chip.yes{{background:#e9f7ec}} .addon-chip.no{{background:#fde8e8;color:#8a1c1c}}
        .hold-modal{{position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:100;display:flex;align-items:center;justify-content:center;padding:20px}}
        .hold-modal[hidden]{{display:none}}
        .hold-dialog{{background:white;max-width:520px;width:100%;padding:22px;border-radius:10px;box-shadow:0 12px 36px rgba(0,0,0,.25)}}
        @media(max-width:700px){{.availability-result{{align-items:flex-start;flex-direction:column}}}}
        </style>
        <script>(function(){{
          const csrf={repr(str(context['csrf_token']))};
          const arrival=document.getElementById('calendar-arrival'), departure=document.getElementById('calendar-departure');
          const holdList=document.getElementById('hold-list'), modal=document.getElementById('hold-modal'), modalNames=document.getElementById('hold-modal-names'), modalSeconds=document.getElementById('hold-modal-seconds');
          let releaseTimer=null, tickTimer=null, prompted=false;
          async function post(url,data){{const body=new URLSearchParams(Object.assign({{csrf:csrf}},data||{{}}));const r=await fetch(url,{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body}});return await r.json();}}
          function fmtDate(s){{const p=s.split('-');return p.length===3?p[2]+'/'+p[1]+'/'+p[0]:s;}}
          async function refreshHolds(){{
            const r=await fetch('/availability/holds'); if(!r.ok)return; const data=await r.json();
            if(!data.holds.length){{holdList.textContent='No Elements currently held.';modal.hidden=true;prompted=false;clearTimeout(releaseTimer);clearInterval(tickTimer);return;}}
            const now=Date.now(); holdList.innerHTML=data.holds.map(h=>'<div><strong>'+h.element_name+'</strong> — '+fmtDate(h.arrival_date)+' to '+fmtDate(h.departure_date)+' · hold expires '+new Date(h.expires_at).toLocaleTimeString([],{{hour:'2-digit',minute:'2-digit'}})+'</div>').join('');
            const needs=data.holds.some(h=>h.needs_confirmation);
            if(needs&&!prompted){{prompted=true;modalNames.textContent=data.holds.map(h=>h.element_name).join(', ');modal.hidden=false;let left=60;modalSeconds.textContent=left;tickTimer=setInterval(()=>{{left=Math.max(0,left-1);modalSeconds.textContent=left;}},1000);releaseTimer=setTimeout(async()=>{{await post('/availability/holds/release');location.reload();}},60000);}}
          }}
          document.querySelectorAll('.hold-button').forEach(b=>b.addEventListener('click',async()=>{{
            if(!arrival.value||!departure.value){{alert('Choose arrival and departure dates first.');return;}}
            const data=await post('/availability/hold',{{element_id:b.dataset.element,arrival_date:arrival.value,departure_date:departure.value}});
            if(!data.ok){{alert(data.error||'That Element is no longer available.');return;}}
            b.textContent='Held';b.disabled=true;await refreshHolds();
          }}));
          document.getElementById('hold-yes').addEventListener('click',async()=>{{await post('/availability/holds/renew');modal.hidden=true;prompted=false;clearTimeout(releaseTimer);clearInterval(tickTimer);await refreshHolds();}});
          document.getElementById('hold-release').addEventListener('click',async()=>{{await post('/availability/holds/release');location.reload();}});
          refreshHolds();setInterval(refreshHolds,15000);
        }})();</script>'''
        return HTMLResponse(layout('Availability Calendar', body, context))

    @app.get('/operations/bookings/{booking_id}', response_class=HTMLResponse)
    def booking_detail(request: Request, booking_id: int):
        context = context_for(database, request)
        if str(context['role']) not in {'operator', 'supervisor'}:
            raise HTTPException(status_code=403, detail='Booking details are staff-only')
        cid = working_company(context)
        if not cid:
            raise HTTPException(status_code=403, detail='Select a Client first')
        with database.connect() as c:
            booking = c.execute('''SELECT b.*,c.first_name,c.last_name,c.email,c.phone FROM bookings b LEFT JOIN customer_records c ON c.id=b.customer_id AND c.company_id=b.company_id WHERE b.id=? AND b.company_id=?''', (booking_id, cid)).fetchone()
            if booking is None:
                raise HTTPException(status_code=404, detail='Booking not found')
            elements = c.execute('''SELECT be.*,e.name AS element_name,e.element_type FROM booking_elements be JOIN setup_elements e ON e.id=be.element_id WHERE be.booking_id=? AND be.company_id=? ORDER BY be.arrival_date,e.name''', (booking_id, cid)).fetchall()
        customer = (f"{booking['first_name']} {booking['last_name']}").strip() or 'Customer'
        body = f'''<h1>Booking {esc(booking['reference'])}</h1><div class="card"><div class="grid"><div><strong>Customer</strong><p>{esc(customer)}</p></div><div><strong>Status</strong><p>{esc(booking['status'])}</p></div><div><strong>Arrival</strong><p>{_fmt(booking['arrival_date'])}</p></div><div><strong>Departure</strong><p>{_fmt(booking['departure_date'])}</p></div></div><p>Email: {esc(booking['email'])}<br>Phone: {esc(booking['phone'])}</p>'''
        if booking['enquiry_id']:
            body += f'<p><a class="button" href="/operations/enquiries/{int(booking["enquiry_id"])}/edit">Open original Enquiry</a></p>'
        body += '<p><a class="button secondary" href="/availability/calendar">Back to Availability Calendar</a></p></div><div class="card"><h2>Elements</h2><table><thead><tr><th>Element</th><th>Type</th><th>Arrival</th><th>Departure</th></tr></thead><tbody>'
        for e in elements:
            body += f'<tr><td>{esc(e["element_name"])}</td><td>{esc(e["element_type"])}</td><td>{_fmt(e["arrival_date"])}</td><td>{_fmt(e["departure_date"])}</td></tr>'
        body += '</tbody></table></div>'
        return HTMLResponse(layout(f'Booking {booking["reference"]}', body, context))
