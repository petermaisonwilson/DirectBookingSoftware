from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .app import esc, form_data, layout
from .database import iso_now
from .db_compat import seed_runtime_defaults
from .setup015_core import audit, context_for, require_csrf, rows, working_company

INTERNAL_STATES=(('HELD','Held / Enquiry'),('RESERVED','Reserved / Provisional'),('CONFIRMED','Confirmed booking'),('ON_SITE','On site'),('RELEASED','Released / no longer blocks'))
DEFAULT_STATUSES=(('Enquiry / Held','Held','#FFE39A',10,'HELD',1,10),('Deposit Paid','Deposit','#F3C5C9',20,'CONFIRMED',1,None),('Balance Paid','Paid','#CDECCF',30,'CONFIRMED',1,None),('On Site','On site','#CFE2FF',40,'ON_SITE',1,None),('Released / Cancelled','Released','#E5E7EB',90,'RELEASED',0,None))

def initialise_booking_statuses(database)->None:seed_runtime_defaults(database)
def default_status(database,company_id:int,internal_state:str):
    result=rows(database,'SELECT * FROM booking_status_definitions WHERE company_id=? AND active=1 AND internal_state=? ORDER BY display_order,id LIMIT 1',(company_id,internal_state));return result[0] if result else None
def status_by_id(database,company_id:int,status_id:int|None):
    if not status_id:return None
    result=rows(database,'SELECT * FROM booking_status_definitions WHERE company_id=? AND id=?',(company_id,status_id));return result[0] if result else None
def _page(database,context,message='',edit=0):
    cid=working_company(context);items=rows(database,'SELECT * FROM booking_status_definitions WHERE company_id=? ORDER BY display_order,id',(cid,));current=next((r for r in items if int(r['id'])==int(edit or 0)),None);error=f'<div class="error">{esc(message)}</div>' if message else '';name=str(current['name']) if current else '';short=str(current['short_name']) if current else '';colour=str(current['colour']) if current else '#F3C5C9';order=int(current['display_order']) if current else 10;state=str(current['internal_state']) if current else 'CONFIRMED';blocks=int(current['blocks_availability']) if current else 1;expiry='' if not current or current['expiry_minutes'] is None else str(current['expiry_minutes']);state_options=''.join(f'<option value="{code}" {"selected" if code==state else ""}>{esc(label)}</option>' for code,label in INTERNAL_STATES);body=f'''<h1>Booking Statuses</h1>{error}<div class="card"><p>Client-defined labels and colours map onto stable internal availability states.</p><form method="post" action="/setup/booking-statuses"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><input type="hidden" name="id" value="{int(current['id']) if current else ''}"><div class="grid"><div><label>Name</label><input name="name" value="{esc(name)}"></div><div><label>Short name</label><input name="short_name" value="{esc(short)}"></div><div><label>Colour</label><input type="color" name="colour" value="{esc(colour)}"></div><div><label>Order</label><input type="number" name="display_order" value="{order}"></div><div><label>Internal state</label><select name="internal_state">{state_options}</select></div><div><label>Expiry minutes</label><input type="number" min="1" name="expiry_minutes" value="{esc(expiry)}"></div></div><label><input style="width:auto" type="checkbox" name="blocks_availability" value="1" {'checked' if blocks else ''}> Blocks availability</label><p><button>Save status</button></p></form></div><div class="card"><table><thead><tr><th>Status</th><th>State</th><th>Blocks</th><th>Expiry</th><th></th></tr></thead><tbody>'''
    for r in items:body+=f'<tr><td><span style="display:inline-block;width:18px;height:18px;background:{esc(r["colour"])};border:1px solid #888;vertical-align:middle"></span> {esc(r["name"])}</td><td>{esc(r["internal_state"])}</td><td>{"Yes" if r["blocks_availability"] else "No"}</td><td>{esc(r["expiry_minutes"] or "—")}</td><td><a href="/setup/booking-statuses?edit={int(r["id"])}">Edit</a></td></tr>'
    body+='</tbody></table></div>';return layout('Booking Statuses',body,context)
def register_booking_status_routes(app)->None:
    database=app.state.database
    @app.get('/setup/booking-statuses',response_class=HTMLResponse)
    def page(request:Request,edit:int=0):return _page(database,context_for(database,request),edit=edit)
    @app.post('/setup/booking-statuses')
    async def save(request:Request):
        context=context_for(database,request);cid=int(working_company(context));data=await form_data(request);require_csrf(context,data);name=str(data.get('name','')).strip();state=str(data.get('internal_state',''));raw_id=str(data.get('id',''));short=str(data.get('short_name','')).strip();colour=str(data.get('colour','#F3C5C9'));blocks=1 if data.get('blocks_availability')=='1' else 0
        try:order=int(data.get('display_order','10'));expiry=int(data['expiry_minutes']) if str(data.get('expiry_minutes','')).strip() else None
        except ValueError:return HTMLResponse(_page(database,context,'Order and expiry must be whole numbers.',int(raw_id) if raw_id.isdigit() else 0),400)
        if not name or state not in {x[0] for x in INTERNAL_STATES}:return HTMLResponse(_page(database,context,'Enter a name and valid internal state.',int(raw_id) if raw_id.isdigit() else 0),400)
        now=iso_now()
        with database.connect() as c:
            if raw_id.isdigit():
                old=c.execute('SELECT * FROM booking_status_definitions WHERE company_id=? AND id=?',(cid,int(raw_id))).fetchone();entity_id=int(raw_id);before=dict(old) if old else None;c.execute('UPDATE booking_status_definitions SET name=?,short_name=?,colour=?,display_order=?,internal_state=?,blocks_availability=?,expiry_minutes=?,updated_at=? WHERE company_id=? AND id=?',(name,short,colour,order,state,blocks,expiry,now,cid,entity_id))
            else:before=None;entity_id=c.execute('INSERT INTO booking_status_definitions(company_id,name,short_name,colour,display_order,internal_state,blocks_availability,expiry_minutes,automation_config_json,active,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,1,?,?)',(cid,name,short,colour,order,state,blocks,expiry,'{}',now,now)).lastrowid
        audit(database,context,cid,'BOOKING_STATUS_SAVED','booking_status',entity_id,before,{'name':name,'internal_state':state,'blocks_availability':blocks,'expiry_minutes':expiry});return RedirectResponse('/setup/booking-statuses',303)
