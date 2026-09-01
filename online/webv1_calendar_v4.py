from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.parse import quote_plus

from fastapi import Request
from fastapi.responses import HTMLResponse

from .app import COOKIE_NAME, esc, layout
from .setup015_core import rows
from .webv1_availability import operating_window
from .webv1_booking_status import default_status
from .webv1_status_availability import available_elements
from .webv1_calendar_v2 import _bar, _blocking_booking, _cols, _fmt, _overlap, _records, _session_context

CALENDAR_DAYS = 28
MAX_CALENDAR_DAYS = 366


def _parse(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _window(start: str, arrival: str, departure: str) -> tuple[date, int]:
    a = _parse(arrival)
    d = _parse(departure)
    requested = _parse(start)
    if a and d and d > a and (requested is None or requested == a):
        stay_days = (d - a).days
        display_days = min(MAX_CALENDAR_DAYS, max(CALENDAR_DAYS, stay_days + 14))
        spare = max(0, display_days - stay_days)
        before = max(1, spare // 2)
        return a - timedelta(days=before), display_days
    visible_start = requested or a or date.today()
    display_days = CALENDAR_DAYS
    if d and d > visible_start:
        display_days = min(MAX_CALENDAR_DAYS, max(display_days, (d - visible_start).days + 7))
    return visible_start, display_days


def _basket_rows(database, company_id: int, token: str):
    with database.connect() as c:
        c.execute('DELETE FROM element_holds WHERE expires_at<=datetime(\'now\')')
        return c.execute(
            '''SELECT h.*,e.name AS element_name,e.element_type
               FROM element_holds h
               JOIN setup_elements e ON e.id=h.element_id AND e.company_id=h.company_id
               WHERE h.company_id=? AND h.session_token=?
               ORDER BY h.created_at,h.id''',
            (company_id, token),
        ).fetchall()


def _header(dates: list[date], arrival_day: date | None, departure_day: date | None, *, actions: bool = False) -> str:
    css = 'progress-row cal-head' if actions else 'cal-row cal-head'
    html = f'<div class="{css}"><div class="cal-name" style="grid-column:1;grid-row:1">Element</div>'
    for i, day in enumerate(dates):
        selected = ' selected-date' if arrival_day and departure_day and arrival_day <= day < departure_day else (' selected-start' if arrival_day == day else '')
        html += f'<div class="cal-date{selected}" data-date="{day.isoformat()}" style="grid-column:{i + 2};grid-row:1"><strong>{day.strftime("%d")}</strong><small>{day.strftime("%b")}</small></div>'
    if actions:
        html += f'<div class="progress-actions-head" style="grid-column:{len(dates) + 2};grid-row:1">Actions</div>'
    return html + '</div>'


def register_calendar_v4_routes(app) -> None:
    database = app.state.database

    @app.get('/availability/calendar-v2', response_class=HTMLResponse)
    def calendar(request: Request, element_type: str = '', start: str = '', arrival: str = '', departure: str = '', edit_hold: int = 0):
        context, cid = _session_context(database, request)
        staff = str(context['role']) in {'operator', 'supervisor'}
        token = request.cookies.get(COOKIE_NAME, '')
        basket = _basket_rows(database, cid, token)
        anchor_arrival = str(basket[0]['arrival_date']) if basket else arrival
        anchor_departure = str(basket[0]['departure_date']) if basket else departure

        types = [str(r['name']) for r in rows(database, 'SELECT name FROM setup_element_types WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (cid,))]
        selected_type = element_type if element_type in types else (types[0] if types else '')
        arrival_day = _parse(arrival)
        departure_day = _parse(departure)
        visible_start, display_days = _window(start, anchor_arrival, anchor_departure)
        visible_end = visible_start + timedelta(days=display_days)
        dates = [visible_start + timedelta(days=i) for i in range(display_days)]

        editing = next((r for r in basket if int(r['id']) == int(edit_hold or 0)), None)
        if edit_hold and editing is None:
            edit_hold = 0

        exact = []
        exact_message = ''
        if selected_type and arrival and departure:
            if not arrival_day or not departure_day:
                exact_message = 'Enter valid arrival and departure dates.'
            elif departure_day <= arrival_day:
                exact_message = 'Departure must be after arrival.'
            else:
                exact = available_elements(database, cid, selected_type, arrival, departure, session_token=token)
                if not exact:
                    exact_message = f'No {selected_type} Elements are available for {_fmt(arrival)} to {_fmt(departure)}.'

        options = ''.join(f'<option value="{esc(t)}" {"selected" if t == selected_type else ""}>{esc(t)}</option>' for t in types)
        preserve = f'element_type={quote_plus(selected_type)}&arrival={quote_plus(arrival)}&departure={quote_plus(departure)}'
        if edit_hold:
            preserve += f'&edit_hold={edit_hold}'

        body = '<h1>Availability Calendar</h1>'

        if basket:
            progress_header = _header(dates, None, None, actions=True)
            progress_rows = ''
            for item in basket:
                cols = _cols(str(item['arrival_date']), str(item['departure_date']), visible_start, visible_end)
                label = f'{esc(item["element_name"])} · {esc(item["element_type"])}'
                bar = _bar(label, '#FFE39A', cols, css='held', title=f'{_fmt(item["arrival_date"])} to {_fmt(item["departure_date"])}')
                actions_col = display_days + 2
                edit_q = f'element_type={quote_plus(str(item["element_type"]))}&arrival={item["arrival_date"]}&departure={item["departure_date"]}&edit_hold={int(item["id"])}'
                actions = f'<div class="progress-actions" style="grid-column:{actions_col};grid-row:1"><a class="button secondary" href="/availability/calendar-v2?{edit_q}">Edit</a><button type="button" class="secondary progress-remove" data-hold="{int(item["id"])}" data-name="{esc(item["element_name"])}">Remove</button></div>'
                progress_rows += f'<div class="progress-row"><div class="cal-name progress-name" style="grid-column:1;grid-row:1"><strong>{esc(item["element_name"])}</strong><small>{esc(item["element_type"])}</small></div>{bar}{actions}</div>'
            body += f'''<div class="card booking-progress"><div class="section-head"><div><h2>Booking in progress</h2><p class="muted">Your selected Elements stay here while you compare and add alternatives below.</p></div><button id="basket-clear" type="button" class="secondary">Clear all</button></div>
            <div id="progress-scroll" class="calendar-scroll progress-scroll"><div class="progress-grid" style="--days:{display_days}">{progress_header}{progress_rows}</div></div></div>'''

        body += f'''<div class="card"><form id="availability-form" method="get" action="/availability/calendar-v2">
        <input type="hidden" id="edit-hold" name="edit_hold" value="{edit_hold or ''}">
        <div class="grid">
          <div><label>Element Type</label><select id="element-type" name="element_type">{options}</select></div>
          <div><label>Arrival</label><input id="arrival-date" type="date" name="arrival" value="{esc(arrival)}"></div>
          <div><label>Departure</label><input id="departure-date" type="date" name="departure" value="{esc(departure)}"></div>
          <div><label>Calendar starts</label><input id="calendar-start" type="date" name="start" value="{visible_start.isoformat()}"></div>
        </div><p>
        <a class="button secondary" href="/availability/calendar-v2?{preserve}&start={(visible_start - timedelta(days=14)).isoformat()}">← Previous 14 days</a>
        <a class="button secondary" href="/availability/calendar-v2?{preserve}&start={(visible_start + timedelta(days=14)).isoformat()}">Next 14 days →</a>
        {'<a class="button secondary" href="/setup/booking-statuses">Booking Statuses</a>' if staff else ''}</p></form></div>'''

        if editing:
            body += f'<div class="card edit-notice"><strong>Editing {esc(editing["element_name"])}</strong> — choose new dates and/or another {esc(editing["element_type"])} Element below, then click <strong>UPDATE</strong>.</div>'

        if staff:
            status_rows = rows(database, 'SELECT * FROM booking_status_definitions WHERE company_id=? AND active=1 ORDER BY display_order,name', (cid,))
            legend = ''.join(f'<span class="legend" style="background:{esc(r["colour"])}">{esc(r["name"])}</span>' for r in status_rows)
            body += f'<div class="card"><div class="legend-row"><strong>Key:</strong>{legend}<span class="legend closed">Closed</span><span>Available = clear</span></div></div>'
        else:
            body += '<div class="card"><div class="legend-row"><strong>Key:</strong><span class="legend booked">Unavailable</span><span>Available = clear</span></div></div>'

        lower_header = _header(dates, arrival_day, departure_day)
        lower_rows = ''
        elements = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1 AND element_type=? ORDER BY name COLLATE NOCASE', (cid, selected_type)) if selected_type else []
        held_status = default_status(database, cid, 'HELD')
        held_colour = str(held_status['colour']) if held_status else '#FFE39A'
        held_name = str(held_status['name']) if held_status else 'Enquiry / Held'
        windows = {day.year: operating_window(database, cid, day.year) for day in dates}
        for element in elements:
            eid = int(element['id'])
            bookings, enquiries, closures, holds = _records(database, cid, eid, visible_start, visible_end)
            cells = ''
            for i, day in enumerate(dates):
                window = windows[day.year]
                state = 'available'
                if window is None or day < window[0] or day + timedelta(days=1) > window[1]:
                    state = 'out'
                elif any(_overlap(r['arrival_date'], r['departure_date'], day) and _blocking_booking(r) for r in bookings):
                    state = 'booked'
                elif any(_overlap(r['arrival_date'], r['departure_date'], day) for r in enquiries):
                    state = 'held'
                elif any(_overlap(r['start_date'], r['end_date'], day) for r in closures):
                    state = 'closed'
                elif any(_overlap(r['arrival_date'], r['departure_date'], day) for r in holds):
                    state = 'held'
                col = i + 2
                selected = ' selected-date' if arrival_day and departure_day and arrival_day <= day < departure_day else (' selected-start' if arrival_day == day else '')
                if state == 'available':
                    cells += f'<button type="button" class="cal-cell available date-pick{selected}" style="grid-column:{col};grid-row:1" data-date="{day.isoformat()}" title="Available {day.strftime("%d/%m/%Y")}"></button>'
                else:
                    cells += f'<span class="cal-cell {state}{selected}" style="grid-column:{col};grid-row:1" data-date="{day.isoformat()}"></span>'

            bars = ''
            for booking in bookings:
                if not staff and not _blocking_booking(booking):
                    continue
                cols = _cols(booking['arrival_date'], booking['departure_date'], visible_start, visible_end)
                if staff:
                    customer = (f"{booking['first_name']} {booking['last_name']}").strip() or 'Customer'
                    status_name = str(booking['workflow_name'] or 'Booked')
                    colour = str(booking['colour'] or '#F3C5C9')
                    bars += _bar(f'{customer} · {booking["reference"]} · {status_name}', colour, cols, href=f'/operations/bookings/{int(booking["booking_id"])}')
                else:
                    bars += _bar('Unavailable', '#F3C5C9', cols)
            for enquiry in enquiries:
                cols = _cols(enquiry['arrival_date'], enquiry['departure_date'], visible_start, visible_end)
                if staff:
                    customer = (f"{enquiry['first_name']} {enquiry['last_name']}").strip() or 'Customer'
                    bars += _bar(f'{customer} · Enquiry #{int(enquiry["enquiry_id"])} · {enquiry["workflow_name"] or held_name}', str(enquiry['colour'] or held_colour), cols, href=f'/operations/enquiries/{int(enquiry["enquiry_id"])}')
                else:
                    bars += _bar('Unavailable', '#F3C5C9', cols)
            for closure in closures:
                bars += _bar(str(closure['reason'] or 'Closed') if staff else 'Unavailable', '#CFD4DA' if staff else '#F3C5C9', _cols(closure['start_date'], closure['end_date'], visible_start, visible_end), css='closed')
            for hold in holds:
                own = str(hold['session_token']) == token
                bars += _bar('In this booking' if own else (held_name if staff else 'Unavailable'), held_colour if staff or own else '#F3C5C9', _cols(hold['arrival_date'], hold['departure_date'], visible_start, visible_end), css='held')
            lower_rows += f'<div class="cal-row"><div class="cal-name" style="grid-column:1;grid-row:1"><strong>{esc(element["name"])}</strong></div>{cells}{bars}</div>'

        if not lower_rows:
            lower_rows = '<div class="card"><p>No active Elements exist for this Element Type.</p></div>'
        lower_title = f'Availability / alternatives — {esc(selected_type)}' if selected_type else 'Availability / alternatives'
        body += f'<div class="card"><h2>{lower_title}</h2><div id="calendar-scroll" class="calendar-scroll"><div class="calendar-grid" style="--days:{display_days}">{lower_header}{lower_rows}</div></div><p class="muted">The lower calendar stays on the original booking date window while you add or adjust Elements.</p></div>'

        if arrival and departure:
            body += '<div class="card"><h2>Available for your selected dates</h2>'
            if exact_message:
                body += f'<div class="error"><strong>{esc(exact_message)}</strong><br>Try different dates or another Element Type.</div>'
            else:
                body += f'<p><strong>{esc(selected_type)}</strong> — {_fmt(arrival)} to {_fmt(departure)}</p>'
                for item in exact:
                    chips = ''.join(f'<span class="addon-chip {"yes" if addon["available"] else "no"}">{"✓" if addon["available"] else "✕"} {esc(addon["name"])}</span>' for addon in item['addons'])
                    label = 'UPDATE' if edit_hold else 'Select &amp; hold'
                    cls = 'update-button' if edit_hold else 'hold-button'
                    body += f'<div class="availability-result"><div><h3>{esc(item["name"])}</h3><div class="addon-list">{chips}</div></div><button type="button" class="{cls}" data-element="{int(item["id"])}" data-name="{esc(item["name"])}">{label}</button></div>'
            body += '</div>'

        body += f'''<div id="hold-modal" class="hold-modal" hidden><div class="hold-dialog"><h2>Still want to hold these Elements?</h2><p id="hold-names"></p><p>If Yes is not clicked before the hold expires, everything is released automatically.</p><p><button id="hold-yes" type="button">Yes — keep holding</button> <button id="hold-release" type="button" class="secondary">Release now</button></p></div></div>
        <style>
        .calendar-scroll{{overflow:auto;max-height:520px;border:1px solid #d6dde5;border-radius:8px;position:relative;scrollbar-gutter:stable both-edges}}
        .calendar-grid{{min-width:calc(170px + (var(--days) * 48px));background:white}}
        .progress-grid{{min-width:calc(320px + (var(--days) * 48px));background:white}}
        .cal-row{{display:grid;grid-template-columns:170px repeat(var(--days),48px);position:relative;min-height:48px;border-bottom:1px solid #e1e6eb}}
        .progress-row{{display:grid;grid-template-columns:170px repeat(var(--days),48px) 150px;position:relative;min-height:52px;border-bottom:1px solid #e1e6eb}}
        .cal-head{{min-height:52px;background:#f4f6f8;position:sticky;top:0;z-index:10}}
        .cal-name{{padding:10px 8px;position:sticky;left:0;z-index:6;background:white;border-right:1px solid #d6dde5}}
        .progress-name small{{display:block;color:#66717f;margin-top:2px}} .cal-head .cal-name{{background:#f4f6f8;z-index:12}}
        .cal-date{{padding:6px 2px;text-align:center;border-right:1px solid #e5e9ee;font-size:12px;background:#f4f6f8}} .cal-date small{{display:block;color:#66717f}}
        .cal-cell{{min-height:48px;border:0;border-right:1px solid #eef1f4;display:block;z-index:1;padding:0;margin:0;border-radius:0;cursor:default}}
        button.cal-cell.available{{background:white;cursor:pointer}} button.cal-cell.available:hover{{outline:2px solid #5f7893;outline-offset:-2px}}
        .cal-cell.out{{background:#eef1f4}} .cal-cell.closed{{background:#e4e7eb}} .cal-cell.held{{background:#fff0c2}} .cal-cell.booked{{background:#f8d7da}}
        .cal-cell.selected-date,.cal-date.selected-date{{box-shadow:inset 0 0 0 2px #6d8196}} .cal-cell.selected-start,.cal-date.selected-start{{box-shadow:inset 0 0 0 3px #405b75}}
        .cal-bar{{z-index:4;align-self:center;height:30px;margin:0 2px;border-radius:5px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;display:flex;align-items:center}} .cal-bar a,.cal-bar span{{display:block;padding:6px 8px;color:#26313d;text-decoration:none;width:100%;overflow:hidden;text-overflow:ellipsis}}
        .progress-actions,.progress-actions-head{{position:sticky;right:0;z-index:8;background:white;border-left:1px solid #d6dde5;display:flex;align-items:center;justify-content:center;gap:5px;padding:5px}} .progress-actions-head{{background:#f4f6f8;font-weight:bold;z-index:12}} .progress-actions a,.progress-actions button{{font-size:12px;padding:5px 7px}}
        .section-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:14px}} .booking-progress h2{{margin-bottom:4px}} .edit-notice{{border-color:#9bb3c9;background:#f3f8fc}}
        .legend-row{{display:flex;gap:10px;flex-wrap:wrap;align-items:center}} .legend{{padding:4px 9px;border-radius:4px;border:1px solid rgba(0,0,0,.08)}} .legend.closed{{background:#cfd4da}} .legend.booked{{background:#f3c5c9}}
        .availability-result{{display:flex;justify-content:space-between;gap:16px;align-items:center;border-top:1px solid #e1e6eb;padding:14px 0}} .addon-list{{display:flex;gap:7px;flex-wrap:wrap}} .addon-chip{{padding:5px 8px;border-radius:5px;background:#edf2f7}} .addon-chip.no{{color:#8a2731}}
        .hold-modal{{position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:100;display:flex;align-items:center;justify-content:center}} .hold-modal[hidden]{{display:none}} .hold-dialog{{background:white;padding:24px;border-radius:10px;max-width:520px;width:90%}}
        </style>
        <script>
        const csrf={json.dumps(str(context['csrf_token']))}; const arr={json.dumps(arrival)}; const dep={json.dumps(departure)}; const editingHold={int(edit_hold or 0)};
        const anchorArr={json.dumps(anchor_arrival)}; const anchorDep={json.dumps(anchor_departure)};
        const form=document.getElementById('availability-form'),elementType=document.getElementById('element-type'),arrivalInput=document.getElementById('arrival-date'),departureInput=document.getElementById('departure-date'),startInput=document.getElementById('calendar-start'),scrollBox=document.getElementById('calendar-scroll'),progressScroll=document.getElementById('progress-scroll');
        function goToSelection(){{const a=arrivalInput.value,d=departureInput.value;if(!a||!d)return;if(d<=a){{departureInput.setCustomValidity('Departure must be after arrival.');departureInput.reportValidity();return;}}departureInput.setCustomValidity('');const q=new URLSearchParams();q.set('element_type',elementType.value);q.set('arrival',a);q.set('departure',d);if(editingHold)q.set('edit_hold',editingHold);window.location='/availability/calendar-v2?'+q.toString();}}
        elementType.addEventListener('change',()=>{{const q=new URLSearchParams();q.set('element_type',elementType.value);q.set('arrival',editingHold&&arrivalInput.value?arrivalInput.value:(anchorArr||arrivalInput.value));q.set('departure',editingHold&&departureInput.value?departureInput.value:(anchorDep||departureInput.value));if(editingHold)q.set('edit_hold',editingHold);window.location='/availability/calendar-v2?'+q.toString();}});
        arrivalInput.addEventListener('change',()=>{{departureInput.setCustomValidity('');if(arrivalInput.value&&departureInput.value)goToSelection();}});departureInput.addEventListener('change',goToSelection);startInput.addEventListener('change',()=>form.submit());
        document.querySelectorAll('.date-pick').forEach(cell=>cell.addEventListener('click',()=>{{const chosen=cell.dataset.date;if(!arrivalInput.value||departureInput.value||chosen<=arrivalInput.value){{arrivalInput.value=chosen;departureInput.value='';document.querySelectorAll('.selected-date,.selected-start').forEach(x=>x.classList.remove('selected-date','selected-start'));document.querySelectorAll('[data-date="'+chosen+'"]').forEach(x=>x.classList.add('selected-start'));return;}}departureInput.value=chosen;goToSelection();}}));
        if(scrollBox&&progressScroll){{let syncing=false;scrollBox.addEventListener('scroll',()=>{{if(syncing)return;syncing=true;progressScroll.scrollLeft=scrollBox.scrollLeft;syncing=false;}});progressScroll.addEventListener('scroll',()=>{{if(syncing)return;syncing=true;scrollBox.scrollLeft=progressScroll.scrollLeft;syncing=false;}});}}
        if(anchorArr&&scrollBox){{const first=document.querySelector('#calendar-scroll .cal-date[data-date="'+anchorArr+'"]');const last=document.querySelector('#calendar-scroll .cal-date[data-date="'+anchorDep+'"]');if(first){{const middle=last?(first.offsetLeft+last.offsetLeft)/2:first.offsetLeft;scrollBox.scrollLeft=Math.max(0,middle-(scrollBox.clientWidth/2));if(progressScroll)progressScroll.scrollLeft=scrollBox.scrollLeft;}}}}
        async function post(url,data={{}}){{const body=new URLSearchParams();body.set('csrf',csrf);Object.entries(data).forEach(([k,v])=>body.set(k,v));return fetch(url,{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'}},body:body.toString()}})}}
        document.querySelectorAll('.hold-button').forEach(btn=>btn.addEventListener('click',async()=>{{if(!arr||!dep){{alert('Choose Arrival and Departure first.');return;}}const r=await post('/availability/hold',{{element_id:btn.dataset.element,arrival_date:arr,departure_date:dep}});let data={{}};try{{data=await r.json();}}catch(e){{}}if(!r.ok){{alert(data.error||data.detail||'Unable to hold that Element');return;}}window.location='/availability/calendar-v2?element_type='+encodeURIComponent(elementType.value)+'&arrival='+encodeURIComponent(anchorArr||arr)+'&departure='+encodeURIComponent(anchorDep||dep);}}));
        document.querySelectorAll('.update-button').forEach(btn=>btn.addEventListener('click',async()=>{{const r=await post('/availability/basket/update',{{hold_id:editingHold,element_id:btn.dataset.element,arrival_date:arr,departure_date:dep}});let data={{}};try{{data=await r.json();}}catch(e){{}}if(!r.ok){{alert(data.error||'Unable to update that booking item.');return;}}window.location='/availability/calendar-v2?element_type='+encodeURIComponent(data.item.element_type)+'&arrival='+encodeURIComponent(anchorArr||arr)+'&departure='+encodeURIComponent(anchorDep||dep);}}));
        document.querySelectorAll('.progress-remove').forEach(btn=>btn.addEventListener('click',async()=>{{if(!confirm('Remove '+btn.dataset.name+' from this booking?'))return;const r=await post('/availability/basket/remove',{{hold_id:btn.dataset.hold}});if(!r.ok){{alert('Unable to remove that item.');return;}}window.location.reload();}}));
        const clear=document.getElementById('basket-clear');if(clear)clear.addEventListener('click',async()=>{{if(!confirm('Remove all held items from this booking?'))return;await post('/availability/holds/release');window.location.reload();}});
        async function checkExpiry(){{const r=await fetch('/availability/basket');if(!r.ok)return;const data=await r.json();if(!data.items.length&&{str(bool(basket)).lower()}){{window.location.reload();return;}}if(data.items.some(x=>x.needs_confirmation)){{document.getElementById('hold-names').textContent=data.items.map(x=>x.element_name).join(', ');document.getElementById('hold-modal').hidden=false;}}}}
        document.getElementById('hold-yes').addEventListener('click',async()=>{{await post('/availability/holds/renew');document.getElementById('hold-modal').hidden=true;}});document.getElementById('hold-release').addEventListener('click',async()=>{{await post('/availability/holds/release');window.location.reload();}});setInterval(checkExpiry,5000);
        </script>'''
        return HTMLResponse(layout('Availability Calendar', body, context))
