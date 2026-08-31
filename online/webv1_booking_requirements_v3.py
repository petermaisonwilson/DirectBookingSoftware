from __future__ import annotations
import json
from datetime import date
from urllib.parse import quote_plus
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse,RedirectResponse
from .app import COOKIE_NAME,form_data
from .setup015_core import rows
from .webv1_booking_requirements import _requirements_page,_snapshot_hold_requirements
from .webv1_booking_requirements_refinements import _addon_caps,_working_context
from .webv1_ordering import person_type_rows

def register_booking_requirements_v3(app):
    database=app.state.database
    @app.post('/availability/requirements-v3')
    async def save(request:Request):
        context,cid=_working_context(database,request);token=request.cookies.get(COOKIE_NAME,'');data=await form_data(request)
        if data.get('csrf')!=context['csrf_token']:raise HTTPException(status_code=403,detail='Invalid form token')
        raw=str(data.get('edit_hold','') or '').strip();edit=int(raw) if raw.isdigit() and int(raw)>0 else 0;lead=str(data.get('lead_name','') or '').strip()
        if not lead:return HTMLResponse(_requirements_page(database,context,cid,token,'Please enter the lead name.',edit_hold=edit),400)
        arrival=str(data.get('arrival','')).strip();departure=str(data.get('departure','')).strip()
        try:a=date.fromisoformat(arrival);d=date.fromisoformat(departure)
        except ValueError:return HTMLResponse(_requirements_page(database,context,cid,token,'Enter valid arrival and departure dates.',edit_hold=edit),400)
        if d<=a:return HTMLResponse(_requirements_page(database,context,cid,token,'Departure must be after arrival.',edit_hold=edit),400)
        people=person_type_rows(database,cid,active_only=True);addons=rows(database,'SELECT * FROM setup_addons WHERE company_id=? AND active=1 AND ask_before_availability=1 ORDER BY lower(name)',(cid,));parsed_people=[];total=0
        for p in people:
            pid=int(p['id']);qty=max(0,int(data.get(f'person_{pid}','0') or 0));total+=qty;ages=[]
            if int(p['ask_age'] or 0):
                for i in range(1,qty+1):ages.append(int(str(data.get(f'age_{pid}_{i}','')).strip()))
            parsed_people.append((pid,qty,json.dumps(ages)))
        if people and total<=0:return HTMLResponse(_requirements_page(database,context,cid,token,'Enter at least one person.',edit_hold=edit),400)
        caps=_addon_caps(database,cid);parsed_addons=[]
        for item in addons:
            aid=int(item['id']);qty=max(0,int(data.get(f'addon_{aid}','0') or 0))
            if qty>int(caps.get(aid,0)):return HTMLResponse(_requirements_page(database,context,cid,token,f'{item["name"]} exceeds the maximum.',edit_hold=edit),400)
            parsed_addons.append((aid,qty))
        with database.connect() as c:
            c.execute('DELETE FROM booking_requirement_people WHERE company_id=? AND session_token=?',(cid,token));c.execute('DELETE FROM booking_requirement_addons WHERE company_id=? AND session_token=?',(cid,token))
            for pid,qty,ages in parsed_people:c.execute('INSERT INTO booking_requirement_people(session_token,company_id,person_type_id,quantity,ages_json) VALUES (?,?,?,?,?)',(token,cid,pid,qty,ages))
            for aid,qty in parsed_addons:c.execute('INSERT INTO booking_requirement_addons(session_token,company_id,addon_id,quantity) VALUES (?,?,?,?)',(token,cid,aid,qty))
            c.execute('INSERT INTO booking_requirement_sessions(session_token,company_id,ready,arrival_date,departure_date,lead_name,updated_at) VALUES (?,?,1,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(session_token,company_id) DO UPDATE SET ready=1,arrival_date=excluded.arrival_date,departure_date=excluded.departure_date,lead_name=excluded.lead_name,updated_at=CURRENT_TIMESTAMP',(token,cid,arrival,departure,lead))
        if edit:
            _snapshot_hold_requirements(database,cid,token,edit)
            with database.connect() as c:c.execute('UPDATE element_holds SET lead_name=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND company_id=? AND session_token=?',(lead,edit,cid,token));item=c.execute('SELECT e.element_type FROM element_holds h JOIN setup_elements e ON e.id=h.element_id AND e.company_id=h.company_id WHERE h.id=? AND h.company_id=? AND h.session_token=?',(edit,cid,token)).fetchone()
            return RedirectResponse(f'/availability/calendar-v2?element_type={quote_plus(str(item["element_type"])) if item else ""}&arrival={arrival}&departure={departure}&edit_hold={edit}',303)
        return RedirectResponse(f'/availability/calendar-v2?arrival={arrival}&departure={departure}',303)

def install_booking_requirements_v3_form(app) -> None:
    @app.middleware('http')
    async def booking_requirements_v3_form(request, call_next):
        response = await call_next(request)
        if request.url.path != '/availability/start' or response.status_code >= 400 or 'text/html' not in response.headers.get('content-type', ''):
            return response
        body = b''
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else str(chunk).encode('utf-8')
        text = body.decode('utf-8')
        headers = {k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'content-type'}}
        text = text.replace('action="/availability/requirements-v2"', 'action="/availability/requirements-v3"', 1)
        from fastapi.responses import Response
        return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
