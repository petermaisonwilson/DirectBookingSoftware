from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.parse import quote_plus

from fastapi import Request
from fastapi.responses import HTMLResponse

from .app import COOKIE_NAME, esc, layout
from .setup015_core import rows
from .webv1_addon_popup import popup_addons_for_element
from .webv1_booking_status import default_status
from .webv1_calendar_v2 import _bar, _cols, _fmt, _records, _session_context
from .webv1_calendar_v4 import _basket_rows, _header, _parse, _window
from .webv1_status_availability import availability_state


def register_calendar_v5_routes(app) -> None:
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
        selected_type = element_type if element_type in types else ''
        visible_start, display_days = _window(start, anchor_arrival, anchor_departure)
        visible_end = visible_start + timedelta(days=display_days)
        dates = [visible_start + timedelta(days=i) for i in range(display_days)]
        arrival_day, departure_day = _parse(arrival), _parse(departure)

        editing = next((r for r in basket if int(r['id']) == int(edit_hold or 0)), None)
        if edit_hold and editing is None:
            edit_hold = 0
        if editing:
            selected_type = str(editing['element_type'])
            if not arrival:
                arrival = str(editing['arrival_date']); arrival_day = _parse(arrival)
            if not departure:
                departure = str(editing['departure_date']); departure_day = _parse(departure)

        options = '<option value="">Select Element Type</option>' + ''.join(
            f'<option value="{esc(t)}" {"selected" if t == selected_type else ""}>{esc(t)}</option>' for t in types
        )
        preserve = f'element_type={quote_plus(selected_type)}&arrival={quote_plus(arrival)}&departure={quote_plus(departure)}'
        if edit_hold:
            preserve += f'&edit_hold={edit_hold}'

        body = '<h1>Availability Calendar</h1>'
        body += f'''<div class="card"><form id="availability-form" method="get" action="/availability/calendar-v2">
        <input type="hidden" id="edit-hold" name="edit_hold" value="{edit_hold or ''}">
        <input type="hidden" id="calendar-start" name="start" value="{visible_start.isoformat()}">
        <div class="grid">
          <div><label>Element Type</label><select id="element-type" name="element_type">{options}</select></div>
          <div><label>Arrival</label><input id="arrival-date" type="date" name="arrival" value="{esc(arrival)}"></div>
          <div><label>Departure</label><input id="departure-date" type="date" name="departure" value="{esc(departure)}"></div>
        </div><p>
        <a class="button secondary" href="/availability/calendar-v2?{preserve}&start={(visible_start - timedelta(days=14)).isoformat()}">← Previous 14 days</a>
        <a class="button secondary" href="/availability/calendar-v2?{preserve}&start={(visible_start + timedelta(days=14)).isoformat()}">Next 14 days →</a>
        {'<a class="button secondary" href="/setup/booking-statuses">Booking Statuses</a>' if staff else ''}</p></form></div>'''

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
            body += f'''<div class="card booking-progress"><div class="section-head"><div><h2>Booking in progress</h2></div><button id="basket-clear" type="button" class="secondary">Clear all</button></div>
            <div id="progress-scroll" class="calendar-scroll progress-scroll"><div class="progress-grid" style="--days:{display_days}">{progress_header}{progress_rows}</div></div></div>'''

        if editing:
            body += f'<div class="card edit-notice"><strong>Editing {esc(editing["element_name"])}</strong> — select new dates and/or another {esc(editing["element_type"])} Element directly on the calendar, then click <strong>UPDATE</strong> on the coloured selection.</div>'

        held_status = default_status(database, cid, 'HELD')
        held_colour = str(held_status['colour']) if held_status else '#FFE39A'
        held_name = str(held_status['name']) if held_status else 'Enquiry / Held'
        if staff:
            status_rows = rows(database, 'SELECT * FROM booking_status_definitions WHERE company_id=? AND active=1 ORDER BY display_order,name', (cid,))
            status_legend = ''.join(f'<span class="legend mini" style="background:{esc(r["colour"])}">{esc(r["name"])}</span>' for r in status_rows)
        else:
            status_legend = '<span class="legend mini unavailable">Unavailable</span>'
        legend = f'<span class="legend mini available-key">Available</span>{status_legend}<span class="legend mini own-held">In this booking</span><span class="legend mini closed">Closed</span>'

        cal_header = _header(dates, arrival_day, departure_day)
        cal_rows = ''
        elements = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1 AND element_type=? ORDER BY name COLLATE NOCASE', (cid, selected_type)) if selected_type else []
        feature_year = (_parse(arrival) or _parse(anchor_arrival) or visible_start).year
        for element in elements:
            eid = int(element['id'])
            features = popup_addons_for_element(database, cid, feature_year, element)
            feature_json = esc(json.dumps(features, ensure_ascii=False))
            bookings, enquiries, closures, holds = _records(database, cid, eid, visible_start, visible_end)
            cells = ''
            selected_range_available = bool(
                arrival_day and departure_day and departure_day > arrival_day
                and arrival_day >= visible_start and departure_day <= visible_end
            )
            for i, day in enumerate(dates):
                next_day = day + timedelta(days=1)
                state = availability_state(database, cid, eid, day.isoformat(), next_day.isoformat(), session_token=token)
                code = str(state['state'])
                own_editing_day = bool(editing and eid == int(editing['element_id']) and _parse(str(editing['arrival_date'])) <= day < _parse(str(editing['departure_date'])))
                selected = ' selected-date' if arrival_day and departure_day and arrival_day <= day < departure_day else (' selected-start' if arrival_day == day else '')
                if arrival_day and departure_day and arrival_day <= day < departure_day and code != 'AVAILABLE':
                    selected_range_available = False
                col = i + 2
                if code == 'AVAILABLE':
                    cells += f'<button type="button" class="cal-cell available date-pick{selected}" style="grid-column:{col};grid-row:1" data-date="{day.isoformat()}" data-element="{eid}" data-name="{esc(element["name"])}" title="Available {day.strftime("%d/%m/%Y")}"></button>'
                elif code == 'HELD_BY_YOU':
                    cls = ' editable-own date-pick' if own_editing_day else ''
                    tag = 'button type="button"' if own_editing_day else 'span'
                    endtag = 'button' if own_editing_day else 'span'
                    cells += f'<{tag} class="cal-cell own-held{cls}{selected}" style="grid-column:{col};grid-row:1" data-date="{day.isoformat()}" data-element="{eid}" data-name="{esc(element["name"])}"></{endtag}>'
                elif code in {'BOOKED','ENQUIRY','HELD'}:
                    cells += f'<span class="cal-cell unavailable{selected}" style="grid-column:{col};grid-row:1" data-date="{day.isoformat()}"></span>'
                else:
                    cells += f'<span class="cal-cell closed{selected}" style="grid-column:{col};grid-row:1" data-date="{day.isoformat()}"></span>'

            bars = ''
            for booking in bookings:
                cols = _cols(booking['arrival_date'], booking['departure_date'], visible_start, visible_end)
                if staff:
                    customer = (f"{booking['first_name']} {booking['last_name']}").strip() or 'Customer'
                    status_name = str(booking['workflow_name'] or 'Booked')
                    bars += _bar(f'{customer} · {booking["reference"]} · {status_name}', str(booking['colour'] or '#F3C5C9'), cols, href=f'/operations/bookings/{int(booking["booking_id"])}')
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
                if str(hold['session_token']) == token:
                    continue
                bars += _bar(held_name if staff else 'Unavailable', held_colour if staff else '#F3C5C9', _cols(hold['arrival_date'], hold['departure_date'], visible_start, visible_end), css='held')

            name_html = f'<strong>{esc(element["name"])}</strong><button type="button" class="more-info" data-element-name="{esc(element["name"])}" data-features="{feature_json}">More info</button>'
            selection_style = ''
            selection_hidden = ' hidden'
            if not edit_hold and selected_range_available and arrival_day and departure_day:
                selection_start = (arrival_day - visible_start).days + 2
                selection_end = (departure_day - visible_start).days + 2
                selection_style = f' style="grid-column:{selection_start} / {selection_end};grid-row:1"'
                selection_hidden = ''
            selection = f'<button type="button" class="selection-action" data-element="{eid}" data-name="{esc(element["name"])}"{selection_style}{selection_hidden}>{"UPDATE" if edit_hold else "RESERVE"}</button>'
            cal_rows += f'<div class="cal-row element-row" data-element="{eid}" data-name="{esc(element["name"])}" data-features="{feature_json}"><div class="cal-name" style="grid-column:1;grid-row:1">{name_html}</div>{cells}{bars}{selection}</div>'

        if not cal_rows:
            if selected_type:
                cal_rows = '<div class="card"><p>No active Elements exist for this Element Type.</p></div>'
            else:
                cal_rows = '<div class="card"><p>Select an Element Type to see matching Elements.</p></div>'
        body += f'''<div class="card availability-card"><div class="availability-head"><h2>Availability</h2><div class="legend-row compact"><strong>Key:</strong>{legend}</div></div>
        <div id="calendar-scroll" class="calendar-scroll"><div class="calendar-grid" style="--days:{display_days}">{cal_header}{cal_rows}</div></div>
        <p class="muted">Pale green days are available. Click a start day and then a later day on the same Element. Hover a green day for the quick details; use More info under the Element name for the larger information panel.</p></div>'''

        body += f'''<div id="quick-popover" class="quick-popover" hidden><strong id="quick-name"></strong><div id="quick-dates" class="muted"></div><div id="quick-features"></div><button id="quick-action" type="button" hidden></button></div>
        <div id="info-modal" class="hold-modal" hidden><div class="hold-dialog"><button id="info-close" type="button" class="secondary close-info">Close</button><h2 id="info-title"></h2><p class="muted">Element information</p><div id="info-features"></div></div></div>
        <div id="hold-modal" class="hold-modal" hidden><div class="hold-dialog"><h2>Still want to hold these Elements?</h2><p id="hold-names"></p><p>If Yes is not clicked before the hold expires, everything is released automatically.</p><p><button id="hold-yes" type="button">Yes — keep holding</button> <button id="hold-release" type="button" class="secondary">Release now</button></p></div></div>
        <style>
        .calendar-scroll{{overflow:auto;max-height:520px;border:1px solid #d6dde5;border-radius:8px;position:relative;scrollbar-gutter:stable both-edges}}
        .calendar-grid{{min-width:calc(190px + (var(--days) * 48px));background:white;position:relative}}
        .progress-grid{{min-width:calc(340px + (var(--days) * 48px));background:white;position:relative}}
        .cal-row{{display:grid;grid-template-columns:190px repeat(var(--days),48px);position:relative;min-height:56px;border-bottom:4px solid #fff;box-sizing:border-box;overflow:visible}}
        .progress-row{{display:grid;grid-template-columns:190px repeat(var(--days),48px) 150px;position:relative;min-height:52px;border-bottom:1px solid #e1e6eb;overflow:visible}}
        .element-row .cal-cell{{min-height:52px;height:52px;align-self:start;box-sizing:border-box}}
        .element-row .cal-name{{height:52px;align-self:start}}
        .element-row.party-unsuitable{{min-height:76px;height:76px}}
        .element-row.party-unsuitable .cal-cell{{min-height:72px;height:72px}}
        .element-row.party-unsuitable .cal-name{{height:72px;white-space:normal;overflow:visible}}
        .cal-head{{min-height:52px;background:#f4f6f8;position:sticky;top:0;z-index:30}}
        .cal-name{{padding:8px;position:sticky;left:0;z-index:20;width:190px;min-width:190px;max-width:190px;box-sizing:border-box;background:white;border-right:2px solid #c5cdd6;box-shadow:4px 0 7px -6px rgba(0,0,0,.7)}} .cal-name strong{{display:block}} .cal-name .more-info{{border:0;background:none;color:#365f86;padding:2px 0;font-size:12px;text-decoration:underline}}
        .progress-name small{{display:block;color:#66717f;margin-top:2px}} .cal-head .cal-name{{background:#f4f6f8;z-index:40}}
        .cal-date{{padding:6px 2px;text-align:center;border-right:1px solid #e5e9ee;font-size:12px;background:#f4f6f8}} .cal-date small{{display:block;color:#66717f}}
        .cal-cell{{min-height:52px;border:0;border-right:1px solid rgba(255,255,255,.6);display:block;z-index:1;padding:0;margin:0;border-radius:0}}
        button.cal-cell{{cursor:pointer}} .cal-cell.available{{background:#dff2df}} .cal-cell.available:hover{{background:#c8e9c8;outline:2px solid #5f8b66;outline-offset:-2px}} .cal-cell.unavailable{{background:#f6d6d9}} .cal-cell.own-held{{background:#ffe39a}} .cal-cell.closed{{background:#e1e4e8}}
        .cal-cell.selected-date,.cal-date.selected-date{{box-shadow:inset 0 0 0 2px #6d8196}} .cal-cell.selected-start,.cal-date.selected-start{{box-shadow:inset 0 0 0 3px #405b75}}
        .cal-bar{{z-index:4;align-self:center;height:30px;margin:0 2px;border-radius:5px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;display:flex;align-items:center}} .cal-bar a,.cal-bar span{{display:block;padding:6px 8px;color:#26313d;text-decoration:none;width:100%;overflow:hidden;text-overflow:ellipsis}}
        .selection-action{{z-index:7;align-self:center;height:34px;margin:0 2px;padding:4px 12px;border-radius:6px;font-weight:bold}}
        .progress-actions,.progress-actions-head{{position:sticky;right:0;z-index:8;background:white;border-left:1px solid #d6dde5;display:flex;align-items:center;justify-content:center;gap:5px;padding:5px}} .progress-actions-head{{background:#f4f6f8;font-weight:bold;z-index:12}} .progress-actions a,.progress-actions button{{font-size:12px;padding:5px 7px}}
        .section-head,.availability-head{{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap}} .booking-progress h2,.availability-head h2{{margin:0}} .edit-notice{{border-color:#9bb3c9;background:#f3f8fc}}
        .legend-row{{display:flex;gap:6px;flex-wrap:wrap;align-items:center}} .legend.mini{{padding:2px 6px;border-radius:4px;border:1px solid rgba(0,0,0,.08);font-size:11px}} .available-key{{background:#dff2df}} .unavailable{{background:#f6d6d9}} .own-held{{background:#ffe39a}} .legend.closed{{background:#e1e4e8}}
        .quick-popover{{position:fixed;z-index:90;background:white;border:1px solid #bcc7d2;border-radius:8px;box-shadow:0 8px 25px rgba(0,0,0,.18);padding:12px;min-width:230px;max-width:320px}} .quick-popover ul,.hold-dialog ul{{margin:8px 0;padding-left:20px}} .quick-popover button{{margin-top:8px}}
        .hold-modal{{position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:100;display:flex;align-items:center;justify-content:center}} .hold-modal[hidden],.quick-popover[hidden]{{display:none}} .hold-dialog{{background:white;padding:24px;border-radius:10px;max-width:620px;width:90%;max-height:80vh;overflow:auto}} .close-info{{float:right}}
        </style>
        <script>
        const csrf={json.dumps(str(context['csrf_token']))}; const editingHold={int(edit_hold or 0)}; const anchorArr={json.dumps(anchor_arrival)}; const anchorDep={json.dumps(anchor_departure)};
        const form=document.getElementById('availability-form'),elementType=document.getElementById('element-type'),arrivalInput=document.getElementById('arrival-date'),departureInput=document.getElementById('departure-date'),startInput=document.getElementById('calendar-start'),scrollBox=document.getElementById('calendar-scroll'),progressScroll=document.getElementById('progress-scroll');
        let selectedElement={int(editing['element_id']) if editing else 0}; let firstPick='';
        function qsFor(a,d){{const q=new URLSearchParams();if(elementType.value)q.set('element_type',elementType.value);if(a)q.set('arrival',a);if(d)q.set('departure',d);if(editingHold)q.set('edit_hold',editingHold);return q;}}
        function submitDates(){{const a=arrivalInput.value,d=departureInput.value;if(!a||!d)return;if(d<=a){{departureInput.setCustomValidity('Departure must be after arrival.');departureInput.reportValidity();return;}}window.location='/availability/calendar-v2?'+qsFor(a,d).toString();}}
        elementType.addEventListener('change',()=>{{const a=anchorArr||arrivalInput.value,d=anchorDep||departureInput.value;window.location='/availability/calendar-v2?'+qsFor(a,d).toString();}}); arrivalInput.addEventListener('change',()=>{{if(arrivalInput.value&&departureInput.value)submitDates();}}); departureInput.addEventListener('change',submitDates);
        function clearSelectionBars(){{document.querySelectorAll('.selection-action').forEach(x=>x.hidden=true);}}
        function showSelection(elementId,a,d){{clearSelectionBars();const row=document.querySelector('.element-row[data-element="'+elementId+'"]');if(!row)return;const dates=[...document.querySelectorAll('#calendar-scroll .cal-date')].map(x=>x.dataset.date);let s=dates.indexOf(a),e=dates.indexOf(d);if(s<0||e<0||e<=s)return;const btn=row.querySelector('.selection-action');btn.style.gridColumn=(s+2)+' / '+(e+2);btn.style.gridRow='1';btn.hidden=false;selectedElement=Number(elementId);}}
        if(editingHold&&arrivalInput.value&&departureInput.value&&selectedElement)showSelection(selectedElement,arrivalInput.value,departureInput.value);
        document.querySelectorAll('.date-pick').forEach(cell=>cell.addEventListener('click',()=>{{const chosen=cell.dataset.date,eid=Number(cell.dataset.element);if(!firstPick||selectedElement!==eid){{firstPick=chosen;selectedElement=eid;arrivalInput.value=chosen;departureInput.value='';clearSelectionBars();return;}}if(chosen<=firstPick){{firstPick=chosen;arrivalInput.value=chosen;departureInput.value='';return;}}arrivalInput.value=firstPick;departureInput.value=chosen;showSelection(eid,firstPick,chosen);firstPick='';}}));
        async function post(url,data={{}}){{const body=new URLSearchParams();body.set('csrf',csrf);Object.entries(data).forEach(([k,v])=>body.set(k,v));return fetch(url,{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'}},body:body.toString()}})}}
        document.querySelectorAll('.selection-action').forEach(btn=>btn.addEventListener('click',async()=>{{const a=arrivalInput.value,d=departureInput.value;if(!a||!d){{alert('Choose the dates on the calendar first.');return;}}const url=editingHold?'/availability/basket/update':'/availability/hold';const data=editingHold?{{hold_id:editingHold,element_id:btn.dataset.element,arrival_date:a,departure_date:d}}:{{element_id:btn.dataset.element,arrival_date:a,departure_date:d}};const r=await post(url,data);let payload={{}};try{{payload=await r.json();}}catch(e){{}}if(!r.ok){{alert(payload.error||payload.detail||'Unable to save that Element.');return;}}window.location='/availability/calendar-v2?element_type='+encodeURIComponent(elementType.value)+'&arrival='+encodeURIComponent(anchorArr||a)+'&departure='+encodeURIComponent(anchorDep||d);}}));
        document.querySelectorAll('.progress-remove').forEach(btn=>btn.addEventListener('click',async()=>{{if(!confirm('Remove '+btn.dataset.name+' from this booking?'))return;const r=await post('/availability/basket/remove',{{hold_id:btn.dataset.hold}});if(!r.ok){{alert('Unable to remove that item.');return;}}window.location.reload();}})); const clear=document.getElementById('basket-clear');if(clear)clear.addEventListener('click',async()=>{{if(!confirm('Remove all held items from this booking?'))return;await post('/availability/holds/release');window.location.reload();}});
        if(scrollBox&&progressScroll){{let syncing=false;scrollBox.addEventListener('scroll',()=>{{if(syncing)return;syncing=true;progressScroll.scrollLeft=scrollBox.scrollLeft;syncing=false;}});progressScroll.addEventListener('scroll',()=>{{if(syncing)return;syncing=true;scrollBox.scrollLeft=progressScroll.scrollLeft;syncing=false;}});}}
        if(anchorArr&&scrollBox){{const first=document.querySelector('#calendar-scroll .cal-date[data-date="'+anchorArr+'"]');const last=document.querySelector('#calendar-scroll .cal-date[data-date="'+anchorDep+'"]');if(first){{const middle=last?(first.offsetLeft+last.offsetLeft)/2:first.offsetLeft;scrollBox.scrollLeft=Math.max(0,middle-(scrollBox.clientWidth/2));if(progressScroll)progressScroll.scrollLeft=scrollBox.scrollLeft;}}}}
        const pop=document.getElementById('quick-popover'),qname=document.getElementById('quick-name'),qdates=document.getElementById('quick-dates'),qfeatures=document.getElementById('quick-features'),qaction=document.getElementById('quick-action');let hideTimer;
        function featureHtml(features){{return '<ul>'+features.map(f=>'<li>'+(f.available?'✓ ':'✕ ')+f.name+'</li>').join('')+'</ul>';}}
        function showQuick(cell,ev){{clearTimeout(hideTimer);const row=cell.closest('.element-row'),features=JSON.parse(row.dataset.features||'[]');qname.textContent=row.dataset.name;qdates.textContent=arrivalInput.value&&departureInput.value?(arrivalInput.value+' to '+departureInput.value):('Available '+cell.dataset.date);qfeatures.innerHTML=featureHtml(features);qaction.hidden=!(arrivalInput.value&&departureInput.value&&selectedElement===Number(row.dataset.element));if(!qaction.hidden){{qaction.textContent=editingHold?'UPDATE':'RESERVE';qaction.dataset.element=row.dataset.element;}}pop.style.left=Math.min(window.innerWidth-340,ev.clientX+15)+'px';pop.style.top=Math.min(window.innerHeight-250,ev.clientY+15)+'px';pop.hidden=false;}}
        document.querySelectorAll('.cal-cell.available').forEach(cell=>{{cell.addEventListener('mouseenter',e=>showQuick(cell,e));cell.addEventListener('mouseleave',()=>hideTimer=setTimeout(()=>pop.hidden=true,250));}});pop.addEventListener('mouseenter',()=>clearTimeout(hideTimer));pop.addEventListener('mouseleave',()=>hideTimer=setTimeout(()=>pop.hidden=true,200));qaction.addEventListener('click',()=>{{const row=document.querySelector('.element-row[data-element="'+qaction.dataset.element+'"]');if(row)row.querySelector('.selection-action').click();}});
        const info=document.getElementById('info-modal');document.querySelectorAll('.more-info').forEach(btn=>btn.addEventListener('click',()=>{{document.getElementById('info-title').textContent=btn.dataset.elementName;document.getElementById('info-features').innerHTML=featureHtml(JSON.parse(btn.dataset.features||'[]'));info.hidden=false;}}));document.getElementById('info-close').addEventListener('click',()=>info.hidden=true);
        async function checkExpiry(){{const r=await fetch('/availability/basket');if(!r.ok)return;const data=await r.json();if(!data.items.length&&{str(bool(basket)).lower()}){{window.location.reload();return;}}if(data.items.some(x=>x.needs_confirmation)){{document.getElementById('hold-names').textContent=data.items.map(x=>x.element_name).join(', ');document.getElementById('hold-modal').hidden=false;}}}}
        document.getElementById('hold-yes').addEventListener('click',async()=>{{await post('/availability/holds/renew');document.getElementById('hold-modal').hidden=true;}});document.getElementById('hold-release').addEventListener('click',async()=>{{await post('/availability/holds/release');window.location.reload();}});setInterval(checkExpiry,5000);
        </script>'''
        return HTMLResponse(layout('Availability Calendar', body, context))
