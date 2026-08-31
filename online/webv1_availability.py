from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from .app import COOKIE_NAME, esc, form_data, layout
from .database import iso_now
from .setup015_calculator import _addon_rule
from .setup015_catalogue import setup_nav
from .setup015_core import audit, context_for, require_csrf, rows, working_company
HOLD_MINUTES=10;HOLD_GRACE_MINUTES=1

def initialise_availability(database)->None:return None
def _parse_day(value):return date.fromisoformat(value)
def _utc(value):
    parsed=datetime.fromisoformat(value);return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
def _now():return datetime.now(timezone.utc)
def _session_company(database,request):
    context=database.session_context(request.cookies.get(COOKIE_NAME))
    if context is None:raise HTTPException(status_code=401,detail='Login required')
    cid=context['acting_company_id'] if context['role']=='supervisor' else context['company_id']
    if not cid:raise HTTPException(status_code=403,detail='No Client selected')
    return context,int(cid)
def operating_window(database,company_id,year):
    with database.connect() as c:row=c.execute('SELECT MIN(start_date) AS first_day, MAX(end_date) AS last_day FROM setup_seasons WHERE company_id=? AND year=?',(company_id,year)).fetchone()
    if not row or not row['first_day'] or not row['last_day']:return None
    return _parse_day(row['first_day']),_parse_day(row['last_day'])+timedelta(days=1)
def _purge_expired_holds(c):
    expired=[int(r['id']) for r in c.execute('SELECT id FROM element_holds WHERE expires_at<=?',(iso_now(),)).fetchall()]
    for hid in expired:c.execute('DELETE FROM hold_requirement_people WHERE hold_id=?',(hid,));c.execute('DELETE FROM hold_requirement_addons WHERE hold_id=?',(hid,))
    if expired:c.execute('DELETE FROM element_holds WHERE expires_at<=?',(iso_now(),))
def _booking_conflict(c,cid,eid,start,end,exclude_booking_id=None):
    sql="""SELECT b.id,b.reference,b.status,be.arrival_date,be.departure_date FROM booking_elements be JOIN bookings b ON b.id=be.booking_id AND b.company_id=be.company_id WHERE be.company_id=? AND be.element_id=? AND b.status<>'cancelled' AND CAST(be.arrival_date AS DATE)<CAST(? AS DATE) AND CAST(be.departure_date AS DATE)>CAST(? AS DATE)""";params=[cid,eid,end,start]
    if exclude_booking_id is not None:sql+=' AND b.id<>?';params.append(exclude_booking_id)
    return c.execute(sql+' ORDER BY be.arrival_date LIMIT 1',params).fetchone()
def _closure_conflict(c,cid,eid,start,end,exclude_closure_id=None):
    sql='SELECT * FROM element_closures WHERE company_id=? AND element_id=? AND CAST(start_date AS DATE)<CAST(? AS DATE) AND CAST(end_date AS DATE)>CAST(? AS DATE)';params=[cid,eid,end,start]
    if exclude_closure_id is not None:sql+=' AND id<>?';params.append(exclude_closure_id)
    return c.execute(sql+' ORDER BY start_date LIMIT 1',params).fetchone()
def availability_state(database,cid,eid,arrival,departure,*,session_token='',exclude_booking_id=None):
    try:start,end=_parse_day(arrival),_parse_day(departure)
    except ValueError:return {'available':False,'state':'INVALID','reason':'Enter valid arrival and departure dates.'}
    if end<=start:return {'available':False,'state':'INVALID','reason':'Departure must be after arrival.'}
    if start.year!=(end-timedelta(days=1)).year:return {'available':False,'state':'OUT_OF_SEASON','reason':'The stay must remain within one pricing year.'}
    window=operating_window(database,cid,start.year)
    if window is None:return {'available':False,'state':'OUT_OF_SEASON','reason':'No operating season is configured for these dates.'}
    if start<window[0] or end>window[1]:return {'available':False,'state':'OUT_OF_SEASON','reason':f'Element is open from {window[0].isoformat()} to {(window[1]-timedelta(days=1)).isoformat()}.'}
    with database.connect() as c:
        element=c.execute('SELECT * FROM setup_elements WHERE id=? AND company_id=?',(eid,cid)).fetchone()
        if element is None or not int(element['active']):return {'available':False,'state':'INACTIVE','reason':'Element is inactive.'}
        closed=_closure_conflict(c,cid,eid,arrival,departure)
        if closed:return {'available':False,'state':'CLOSED','reason':str(closed['reason'] or 'Closed'),'closure_id':int(closed['id'])}
        booked=_booking_conflict(c,cid,eid,arrival,departure,exclude_booking_id)
        if booked:return {'available':False,'state':'BOOKED','reason':f"Booked: {booked['reference']}",'booking_id':int(booked['id']),'booking_reference':str(booked['reference'])}
        _purge_expired_holds(c);held=c.execute('SELECT * FROM element_holds WHERE company_id=? AND element_id=? AND CAST(arrival_date AS DATE)<CAST(? AS DATE) AND CAST(departure_date AS DATE)>CAST(? AS DATE) ORDER BY expires_at DESC LIMIT 1',(cid,eid,departure,arrival)).fetchone()
        if held:
            own=bool(session_token and str(held['session_token'])==session_token);return {'available':own,'state':'HELD_BY_YOU' if own else 'HELD','reason':'Held in your basket' if own else 'Temporarily held','hold_id':int(held['id']),'expires_at':str(held['expires_at']),'renewal_required_at':str(held['renewal_required_at'])}
    return {'available':True,'state':'AVAILABLE','reason':''}
def available_elements(database,cid,element_type,arrival,departure,*,session_token=''):
    try:year=_parse_day(arrival).year
    except ValueError:return []
    result=[]
    for element in rows(database,'SELECT * FROM setup_elements WHERE company_id=? AND active=1 AND element_type=? ORDER BY lower(name)',(cid,element_type)):
        state=availability_state(database,cid,int(element['id']),arrival,departure,session_token=session_token)
        if not state['available']:continue
        addons=[]
        for addon in rows(database,'SELECT * FROM setup_addons WHERE company_id=? AND active=1 ORDER BY lower(name)',(cid,)):
            rule=_addon_rule(database,cid,year,element,int(addon['id']));addons.append({'id':int(addon['id']),'name':str(addon['name']),'available':bool(rule['allowed'])})
        result.append({'id':int(element['id']),'name':str(element['name']),'element_type':str(element['element_type']),'state':state['state'],'addons':addons})
    return result

def create_or_replace_hold(database,context,cid,token,eid,arrival,departure):
    state=availability_state(database,cid,eid,arrival,departure,session_token=token)
    if not state['available']:raise ValueError(state['reason'])
    now=_now();renew=now+timedelta(minutes=HOLD_MINUTES);expires=renew+timedelta(minutes=HOLD_GRACE_MINUTES)
    with database.connect() as c:
        existing=c.execute('SELECT id FROM element_holds WHERE company_id=? AND element_id=? AND session_token=?',(cid,eid,token)).fetchone()
        if existing:c.execute('UPDATE element_holds SET arrival_date=?,departure_date=?,renewal_required_at=?,expires_at=?,updated_at=? WHERE id=?',(arrival,departure,renew.isoformat(timespec='seconds'),expires.isoformat(timespec='seconds'),iso_now(),int(existing['id'])));hid=int(existing['id'])
        else:hid=int(c.execute('INSERT INTO element_holds(company_id,element_id,session_token,holder_user_id,arrival_date,departure_date,renewal_required_at,expires_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)',(cid,eid,token,context['user_id'],arrival,departure,renew.isoformat(timespec='seconds'),expires.isoformat(timespec='seconds'),iso_now(),iso_now())).lastrowid)
    return {'id':hid,'element_id':eid,'arrival_date':arrival,'departure_date':departure,'renewal_required_at':renew.isoformat(timespec='seconds'),'expires_at':expires.isoformat(timespec='seconds')}
def register_availability_routes(app):
    database=app.state.database
    @app.post('/availability/holds/release')
    async def release(request:Request):
        context,cid=_session_company(database,request);data=await form_data(request);require_csrf(context,data);token=request.cookies.get(COOKIE_NAME,'')
        with database.connect() as c:
            ids=[int(r['id']) for r in c.execute('SELECT id FROM element_holds WHERE company_id=? AND session_token=?',(cid,token)).fetchall()]
            for hid in ids:c.execute('DELETE FROM hold_requirement_people WHERE hold_id=?',(hid,));c.execute('DELETE FROM hold_requirement_addons WHERE hold_id=?',(hid,))
            c.execute('DELETE FROM element_holds WHERE company_id=? AND session_token=?',(cid,token))
        return JSONResponse({'ok':True})
