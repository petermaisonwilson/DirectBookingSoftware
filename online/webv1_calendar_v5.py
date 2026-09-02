from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.parse import quote_plus

from fastapi import Request
from fastapi.responses import HTMLResponse

from .app import COOKIE_NAME, esc, layout
from .setup015_core import one, rows
from .webv1_addon_popup import popup_addons_for_element
from .webv1_booking_progress import booking_progress_strip
from .webv1_booking_requirements import _saved_lead_name, _saved_requirements
from .webv1_booking_requirements_core import element_reasons
from .webv1_booking_status import default_status
from .webv1_calendar_v2 import _bar, _cols, _records, _session_context
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
        anchor_arrival = arrival or (str(basket[0]['arrival_date']) if basket else '')
        anchor_departure = departure or (str(basket[0]['departure_date']) if basket else '')

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
            if not selected_type:
                selected_type = str(editing['element_type'])
            if not arrival:
                arrival = str(editing['arrival_date'])
                arrival_day = _parse(arrival)
            if not departure:
                departure = str(editing['departure_date'])
                departure_day = _parse(departure)

        people, requirements, requirements_ready, _, _ = _saved_requirements(database, cid, token)
        working_lead = _saved_lead_name(database, cid, token).strip()

        options = '<option value="">Select Element Type</option>' + ''.join(
            f'<option value="{esc(t)}" {"selected" if t == selected_type else ""}>{esc(t)}</option>' for t in types
        )
        preserve = f'element_type={quote_plus(selected_type)}&arrival={quote_plus(arrival)}&departure={quote_plus(departure)}'
        if edit_hold:
            preserve += f'&edit_hold={edit_hold}'

        body = booking_progress_strip(database, context, cid, token) + '<h1>Availability Calendar</h1>'

        if requirements_ready:
            summary = []
            for pid, data in people.items():
                qty = int(data.get('quantity', 0))
                if qty:
                    p = one(database, 'SELECT name FROM setup_person_types WHERE company_id=? AND id=?', (cid, pid))
                    label = f'{qty} {str(p["name"]) if p else "person"}'
                    ages = data.get('ages', [])
                    if ages:
                        label += ' (age' + ('s ' if len(ages) != 1 else ' ') + ', '.join(str(x) for x in ages) + ')'
                    summary.append(label)
            for aid, qty in requirements.items():
                if int(qty):
                    a = one(database, 'SELECT name FROM setup_addons WHERE company_id=? AND id=?', (cid, aid))
                    summary.append(f'{str(a["name"]) if a else "Requirement"} {int(qty)}')
            summary_html = ' · '.join(esc(x) for x in summary) or 'No special requirements'
            name_html = f'<strong>Name:</strong> {esc(working_lead)} &nbsp; ' if working_lead else ''
            type_html = f'<strong>Element Type:</strong> {esc(selected_type)} &nbsp; ' if selected_type else ''
            q = ''
            if edit_hold:
                q = f'?edit_hold={int(edit_hold)}'
            if selected_type:
                q += ('&' if q else '?') + 'element_type=' + quote_plus(selected_type)
            body += f'<div class="card requirement-summary">{name_html}{type_html}<strong>Your requirements:</strong> {summary_html} <a class="button secondary" style="margin-left:10px" href="/availability/start{q}">CHANGE REQUIREMENTS</a></div>'

        body += f'''<div class="card"><form id="availability-form" method="get" action="/availability/calendar-v2">
        <input type="hidden" id="edit-hold" name="edit_hold" value="{edit_hold or ''}"><input type="hidden" id="calendar-start" name="start" value="{visible_start.isoformat()}">
        <div class="grid"><div><label>Add/Change Element</label><select id="element-type" name="element_type">{options}</select></div>
        <div><label>Arrival</label><input id="arrival-date" type="date" name="arrival" value="{esc(arrival)}"></div>
        <div><label>Departure</label><input id="departure-date" type="date" name="departure" value="{esc(departure)}"></div></div><p>
        <a class="button secondary" href="/availability/calendar-v2?{preserve}&start={(visible_start-timedelta(days=14)).isoformat()}">← Previous 14 days</a>
        <a class="button secondary" href="/availability/calendar-v2?{preserve}&start={(visible_start+timedelta(days=14)).isoformat()}">Next 14 days →</a>
        {'<a class="button secondary" href="/setup/booking-statuses">Booking Statuses</a>' if staff else ''}</p></form></div>'''

        if editing:
            edit_name = str(editing['lead_name'] or '').strip() or str(editing['element_name'])
            current_type = str(editing['element_type'])
            changed_type = selected_type and selected_type != current_type
            type_note = f' You are now viewing <strong>{esc(selected_type)}</strong>; your current <strong>{esc(editing["element_name"])}</strong> ({esc(current_type)}) remains held until you choose a replacement.' if changed_type else f' Your current held Element is <strong>{esc(editing["element_name"])}</strong>.'
            body += f'<div class="card edit-notice"><strong>Editing {esc(edit_name)}</strong>.{type_note} Every Element of the selected type is shown below. Suitable Elements can be selected; unsuitable Elements remain visible with the reason why. <a class="button secondary" style="margin-left:8px" href="/availability/start?edit_hold={int(edit_hold)}&element_type={quote_plus(selected_type)}">BACK TO REQUIREMENTS</a></div>'

        held_status = default_status(database, cid, 'HELD')
        held_colour = str(held_status['colour']) if held_status else '#FFE39A'
        held_name = str(held_status['name']) if held_status else 'Enquiry / Held'
        if staff:
            status_rows = rows(database, 'SELECT * FROM booking_status_definitions WHERE company_id=? AND active=1 ORDER BY display_order,name', (cid,))
            status_legend = ''.join(f'<span class="legend mini" style="background:{esc(r["colour"])}">{esc(r["name"])}</span>' for r in status_rows)
        else:
            status_legend = '<span class="legend mini unavailable">Unavailable</span>'
        unsuitable_legend = '<span class="legend mini party-unsuitable-key">Not suitable for your requirements</span>' if requirements_ready else ''
        legend = f'<span class="legend mini available-key">Available</span>{unsuitable_legend}{status_legend}<span class="legend mini own-held">In this booking</span><span class="legend mini closed">Closed</span>'

        cal_header = _header(dates, arrival_day, departure_day)
        cal_rows = ''
        if selected_type:
            elements = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1 AND element_type=? ORDER BY name COLLATE NOCASE', (cid, selected_type))
        elif basket:
            basket_ids = list(dict.fromkeys(int(r['element_id']) for r in basket))
            marks = ','.join('?' for _ in basket_ids)
            elements = rows(database, f'SELECT * FROM setup_elements WHERE company_id=? AND active=1 AND id IN ({marks}) ORDER BY element_type,name COLLATE NOCASE', (cid, *basket_ids))
        else:
            elements = []
        feature_year = (_parse(arrival) or _parse(anchor_arrival) or visible_start).year
        locked_holds = {
            int(r['element_id']): r for r in basket
            if not editing or int(r['id']) != int(edit_hold)
        }

        for element in elements:
            eid = int(element['id'])
            reasons = element_reasons(database, cid, feature_year, element, people, requirements) if requirements_ready else []
            party_unsuitable = bool(reasons)
            same_party_hold = next((r for r in basket if int(r['element_id']) == eid and working_lead and str(r['lead_name'] or '').strip() == working_lead), None)
            held_now_unsuitable = bool(party_unsuitable and same_party_hold)

            features = popup_addons_for_element(database, cid, feature_year, element)
            feature_json = esc(json.dumps(features, ensure_ascii=False))
            bookings, enquiries, closures, holds = _records(database, cid, eid, visible_start, visible_end)
            locked_item = locked_holds.get(eid)
            locked_in_basket = locked_item is not None
            cells = ''
            selected_range_available = bool(
                arrival_day and departure_day and departure_day > arrival_day
                and arrival_day >= visible_start and departure_day <= visible_end
                and not locked_in_basket and not party_unsuitable
            )

            for i, day in enumerate(dates):
                next_day = day + timedelta(days=1)
                state = availability_state(database, cid, eid, day.isoformat(), next_day.isoformat(), session_token=token)
                code = str(state['state'])
                own_editing_day = bool(
                    editing and eid == int(editing['element_id'])
                    and _parse(str(editing['arrival_date'])) <= day < _parse(str(editing['departure_date']))
                )
                selected = ' selected-date' if arrival_day and departure_day and arrival_day <= day < departure_day else (' selected-start' if arrival_day == day else '')
                if arrival_day and departure_day and arrival_day <= day < departure_day and code != 'AVAILABLE' and not own_editing_day:
                    selected_range_available = False
                col = i + 2

                if code == 'AVAILABLE' and party_unsuitable:
                    cells += f'<span class="cal-cell unsuitable{selected}" style="grid-column:{col};grid-row:1" data-date="{day.isoformat()}" data-element="{eid}" data-name="{esc(element["name"])}"></span>'
                elif code == 'AVAILABLE' and not locked_in_basket:
                    cells += f'<button type="button" class="cal-cell available date-pick{selected}" style="grid-column:{col};grid-row:1" data-date="{day.isoformat()}" data-element="{eid}" data-name="{esc(element["name"])}"></button>'
                elif code == 'AVAILABLE':
                    cells += f'<button type="button" class="cal-cell available basket-locked{selected}" style="grid-column:{col};grid-row:1" data-date="{day.isoformat()}" data-element="{eid}" data-name="{esc(element["name"])}"></button>'
                elif code == 'HELD_BY_YOU':
                    if held_now_unsuitable:
                        cells += f'<span class="cal-cell own-held now-unsuitable{selected}" style="grid-column:{col};grid-row:1" data-date="{day.isoformat()}" data-element="{eid}" data-name="{esc(element["name"])}"></span>'
                    else:
                        cls = ' editable-own date-pick' if own_editing_day else ''
                        tag = 'button type="button"' if own_editing_day else 'span'
                        endtag = 'button' if own_editing_day else 'span'
                        cells += f'<{tag} class="cal-cell own-held{cls}{selected}" style="grid-column:{col};grid-row:1" data-date="{day.isoformat()}" data-element="{eid}" data-name="{esc(element["name"])}"></{endtag}>'
                elif code in {'BOOKED', 'ENQUIRY', 'HELD'}:
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

            if held_now_unsuitable:
                reason_html = f'<small class="held-unsuitable-warning"><strong>CURRENTLY HELD — NOW UNSUITABLE</strong>: {esc(" · ".join(reasons))}</small>'
            elif party_unsuitable:
                reason_html = f'<small class="party-reason">{esc("Not suitable: " + " · ".join(reasons))}</small>'
            else:
                reason_html = ''
            name_html = (
                f'<button type="button" class="element-info-title" data-element-name="{esc(element["name"])}" data-features="{feature_json}"><strong>{esc(element["name"])}</strong></button>'
                f'<button type="button" class="more-info" data-element-name="{esc(element["name"])}" data-features="{feature_json}">More info</button>{reason_html}'
            )
            selection_style = ''
            selection_hidden = ' hidden'
            if selected_range_available and arrival_day and departure_day:
                selection_start = (arrival_day - visible_start).days + 2
                selection_end = (departure_day - visible_start).days + 2
                selection_style = f' style="grid-column:{selection_start} / {selection_end};grid-row:1"'
                selection_hidden = ''
            selection = f'<button type="button" class="selection-action" data-element="{eid}" data-name="{esc(element["name"])}"{selection_style}{selection_hidden}>{"USE THIS ELEMENT" if edit_hold else "RESERVE"}</button>'
            row_classes = 'cal-row element-row'
            if party_unsuitable:
                row_classes += ' party-unsuitable'
            if held_now_unsuitable:
                row_classes += ' held-now-unsuitable'
            cal_rows += f'<div class="{row_classes}" data-element="{eid}" data-name="{esc(element["name"])}" data-features="{feature_json}"><div class="cal-name" style="grid-column:1;grid-row:1">{name_html}</div>{cells}{bars}{selection}</div>'

        if not cal_rows:
            cal_rows = '<div class="card"><p>No active Elements exist for this Element Type.</p></div>' if selected_type else '<div class="card"><p>Select an Element Type to see matching Elements.</p></div>'

        body += f'''<div class="card availability-card"><div class="availability-head"><h2>Availability</h2><div class="legend-row compact"><strong>Key:</strong>{legend}</div></div>
        <div id="calendar-scroll" class="calendar-scroll"><div class="calendar-grid" style="--days:{display_days}">{cal_header}{cal_rows}</div></div>
        <p class="muted">Every Element of the selected type is shown. Pale green days are available. Elements that do not meet the booking requirements remain visible with the reason why, so you can change the requirements if you want a particular Element. A yellow Element remains protected while held; if revised requirements make it unsuitable it is clearly marked and must be replaced before proceeding.</p></div>'''

        body += f'''<div id="quick-element-info" class="quick-element-info" hidden></div>
        <div id="info-modal" class="hold-modal" hidden><div class="hold-dialog"><button id="info-close" type="button" class="secondary close-info">Close</button><h2 id="info-title"></h2><p class="muted">Element information</p><div id="info-features"></div></div></div>
        <style>
        .calendar-scroll{{overflow:auto;max-height:520px;border:1px solid #d6dde5;border-radius:8px;position:relative;scrollbar-gutter:stable both-edges}}
        .calendar-grid{{min-width:calc(190px + (var(--days) * 48px));background:white;position:relative}}
        .cal-row{{display:grid;grid-template-columns:190px repeat(var(--days),48px);grid-template-rows:52px;width:calc(190px + (var(--days) * 48px));min-width:calc(190px + (var(--days) * 48px));position:relative;height:52px;border-bottom:2px solid #fff;box-sizing:border-box;overflow:visible}}
        .element-row .cal-cell{{height:50px;box-sizing:border-box}} #calendar-scroll .element-row>.cal-name{{position:sticky!important;left:0!important;grid-column:1!important;z-index:50!important;height:50px;background:#fff!important}}
        .element-row.party-unsuitable{{grid-template-rows:68px;height:68px}} .element-row.party-unsuitable .cal-cell{{height:66px}} .element-row.party-unsuitable .cal-name{{height:66px!important}}
        .cal-head{{background:#f4f6f8;position:sticky;top:0;z-index:60}} .cal-name{{padding:7px;position:sticky;left:0;z-index:50;width:190px;box-sizing:border-box;background:white;border-right:2px solid #c5cdd6}}
        .element-info-title{{display:block;border:0;background:none;color:#1f2937;padding:0;margin:0;text-align:left;font:inherit;cursor:help}} .cal-name .more-info{{border:0;background:none;color:#365f86;padding:2px 0;font-size:12px;text-decoration:underline}}
        .cal-head .cal-name{{background:#f4f6f8!important;z-index:70!important}} .cal-date{{padding:6px 2px;text-align:center;border-right:1px solid #e5e9ee;font-size:12px;background:#f4f6f8}} .cal-date small{{display:block;color:#66717f}}
        .cal-cell{{border:0;border-right:1px solid rgba(255,255,255,.6);display:block;z-index:1;padding:0;margin:0}} button.cal-cell{{cursor:pointer}} .cal-cell.available{{background:#dff2df}} .cal-cell.unavailable{{background:#f6d6d9}} .cal-cell.own-held{{background:#ffe39a}} .cal-cell.closed{{background:#e1e4e8}} .cal-cell.unsuitable{{background:#eadcf4;cursor:not-allowed}}
        .element-row.held-now-unsuitable .cal-cell.own-held{{outline:3px solid #b42318;outline-offset:-3px}} .held-unsuitable-warning{{display:block;color:#b42318;font-size:10px;line-height:1.15;margin-top:2px}} .party-reason{{display:block;color:#6d3f7c;font-size:10px;line-height:1.15;margin-top:2px}}
        .party-unsuitable-key{{background:#eadcf4}} .cal-cell.basket-locked{{cursor:help}} .cal-cell.selected-date,.cal-date.selected-date{{box-shadow:inset 0 0 0 2px #6d8196}}
        .selection-action{{z-index:7;align-self:center;height:34px;margin:0 2px;padding:4px 12px;border-radius:6px;font-weight:bold}} .selection-action[hidden]{{display:none!important}}
        .availability-head{{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap}} .availability-head h2{{margin:0}} .legend-row{{display:flex;gap:6px;flex-wrap:wrap;align-items:center}} .legend.mini{{padding:2px 6px;border-radius:4px;font-size:11px}} .available-key{{background:#dff2df}} .own-held{{background:#ffe39a}} .legend.closed{{background:#e1e4e8}}
        .cal-bar{{z-index:4;align-self:center;height:30px;margin:0 2px;border-radius:5px;overflow:hidden;white-space:nowrap;display:flex;align-items:center}} .cal-bar a,.cal-bar span{{display:block;padding:6px 8px;color:#26313d;text-decoration:none;width:100%;overflow:hidden;text-overflow:ellipsis}}
        .hold-modal{{position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:100;display:flex;align-items:center;justify-content:center}} .hold-modal[hidden]{{display:none}} .hold-dialog{{background:white;padding:24px;border-radius:10px;max-width:620px;width:90%;max-height:80vh;overflow:auto}}
        .quick-element-info{{position:fixed;z-index:120;background:white;border:1px solid #8293a4;border-radius:6px;padding:10px 12px;box-shadow:0 4px 14px rgba(0,0,0,.2);font-size:12px;pointer-events:none;max-width:320px}}
        .quick-element-info ul{{margin:6px 0 0;padding-left:18px}}
        </style>
        <script>
        const csrf={json.dumps(str(context['csrf_token']))}; const editingHold={int(edit_hold or 0)}; const anchorArr={json.dumps(anchor_arrival)},anchorDep={json.dumps(anchor_departure)};
        const elementType=document.getElementById('element-type'),arrivalInput=document.getElementById('arrival-date'),departureInput=document.getElementById('departure-date'),scrollBox=document.getElementById('calendar-scroll');
        let selectedElement=0,firstPick='';
        const dayAfter=iso=>{{const d=new Date(iso+'T12:00:00');d.setDate(d.getDate()+1);return d.toISOString().slice(0,10)}};
        function qsFor(a,d){{const q=new URLSearchParams();if(elementType.value)q.set('element_type',elementType.value);if(a)q.set('arrival',a);if(d)q.set('departure',d);if(editingHold)q.set('edit_hold',editingHold);return q}}
        function submitDates(){{const a=arrivalInput.value,d=departureInput.value;if(!a||!d||d<=a)return;window.location='/availability/calendar-v2?'+qsFor(a,d)}}
        elementType.addEventListener('change',()=>{{window.location='/availability/calendar-v2?'+qsFor(arrivalInput.value||anchorArr,departureInput.value||anchorDep)}});
        arrivalInput.addEventListener('change',()=>{{if(!arrivalInput.value)return;const n=dayAfter(arrivalInput.value);departureInput.min=n;departureInput.value=n;submitDates()}}); departureInput.addEventListener('change',submitDates);
        function clearBars(){{document.querySelectorAll('.selection-action').forEach(x=>x.hidden=true)}}
        function showSelection(eid,a,d){{clearBars();const row=document.querySelector('.element-row[data-element="'+eid+'"]');if(!row||row.classList.contains('party-unsuitable'))return;const ds=[...document.querySelectorAll('#calendar-scroll .cal-date')].map(x=>x.dataset.date),s=ds.indexOf(a),e=ds.indexOf(d);if(s<0||e<=s)return;const b=row.querySelector('.selection-action');b.style.gridColumn=(s+2)+' / '+(e+2);b.style.gridRow='1';b.hidden=false;selectedElement=Number(eid)}}
        document.querySelectorAll('.date-pick').forEach(cell=>cell.addEventListener('click',()=>{{const chosen=cell.dataset.date,eid=Number(cell.dataset.element);if(!firstPick||selectedElement!==eid){{firstPick=chosen;selectedElement=eid;arrivalInput.value=chosen;departureInput.value='';clearBars();return}}if(chosen<=firstPick){{firstPick=chosen;arrivalInput.value=chosen;departureInput.value='';return}}arrivalInput.value=firstPick;departureInput.value=chosen;showSelection(eid,firstPick,chosen);firstPick=''}}));
        async function post(url,data={{}}){{const body=new URLSearchParams();body.set('csrf',csrf);Object.entries(data).forEach(([k,v])=>body.set(k,v));return fetch(url,{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'}},body:body.toString()}})}}
        document.querySelectorAll('.selection-action').forEach(btn=>btn.addEventListener('click',async()=>{{const a=arrivalInput.value,d=departureInput.value;if(!a||!d)return;const url=editingHold?'/availability/basket/update':'/availability/hold',data=editingHold?{{hold_id:editingHold,element_id:btn.dataset.element,arrival_date:a,departure_date:d}}:{{element_id:btn.dataset.element,arrival_date:a,departure_date:d}};const r=await post(url,data);let p={{}};try{{p=await r.json()}}catch(e){{}}if(!r.ok){{alert(p.error||'Unable to save that Element.');return}}if(editingHold){{window.location='/availability/basket/review';return}}const q=new URLSearchParams();q.set('element_type',elementType.value);q.set('arrival',a);q.set('departure',d);window.location='/availability/calendar-v2?'+q.toString()}}));
        function featureHtml(features){{return '<ul>'+features.map(f=>'<li>'+(f.available?'✓ ':'✕ ')+f.name+'</li>').join('')+'</ul>'}}
        const info=document.getElementById('info-modal'); function showFullInfo(btn){{document.getElementById('info-title').textContent=btn.dataset.elementName;document.getElementById('info-features').innerHTML=featureHtml(JSON.parse(btn.dataset.features||'[]'));info.hidden=false}}
        document.querySelectorAll('.more-info').forEach(btn=>btn.addEventListener('click',()=>showFullInfo(btn))); document.getElementById('info-close').addEventListener('click',()=>info.hidden=true);
        const quick=document.getElementById('quick-element-info'); document.querySelectorAll('.element-info-title').forEach(btn=>{{btn.addEventListener('mouseenter',e=>{{const features=JSON.parse(btn.dataset.features||'[]');quick.innerHTML='<strong>'+btn.dataset.elementName+'</strong>'+featureHtml(features);quick.hidden=false;quick.style.left=Math.min(innerWidth-340,e.clientX+12)+'px';quick.style.top=Math.min(innerHeight-220,e.clientY+12)+'px'}});btn.addEventListener('mousemove',e=>{{if(!quick.hidden){{quick.style.left=Math.min(innerWidth-340,e.clientX+12)+'px';quick.style.top=Math.min(innerHeight-220,e.clientY+12)+'px'}}}});btn.addEventListener('mouseleave',()=>quick.hidden=true)}});
        document.querySelectorAll('.basket-locked').forEach(cell=>{{const show=e=>{{quick.innerHTML='<strong>'+((cell.dataset.name)||'This Element')+'</strong><div>Already in your basket — use EDIT in Booking in progress to change it. Click the EDIT BOOKING button.</div>';quick.hidden=false;quick.style.left=Math.min(innerWidth-340,e.clientX+12)+'px';quick.style.top=Math.min(innerHeight-160,e.clientY+12)+'px'}};cell.addEventListener('mouseenter',show);cell.addEventListener('mousemove',show);cell.addEventListener('mouseleave',()=>quick.hidden=true);}});
        </script>'''
        return HTMLResponse(layout('Availability Calendar', body, context))