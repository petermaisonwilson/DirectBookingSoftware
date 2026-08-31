from __future__ import annotations
from datetime import date,timedelta
from typing import Any
from .setup015_calculator import _addon_rule
from .setup015_core import rows
from .setup015_readiness import element_available_setup_ready
from . import webv1_availability as legacy

def _booking_conflict(c,cid,eid,start,end,exclude_booking_id=None):
    sql="""SELECT b.id,b.reference,b.status,b.workflow_status_id,be.arrival_date,be.departure_date,s.name AS workflow_name,s.colour,s.blocks_availability,s.internal_state FROM booking_elements be JOIN bookings b ON b.id=be.booking_id AND b.company_id=be.company_id LEFT JOIN booking_status_definitions s ON s.id=b.workflow_status_id AND s.company_id=b.company_id WHERE be.company_id=? AND be.element_id=? AND COALESCE(s.blocks_availability,CASE WHEN b.status='cancelled' THEN 0 ELSE 1 END)=1 AND CAST(be.arrival_date AS DATE)<CAST(? AS DATE) AND CAST(be.departure_date AS DATE)>CAST(? AS DATE)""";params=[cid,eid,end,start]
    if exclude_booking_id is not None:sql+=' AND b.id<>?';params.append(exclude_booking_id)
    return c.execute(sql+' ORDER BY be.arrival_date LIMIT 1',params).fetchone()
def _enquiry_conflict(c,cid,eid,start,end,exclude_enquiry_id=None):
    sql="""SELECT e.id,e.customer_id,e.status,e.arrival_date,e.departure_date,e.availability_expires_at,s.id AS workflow_status_id,s.name AS workflow_name,s.colour,s.blocks_availability,s.internal_state FROM enquiries e JOIN enquiry_requests er ON er.enquiry_id=e.id AND er.company_id=e.company_id JOIN booking_status_definitions s ON s.id=e.workflow_status_id AND s.company_id=e.company_id WHERE e.company_id=? AND er.element_id=? AND e.status NOT IN ('closed','converted') AND s.blocks_availability=1 AND (e.availability_expires_at IS NULL OR e.availability_expires_at>?) AND CAST(e.arrival_date AS DATE)<CAST(? AS DATE) AND CAST(e.departure_date AS DATE)>CAST(? AS DATE)""";params=[cid,eid,legacy.iso_now(),end,start]
    if exclude_enquiry_id is not None:sql+=' AND e.id<>?';params.append(exclude_enquiry_id)
    return c.execute(sql+' ORDER BY e.arrival_date LIMIT 1',params).fetchone()
def availability_state(database,cid,eid,arrival,departure,*,session_token='',exclude_booking_id=None,exclude_enquiry_id=None):
    try:start,end=date.fromisoformat(arrival),date.fromisoformat(departure)
    except ValueError:return {'available':False,'state':'INVALID','reason':'Enter valid arrival and departure dates.'}
    if end<=start:return {'available':False,'state':'INVALID','reason':'Departure must be after arrival.'}
    if start.year!=(end-timedelta(days=1)).year:return {'available':False,'state':'OUT_OF_SEASON','reason':'The stay must remain within one pricing year.'}
    window=legacy.operating_window(database,cid,start.year)
    if window is None or start<window[0] or end>window[1]:return {'available':False,'state':'OUT_OF_SEASON','reason':'The selected stay is outside the configured operating season.'}
    with database.connect() as c:
        element=c.execute('SELECT * FROM setup_elements WHERE id=? AND company_id=?',(eid,cid)).fetchone()
        if element is None or not int(element['active']):return {'available':False,'state':'INACTIVE','reason':'Element is inactive.'}
    ready,reason=element_available_setup_ready(database,cid,eid,start,end)
    if not ready:return {'available':False,'state':'SETUP_INCOMPLETE','reason':reason}
    with database.connect() as c:
        closed=legacy._closure_conflict(c,cid,eid,arrival,departure)
        if closed:return {'available':False,'state':'CLOSED','reason':str(closed['reason'] or 'Closed'),'closure_id':int(closed['id'])}
        booked=_booking_conflict(c,cid,eid,arrival,departure,exclude_booking_id)
        if booked:return {'available':False,'state':'BOOKED','reason':str(booked['workflow_name'] or f"Booked: {booked['reference']}"),'booking_id':int(booked['id']),'booking_reference':str(booked['reference'])}
        enquiry=_enquiry_conflict(c,cid,eid,arrival,departure,exclude_enquiry_id)
        if enquiry:return {'available':False,'state':'ENQUIRY','reason':str(enquiry['workflow_name'] or 'Enquiry / Held'),'enquiry_id':int(enquiry['id']),'expires_at':enquiry['availability_expires_at']}
        legacy._purge_expired_holds(c);held=c.execute('SELECT * FROM element_holds WHERE company_id=? AND element_id=? AND CAST(arrival_date AS DATE)<CAST(? AS DATE) AND CAST(departure_date AS DATE)>CAST(? AS DATE) ORDER BY expires_at DESC LIMIT 1',(cid,eid,departure,arrival)).fetchone()
        if held:
            own=bool(session_token and str(held['session_token'])==session_token);return {'available':own,'state':'HELD_BY_YOU' if own else 'HELD','reason':'Held in your basket' if own else 'Temporarily held','hold_id':int(held['id']),'expires_at':str(held['expires_at']),'renewal_required_at':str(held['renewal_required_at'])}
    return {'available':True,'state':'AVAILABLE','reason':''}
def available_elements(database,cid,element_type,arrival,departure,*,session_token=''):
    result=[]
    for element in rows(database,'SELECT * FROM setup_elements WHERE company_id=? AND active=1 AND element_type=? ORDER BY lower(name)',(cid,element_type)):
        state=availability_state(database,cid,int(element['id']),arrival,departure,session_token=session_token)
        if state['available']:result.append({'id':int(element['id']),'name':str(element['name']),'element_type':str(element['element_type']),'state':state['state'],'addons':[]})
    return result
def install_status_aware_availability():
    legacy.availability_state=availability_state;legacy.available_elements=available_elements
