from __future__ import annotations
from datetime import timedelta
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from .app import COOKIE_NAME, esc, form_data, layout
from .database import iso_now
from .db_compat import seed_runtime_defaults
from .setup015_core import audit, context_for, require_csrf, working_company
from . import webv1_availability as legacy
DEFAULT_HOLD_SECONDS=600;DEFAULT_GRACE_SECONDS=60;MIN_HOLD_SECONDS=10;MAX_HOLD_SECONDS=14400;MIN_GRACE_SECONDS=5;MAX_GRACE_SECONDS=1800
def initialise_hold_settings(database):seed_runtime_defaults(database)
def hold_timing(database,company_id):
    with database.connect() as c:row=c.execute('SELECT hold_seconds,grace_seconds FROM company_hold_settings WHERE company_id=?',(company_id,)).fetchone()
    return (DEFAULT_HOLD_SECONDS,DEFAULT_GRACE_SECONDS) if row is None else (int(row['hold_seconds']),int(row['grace_seconds']))
def _validate(raw,minimum,maximum,label):
    try:value=int(raw)
    except (TypeError,ValueError):raise ValueError(f'{label} must be a whole number of seconds.')
    if value<minimum or value>maximum:raise ValueError(f'{label} must be between {minimum} and {maximum} seconds.')
    return value
def create_or_replace_hold(database,context,company_id,session_token,element_id,arrival,departure):
    with database.connect() as c:
        legacy._purge_expired_holds(c);existing=c.execute('SELECT * FROM element_holds WHERE company_id=? AND element_id=? AND session_token=?',(company_id,element_id,session_token)).fetchone()
    if existing is not None:raise ValueError('That Element is already in your basket. Use EDIT in Booking in progress to change it.')
    state=legacy.availability_state(database,company_id,element_id,arrival,departure,session_token=session_token)
    if not state['available']:raise ValueError(state['reason'])
    hold_seconds,grace_seconds=hold_timing(database,company_id);now=legacy._now();prompt=now+timedelta(seconds=hold_seconds);expires=prompt+timedelta(seconds=grace_seconds)
    with database.connect() as c:
        legacy._purge_expired_holds(c);conflict=c.execute('SELECT id FROM element_holds WHERE company_id=? AND element_id=? AND session_token<>? AND CAST(arrival_date AS DATE)<CAST(? AS DATE) AND CAST(departure_date AS DATE)>CAST(? AS DATE) AND expires_at>? LIMIT 1',(company_id,element_id,session_token,departure,arrival,iso_now())).fetchone()
        if conflict or legacy._booking_conflict(c,company_id,element_id,arrival,departure) or legacy._closure_conflict(c,company_id,element_id,arrival,departure):raise ValueError('That Element has just become unavailable. Please choose another.')
        hid=int(c.execute('INSERT INTO element_holds(company_id,element_id,session_token,holder_user_id,arrival_date,departure_date,renewal_required_at,expires_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)',(company_id,element_id,session_token,context['user_id'],arrival,departure,prompt.isoformat(timespec='seconds'),expires.isoformat(timespec='seconds'),now.isoformat(timespec='seconds'),now.isoformat(timespec='seconds'))).lastrowid)
    audit(database,context,company_id,'ELEMENT_HOLD_SAVED','element_hold',hid,None,{'element_id':element_id,'arrival_date':arrival,'departure_date':departure,'renewal_required_at':prompt.isoformat(timespec='seconds'),'expires_at':expires.isoformat(timespec='seconds')});return {'id':hid,'renewal_required_at':prompt.isoformat(timespec='seconds'),'expires_at':expires.isoformat(timespec='seconds')}
def install_hold_timing():
    legacy.create_or_replace_hold=create_or_replace_hold
def register_hold_settings_routes(app):
    database=app.state.database
    @app.get('/setup/hold-settings',response_class=HTMLResponse)
    def page(request:Request,message:str=''):
        context=context_for(database,request);cid=int(working_company(context));hold,grace=hold_timing(database,cid);error=f'<div class="error">{esc(message)}</div>' if message else '';return layout('Hold Settings',f'<h1>Hold Settings</h1>{error}<div class="card"><form method="post"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><label>Hold seconds</label><input name="hold_seconds" value="{hold}"><label>Grace seconds</label><input name="grace_seconds" value="{grace}"><button>Save</button></form></div>',context)
    @app.post('/setup/hold-settings')
    async def save(request:Request):
        context=context_for(database,request);cid=int(working_company(context));data=await form_data(request);require_csrf(context,data)
        try:hold=_validate(data.get('hold_seconds',''),MIN_HOLD_SECONDS,MAX_HOLD_SECONDS,'Hold time');grace=_validate(data.get('grace_seconds',''),MIN_GRACE_SECONDS,MAX_GRACE_SECONDS,'Grace time')
        except ValueError as exc:return RedirectResponse('/setup/hold-settings?message='+str(exc).replace(' ','+'),303)
        with database.connect() as c:c.execute('INSERT INTO company_hold_settings(company_id,hold_seconds,grace_seconds,updated_by_user_id,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(company_id) DO UPDATE SET hold_seconds=excluded.hold_seconds,grace_seconds=excluded.grace_seconds,updated_by_user_id=excluded.updated_by_user_id,updated_at=excluded.updated_at',(cid,hold,grace,context['user_id'],iso_now()))
        return RedirectResponse('/setup/hold-settings',303)
