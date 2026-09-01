from __future__ import annotations

import html
import json
import re
from datetime import date

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .app import COOKIE_NAME, form_data
from .setup015_calculator import _addon_rule
from .setup015_core import one, rows
from .webv1_addon_popup import popup_addons_for_element
from .webv1_booking_requirements import _element_reasons, _requirements_page, _saved_requirements


def _working_context(database, request: Request):
    context = database.session_context(request.cookies.get(COOKIE_NAME))
    if context is None: raise HTTPException(status_code=401, detail='Login required')
    cid = context['acting_company_id'] if context['role'] == 'supervisor' else context['company_id']
    if not cid: raise HTTPException(status_code=403, detail='Select a Client first')
    return context, int(cid)


def _addon_max_anywhere(database, cid: int, addon_id: int) -> int:
    elements = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1', (cid,))
    years = [int(r['year']) for r in rows(database, 'SELECT year FROM setup_years WHERE company_id=? ORDER BY year', (cid,))] or [date.today().year]
    highest = 0; unlimited = False
    for year in years:
        for element in elements:
            rule = _addon_rule(database, cid, year, element, addon_id)
            if not rule.get('allowed'): continue
            maximum = rule.get('max')
            if maximum is None: unlimited = True
            else: highest = max(highest, int(maximum))
    return 99 if unlimited else highest


def _addon_caps(database, cid: int) -> dict[int, int]:
    return {int(a['id']): _addon_max_anywhere(database, cid, int(a['id'])) for a in rows(database, 'SELECT id FROM setup_addons WHERE company_id=? AND active=1 AND ask_before_availability=1', (cid,))}


def _person_features(database, cid: int, year: int, element_id: int) -> list[dict[str, object]]:
    result = []
    for p in rows(database, 'SELECT id,name FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (cid,)):
        limit = one(database, 'SELECT max_count FROM setup_person_limits WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?', (cid, year, element_id, int(p['id'])))
        result.append({'name': str(p['name']), 'available': bool(limit is not None and int(limit['max_count']) > 0), 'kind': 'person'})
    return result


def _transform_requirement_form(text: str, caps: dict[int, int]) -> str:
    text = text.replace('action="/availability/requirements"', 'action="/availability/requirements-v2"')
    cap_json = json.dumps({str(k): int(v) for k, v in caps.items()})
    injection = f'''<style id="requirement-addon-refinement-style">.must-addon-wrap{{display:flex;align-items:center;gap:10px;flex-wrap:wrap}}.must-addon-wrap input[type=number]{{width:90px}}.must-addon-cap{{font-size:12px;color:#66717f}}</style>
<script id="requirement-addon-refinement-script">(()=>{{const caps={cap_json};document.querySelectorAll('input[name^="addon_"]').forEach(input=>{{const id=input.name.slice(6),cap=Number(caps[id]??0),saved=Math.max(0,Math.min(cap,Number(input.value||0)));const wrap=document.createElement('div');wrap.className='must-addon-wrap';const tick=document.createElement('input');tick.type='checkbox';tick.className='must-addon-check';tick.checked=saved>0;tick.disabled=cap<=0;const tickLabel=document.createElement('span');tickLabel.textContent='Required';const qty=document.createElement('input');qty.type='number';qty.name=input.name;qty.min='1';qty.max=String(Math.max(1,cap));qty.value=String(saved||1);qty.disabled=!tick.checked||cap<=1;const hidden=document.createElement('input');hidden.type='hidden';hidden.name=input.name;hidden.value=tick.checked?String(saved||1):'0';const note=document.createElement('span');note.className='must-addon-cap';note.textContent=cap<=0?'Not available on any Element':(cap===1?'Maximum 1':'Maximum '+cap);const sync=()=>{{let n=Math.max(1,Math.min(cap||1,Number(qty.value||1)));qty.value=String(n);hidden.value=tick.checked?String(cap===1?1:n):'0';qty.disabled=!tick.checked||cap<=1;}};tick.addEventListener('change',sync);qty.addEventListener('input',sync);wrap.append(tick,tickLabel,qty,hidden,note);input.replaceWith(wrap);sync();}});}})();</script>'''
    return text.replace('</body>', injection + '</body>', 1)


def _transform_calendar(database, request: Request, text: str, cid: int, token: str) -> str:
    people, addons, ready, _, _ = _saved_requirements(database, cid, token)
    raw_day = request.query_params.get('arrival') or request.query_params.get('start') or date.today().isoformat()
    try: year = date.fromisoformat(raw_day).year
    except ValueError: year = date.today().year

    if not request.query_params.get('element_type') and not request.query_params.get('edit_hold'):
        text = text.replace('<option value="">Select Element Type</option>', '<option value="" selected>Select Element Type</option>', 1)
        held = one(database, 'SELECT id FROM element_holds WHERE company_id=? AND session_token=? LIMIT 1', (cid, token))
        note = '<strong>Held Elements are shown below.</strong> Select an Element Type above to add another.' if held else '<strong>Select an Element Type</strong> to view availability.'
        text = text.replace('<h2>Availability</h2>', f'<h2>Availability</h2><p class="select-element-note">{note}</p>', 1)
    if not ready: return text

    elements = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1', (cid,))
    for element in elements:
        eid = int(element['id']); features = popup_addons_for_element(database, cid, year, element); features.extend(_person_features(database, cid, year, eid))
        feature_json = html.escape(json.dumps(features, ensure_ascii=False), quote=True)
        pattern = re.compile(r'(<div class="cal-row element-row(?: party-unsuitable)?" data-element="' + str(eid) + r'"[^>]*data-features=")([^"]*)(")')
        text = pattern.sub(lambda m: m.group(1) + feature_json + m.group(3), text, count=1)
        btn_pattern = re.compile(r'(<button type="button" class="more-info" data-element-name="[^"]*" data-features=")([^"]*)(")')
        row_pos = text.find(f'data-element="{eid}"')
        if row_pos >= 0:
            row_end = text.find('</div>', row_pos)
            if row_end > row_pos:
                segment = text[row_pos:row_end]; segment = btn_pattern.sub(lambda m: m.group(1) + feature_json + m.group(3), segment, count=1); text = text[:row_pos] + segment + text[row_end:]
        reasons = _element_reasons(database, cid, year, element, people, addons)
        if reasons:
            encoded = html.escape(json.dumps({'element': str(element['name']), 'reasons': reasons, 'features': features}, ensure_ascii=False), quote=True)
            marker = f'<div class="cal-row element-row party-unsuitable" data-element="{eid}"'; text = text.replace(marker, marker + f' data-party-info="{encoded}"', 1)
    return text


def register_booking_requirements_refinement_routes(app) -> None:
    database = app.state.database
    @app.post('/availability/requirements-v2')
    async def requirements_save_v2(request: Request):
        context, cid = _working_context(database, request); token = request.cookies.get(COOKIE_NAME, ''); data = await form_data(request)
        if data.get('csrf') != context['csrf_token']: raise HTTPException(status_code=403, detail='Invalid form token')
        people_rows = rows(database, 'SELECT * FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name', (cid,)); addon_rows = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND active=1 AND ask_before_availability=1 ORDER BY name', (cid,)); parsed_people=[]; total=0
        for p in people_rows:
            pid=int(p['id'])
            try: qty=max(0,int(data.get(f'person_{pid}','0') or 0))
            except ValueError: return HTMLResponse(_requirements_page(database,context,cid,token,f'Enter a valid number for {p["name"]}.'),400)
            total+=qty; ages=[]
            if int(p['ask_age'] or 0):
                for i in range(1,qty+1):
                    try: age=int(str(data.get(f'age_{pid}_{i}','')).strip())
                    except ValueError: return HTMLResponse(_requirements_page(database,context,cid,token,f'Enter the age at arrival for every {p["name"]}.'),400)
                    if age<0 or age>120: return HTMLResponse(_requirements_page(database,context,cid,token,'Age must be between 0 and 120.'),400)
                    ages.append(age)
            parsed_people.append((pid,qty,json.dumps(ages)))
        if people_rows and total<=0: return HTMLResponse(_requirements_page(database,context,cid,token,'Enter at least one person.'),400)
        caps=_addon_caps(database,cid); parsed_addons=[]
        for a in addon_rows:
            aid=int(a['id']); cap=int(caps.get(aid,0)); raw_values=data.getlist(f'addon_{aid}') if hasattr(data,'getlist') else [data.get(f'addon_{aid}','0')]
            try: qty=max(int(v or 0) for v in raw_values)
            except (ValueError,TypeError): return HTMLResponse(_requirements_page(database,context,cid,token,f'Enter a valid quantity for {a["name"]}.'),400)
            if qty<0 or qty>cap: return HTMLResponse(_requirements_page(database,context,cid,token,f'{a["name"]} can be requested up to a maximum of {cap}.'),400)
            parsed_addons.append((aid,qty))
        with database.connect() as c:
            c.execute('DELETE FROM booking_requirement_people WHERE company_id=? AND session_token=?',(cid,token));c.execute('DELETE FROM booking_requirement_addons WHERE company_id=? AND session_token=?',(cid,token))
            for pid,qty,ages in parsed_people:c.execute('INSERT INTO booking_requirement_people(session_token,company_id,person_type_id,quantity,ages_json) VALUES (?,?,?,?,?)',(token,cid,pid,qty,ages))
            for aid,qty in parsed_addons:c.execute('INSERT INTO booking_requirement_addons(session_token,company_id,addon_id,quantity) VALUES (?,?,?,?)',(token,cid,aid,qty))
            c.execute('''INSERT INTO booking_requirement_sessions(session_token,company_id,ready,updated_at) VALUES (?,?,1,CURRENT_TIMESTAMP) ON CONFLICT(session_token,company_id) DO UPDATE SET ready=1,updated_at=CURRENT_TIMESTAMP''',(token,cid))
        return RedirectResponse('/availability/calendar-v2',303)


def install_booking_requirements_refinements(app) -> None:
    database=app.state.database
    @app.middleware('http')
    async def booking_requirements_refinements(request: Request,call_next):
        response=await call_next(request)
        if response.status_code>=500 or 'text/html' not in response.headers.get('content-type',''):return response
        path=request.url.path
        if path not in {'/availability/start','/availability/requirements-v2','/availability/calendar-v2'}:return response
        body=b''
        async for chunk in response.body_iterator:body+=chunk if isinstance(chunk,bytes) else str(chunk).encode('utf-8')
        text=body.decode('utf-8');context=database.session_context(request.cookies.get(COOKIE_NAME))
        if not context:return Response(content=text,status_code=response.status_code,media_type='text/html')
        cid=context['acting_company_id'] if context['role']=='supervisor' else context['company_id']
        if not cid:return Response(content=text,status_code=response.status_code,media_type='text/html')
        cid=int(cid)
        if 'Booking requirements' in text:text=_transform_requirement_form(text,_addon_caps(database,cid))
        if path=='/availability/calendar-v2':text=_transform_calendar(database,request,text,cid,request.cookies.get(COOKIE_NAME,''))
        headers={k:v for k,v in response.headers.items() if k.lower() not in {'content-length','content-type'}}
        return Response(content=text,status_code=response.status_code,headers=headers,media_type='text/html')
