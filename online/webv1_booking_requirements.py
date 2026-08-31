from __future__ import annotations
import json
from datetime import date
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .app import COOKIE_NAME, esc, form_data, layout
from .setup015_calculator import _addon_rule
from .setup015_core import audit, context_for, one, require_csrf, rows, working_company
from .webv1_booking_progress import booking_progress_strip
from .webv1_ordering import person_type_rows

def initialise_booking_requirements(database):
    with database.connect() as c:c.execute('DELETE FROM hold_requirement_people WHERE hold_id NOT IN (SELECT id FROM element_holds)');c.execute('DELETE FROM hold_requirement_addons WHERE hold_id NOT IN (SELECT id FROM element_holds)')
def _session_context(database,request):
    context=database.session_context(request.cookies.get(COOKIE_NAME));
    if context is None:raise HTTPException(status_code=401,detail='Login required')
    cid=context['acting_company_id'] if context['role']=='supervisor' else context['company_id']
    if not cid:raise HTTPException(status_code=403,detail='Select a Client first')
    return context,int(cid)
def _saved_requirements(database,cid,token):
    people={int(r['person_type_id']):{'quantity':int(r['quantity']),'ages':json.loads(r['ages_json'] or '[]')} for r in rows(database,'SELECT * FROM booking_requirement_people WHERE company_id=? AND session_token=?',(cid,token))};addons={int(r['addon_id']):int(r['quantity']) for r in rows(database,'SELECT * FROM booking_requirement_addons WHERE company_id=? AND session_token=?',(cid,token))};saved=one(database,'SELECT ready,arrival_date,departure_date FROM booking_requirement_sessions WHERE company_id=? AND session_token=?',(cid,token));return people,addons,bool(saved and int(saved['ready'] or 0)),str(saved['arrival_date'] or '') if saved else '',str(saved['departure_date'] or '') if saved else ''
def _saved_lead_name(database,cid,token):saved=one(database,'SELECT lead_name FROM booking_requirement_sessions WHERE company_id=? AND session_token=?',(cid,token));return str(saved['lead_name'] or '') if saved else ''
def _load_hold_requirements_into_working(database,cid,token,hold_id):
    with database.connect() as c:
        hold=c.execute('SELECT id,arrival_date,departure_date,lead_name FROM element_holds WHERE id=? AND company_id=? AND session_token=?',(hold_id,cid,token)).fetchone()
        if hold is None:return False
        c.execute('DELETE FROM booking_requirement_people WHERE company_id=? AND session_token=?',(cid,token));c.execute('DELETE FROM booking_requirement_addons WHERE company_id=? AND session_token=?',(cid,token));c.execute('INSERT INTO booking_requirement_people(session_token,company_id,person_type_id,quantity,ages_json) SELECT ?,company_id,person_type_id,quantity,ages_json FROM hold_requirement_people WHERE hold_id=?',(token,hold_id));c.execute('INSERT INTO booking_requirement_addons(session_token,company_id,addon_id,quantity) SELECT ?,company_id,addon_id,quantity FROM hold_requirement_addons WHERE hold_id=?',(token,hold_id));c.execute('''INSERT INTO booking_requirement_sessions(session_token,company_id,ready,arrival_date,departure_date,lead_name,updated_at) VALUES (?,?,1,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(session_token,company_id) DO UPDATE SET ready=1,arrival_date=excluded.arrival_date,departure_date=excluded.departure_date,lead_name=excluded.lead_name,updated_at=CURRENT_TIMESTAMP''',(token,cid,hold['arrival_date'],hold['departure_date'],hold['lead_name'] or ''))
    return True
def _fmt_user_date(value):
    try:return date.fromisoformat(value).strftime('%d/%m/%Y')
    except (TypeError,ValueError):return value or ''
def _requirements_page(database,context,cid,token,message='',edit_hold=0):
    people_rows=person_type_rows(database,cid,active_only=True);addon_rows=rows(database,'SELECT * FROM setup_addons WHERE company_id=? AND active=1 AND ask_before_availability=1 ORDER BY lower(name)',(cid,));saved_people,saved_addons,_,saved_arrival,saved_departure=_saved_requirements(database,cid,token);lead=_saved_lead_name(database,cid,token);error=f'<div class="error">{esc(message)}</div>' if message else '';progress=booking_progress_strip(database,context,cid,token);edit_hidden=f'<input type="hidden" name="edit_hold" value="{int(edit_hold)}">' if edit_hold else '';edit_note='<div class="card edit-notice"><strong>Editing this basket item’s requirements.</strong> Saving will update only this held Element and return you to it on the Availability Calendar.</div>' if edit_hold else '';body=f'''<h1>Booking requirements</h1>{progress}{edit_note}{error}<div class="card"><p>Tell us who is coming, when they are staying and anything that the Element <strong>must</strong> provide. We use this only to prevent you choosing an unsuitable Element.</p><p class="muted">For privacy, age is requested only for Person Types where the Client has enabled <strong>Ask for age</strong>. Date of birth is not collected.</p></div><form method="post" action="/availability/requirements">{edit_hidden}<input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><div class="card"><h2>Who's coming and when?</h2><div class="grid"><div><label>Please enter the lead name</label><input name="lead_name" placeholder="NAME" required value="{esc(lead)}"></div><div><label>Arrival</label><input id="requirements-arrival" type="date" name="arrival" required value="{esc(saved_arrival)}"></div><div><label>Departure</label><input id="requirements-departure" type="date" name="departure" required value="{esc(saved_departure)}"></div>'''
    for p in people_rows:
        pid=int(p['id']);saved=saved_people.get(pid,{'quantity':0,'ages':[]});body+=f'<div class="requirement-person"><label>{esc(p["name"])}</label><input class="person-qty" data-person="{pid}" data-ask-age="{1 if int(p["ask_age"] or 0) else 0}" type="number" min="0" max="99" name="person_{pid}" value="{int(saved["quantity"])}">'
        if int(p['ask_age'] or 0):body+=f'<div class="age-fields" id="ages-{pid}" data-existing="{esc(json.dumps(saved["ages"]))}"></div>'
        body+='</div>'
    body+='</div></div>'
    if addon_rows:
        body+='<div class="card"><h2>Must-have requirements</h2><p class="muted">Only Features or Extras marked “Ask before Availability” appear here.</p><div class="grid">'
        for a in addon_rows:
            aid=int(a['id']);body+=f'<div><label>{esc(a["name"])}</label><input type="number" min="0" max="99" name="addon_{aid}" value="{int(saved_addons.get(aid,0))}"><small class="muted">0 = not required</small></div>'
        body+='</div></div>'
    body+='''<p><button type="submit">SEARCH AVAILABILITY</button></p></form><script>(()=>{const arr=document.getElementById('requirements-arrival'),dep=document.getElementById('requirements-departure');const next=(iso)=>{const d=new Date(iso+'T12:00:00');d.setDate(d.getDate()+1);return d.toISOString().slice(0,10)};if(arr&&dep)arr.addEventListener('change',()=>{if(!arr.value)return;const n=next(arr.value);dep.min=n;dep.value=n;});function draw(input){if(input.dataset.askAge!=='1')return;const box=document.getElementById('ages-'+input.dataset.person);if(!box)return;const qty=Math.max(0,Number(input.value||0));let existing=[];try{existing=JSON.parse(box.dataset.existing||'[]')}catch(e){}const current=[...box.querySelectorAll('input')].map(x=>x.value);box.innerHTML='';for(let i=0;i<qty;i++){const label=document.createElement('label');label.textContent='Age at arrival — '+(i+1);const age=document.createElement('input');age.type='number';age.min='0';age.max='120';age.required=true;age.name='age_'+input.dataset.person+'_'+(i+1);age.value=current[i]??existing[i]??'';box.append(label,age);}box.dataset.existing='[]';}document.querySelectorAll('.person-qty').forEach(i=>{draw(i);i.addEventListener('input',()=>draw(i));});})();</script>''';return layout('Booking requirements',body,context)
def _element_reasons(database,cid,year,element,people,addons):
    reasons=[];total=sum(int(v.get('quantity',0)) for v in people.values());occupancy=one(database,'SELECT max_total FROM setup_occupancy WHERE company_id=? AND year=? AND element_id=?',(cid,year,int(element['id'])))
    if occupancy is None:reasons.append('occupancy setup incomplete')
    elif total>int(occupancy['max_total']):reasons.append(f'maximum occupancy {int(occupancy["max_total"])}')
    for pid,data in people.items():
        qty=int(data.get('quantity',0));
        if qty<=0:continue
        p=one(database,'SELECT name FROM setup_person_types WHERE company_id=? AND id=?',(cid,pid));limit=one(database,'SELECT max_count FROM setup_person_limits WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?',(cid,year,int(element['id']),pid));name=str(p['name']) if p else 'Person type'
        if limit is None:reasons.append(f'{name} not configured')
        elif qty>int(limit['max_count']):reasons.append(f'{name} not allowed' if int(limit['max_count'])==0 else f'{name} max {int(limit["max_count"])}')
    for aid,qty in addons.items():
        if int(qty)<=0:continue
        addon=one(database,'SELECT name FROM setup_addons WHERE company_id=? AND id=?',(cid,aid));rule=_addon_rule(database,cid,year,element,aid);name=str(addon['name']) if addon else 'Requirement'
        if not rule['allowed']:reasons.append(f'no {name}')
        elif rule['max'] is not None and int(qty)>int(rule['max']):reasons.append(f'{name} max {int(rule["max"])}')
    return reasons
def _snapshot_hold_requirements(database,cid,token,hold_id):
    with database.connect() as c:c.execute('DELETE FROM hold_requirement_people WHERE hold_id=?',(hold_id,));c.execute('DELETE FROM hold_requirement_addons WHERE hold_id=?',(hold_id,));c.execute('INSERT INTO hold_requirement_people(hold_id,company_id,person_type_id,quantity,ages_json) SELECT ?,company_id,person_type_id,quantity,ages_json FROM booking_requirement_people WHERE company_id=? AND session_token=?',(hold_id,cid,token));c.execute('INSERT INTO hold_requirement_addons(hold_id,company_id,addon_id,quantity) SELECT ?,company_id,addon_id,quantity FROM booking_requirement_addons WHERE company_id=? AND session_token=?',(hold_id,cid,token))
def register_booking_requirement_routes(app):
    database=app.state.database;app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path',None)!='/availability/hold']
    @app.get('/availability/start',response_class=HTMLResponse)
    def requirements_start(request:Request):
        context,cid=_session_context(database,request);token=request.cookies.get(COOKIE_NAME,'');raw=request.query_params.get('edit_hold','');edit=int(raw) if raw.isdigit() and int(raw)>0 else 0
        if edit and not _load_hold_requirements_into_working(database,cid,token,edit):edit=0
        return _requirements_page(database,context,cid,token,edit_hold=edit)
    @app.post('/setup/person-types/age-toggle')
    async def person_age_toggle(request:Request):
        context=context_for(database,request);cid=int(working_company(context));data=await form_data(request);require_csrf(context,data);pid=int(data.get('person_type_id','0') or 0)
        with database.connect() as c:
            row=c.execute('SELECT ask_age FROM setup_person_types WHERE company_id=? AND id=?',(cid,pid)).fetchone()
            if row is None:raise HTTPException(status_code=404,detail='Person Type not found')
            old=int(row['ask_age'] or 0);new=0 if old else 1;c.execute('UPDATE setup_person_types SET ask_age=? WHERE company_id=? AND id=?',(new,cid,pid))
        audit(database,context,cid,'PERSON_TYPE_AGE_QUESTION_CHANGED','person_type',pid,{'ask_age':old},{'ask_age':new});return RedirectResponse('/setup/person-types',303)
    @app.post('/setup/addons/requirement-toggle')
    async def addon_requirement_toggle(request:Request):
        context=context_for(database,request);cid=int(working_company(context));data=await form_data(request);require_csrf(context,data);aid=int(data.get('addon_id','0') or 0)
        with database.connect() as c:
            row=c.execute('SELECT ask_before_availability FROM setup_addons WHERE company_id=? AND id=?',(cid,aid)).fetchone()
            if row is None:raise HTTPException(status_code=404,detail='Feature / Extra not found')
            old=int(row['ask_before_availability'] or 0);new=0 if old else 1;c.execute('UPDATE setup_addons SET ask_before_availability=? WHERE company_id=? AND id=?',(new,cid,aid))
        audit(database,context,cid,'ADDON_AVAILABILITY_QUESTION_CHANGED','addon',aid,{'ask_before_availability':old},{'ask_before_availability':new});return RedirectResponse('/setup/addons',303)
