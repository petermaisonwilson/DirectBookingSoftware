from __future__ import annotations
import json
from datetime import datetime
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .app import esc, form_data, layout
from .database import iso_now
from .setup015_calculator import _addon_rule
from .setup015_core import audit, context_for, one, require_csrf, rows, working_company
from .webv1_booking_status import status_by_id
from .webv1_status_availability import availability_state

def initialise_booking_workflow(database):return None
def _fmt_day(value):
    if not value:return '—'
    try:return datetime.fromisoformat(str(value)).strftime('%d/%m/%Y')
    except ValueError:
        try:return datetime.strptime(str(value),'%Y-%m-%d').strftime('%d/%m/%Y')
        except ValueError:return str(value)
def _money(value):return f'€{float(value or 0):.2f}'
def _booking(database,cid,bid):return one(database,'SELECT b.*,c.first_name,c.last_name,c.email,c.phone,s.name AS workflow_name,s.colour,s.internal_state,s.blocks_availability FROM bookings b LEFT JOIN customer_records c ON c.id=b.customer_id AND c.company_id=b.company_id LEFT JOIN booking_status_definitions s ON s.id=b.workflow_status_id AND s.company_id=b.company_id WHERE b.id=? AND b.company_id=?',(bid,cid))
def _next_reference(c,cid):
    prefix=datetime.now().strftime('DB%y');highest=0
    for row in c.execute('SELECT reference FROM bookings WHERE company_id=? AND reference LIKE ? ORDER BY id DESC LIMIT 100',(cid,prefix+'-%')).fetchall():
        try:highest=max(highest,int(str(row['reference']).rsplit('-',1)[1]))
        except (ValueError,IndexError):pass
    return f'{prefix}-{highest+1:05d}'
def _conversion_statuses(database,cid):return rows(database,"SELECT * FROM booking_status_definitions WHERE company_id=? AND active=1 AND internal_state IN ('RESERVED','CONFIRMED','ON_SITE') ORDER BY CASE internal_state WHEN 'RESERVED' THEN 1 WHEN 'CONFIRMED' THEN 2 ELSE 3 END,display_order,id",(cid,))
def _snapshot_line_amount(snapshot,name):
    for line in snapshot.get('lines') or []:
        if str(line.get('item',''))==name:
            try:return float(line.get('amount',0) or 0)
            except (TypeError,ValueError):return 0.0
    return 0.0
def convert_enquiry(database,context,cid,enquiry_id,workflow_status_id):
    enquiry=one(database,'SELECT e.*,er.element_type,er.element_id,er.provisional_total,er.pricing_snapshot_json,se.name AS element_name,se.pricing_method FROM enquiries e JOIN enquiry_requests er ON er.enquiry_id=e.id AND er.company_id=e.company_id LEFT JOIN setup_elements se ON se.id=er.element_id AND se.company_id=er.company_id WHERE e.id=? AND e.company_id=?',(enquiry_id,cid))
    if enquiry is None:raise ValueError('Enquiry not found.')
    if str(enquiry['status'])=='converted':
        existing=one(database,'SELECT id FROM bookings WHERE company_id=? AND enquiry_id=? ORDER BY id DESC LIMIT 1',(cid,enquiry_id))
        if existing:return int(existing['id'])
        raise ValueError('This Enquiry is already marked converted.')
    if not enquiry['element_id'] or not enquiry['arrival_date'] or not enquiry['departure_date']:raise ValueError('Choose a specific Element and stay dates before converting this Enquiry.')
    if enquiry['provisional_total'] is None:raise ValueError('Calculate and save the Enquiry price before converting it to a Booking.')
    status=status_by_id(database,cid,workflow_status_id)
    if status is None or not int(status['active']) or str(status['internal_state']) not in {'RESERVED','CONFIRMED','ON_SITE'}:raise ValueError('Choose a valid Booking Status.')
    state=availability_state(database,cid,int(enquiry['element_id']),str(enquiry['arrival_date']),str(enquiry['departure_date']),exclude_enquiry_id=enquiry_id)
    if not state['available']:raise ValueError('The Element is no longer available: '+str(state['reason']))
    try:snapshot=json.loads(enquiry['pricing_snapshot_json'] or '{}')
    except (TypeError,json.JSONDecodeError):snapshot={}
    if not snapshot:raise ValueError('The Enquiry does not contain a frozen price snapshot. Recalculate it first.')
    now=iso_now();total=float(enquiry['provisional_total'])
    with database.connect() as c:
        reference=_next_reference(c,cid);booking_id=int(c.execute('INSERT INTO bookings(company_id,reference,customer_id,enquiry_id,status,arrival_date,departure_date,currency,total_amount,pricing_snapshot_json,notes,created_at,updated_at,workflow_status_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(cid,reference,enquiry['customer_id'],enquiry_id,'confirmed',enquiry['arrival_date'],enquiry['departure_date'],'EUR',total,enquiry['pricing_snapshot_json'],enquiry['notes'] or '',now,now,workflow_status_id)).lastrowid);element_amount=_snapshot_line_amount(snapshot,str(enquiry['element_name'] or ''));beid=int(c.execute('INSERT INTO booking_elements(company_id,booking_id,element_id,arrival_date,departure_date,pricing_method_snapshot,unit_price_snapshot,total_amount,pricing_snapshot_json) VALUES (?,?,?,?,?,?,?,?,?)',(cid,booking_id,enquiry['element_id'],enquiry['arrival_date'],enquiry['departure_date'],enquiry['pricing_method'] or '',0,element_amount,enquiry['pricing_snapshot_json'])).lastrowid);year=int(snapshot.get('year') or str(enquiry['arrival_date'])[:4])
        for person in c.execute('SELECT ep.person_type_id,ep.quantity,pt.name FROM enquiry_people ep JOIN setup_person_types pt ON pt.id=ep.person_type_id AND pt.company_id=ep.company_id WHERE ep.enquiry_id=? AND ep.company_id=?',(enquiry_id,cid)).fetchall():
            price=c.execute('SELECT rate FROM setup_person_prices WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?',(cid,year,enquiry['element_id'],person['person_type_id'])).fetchone();unit=float(price['rate']) if price else 0.0;c.execute('INSERT INTO booking_people(company_id,booking_element_id,person_type_id,quantity,unit_price_snapshot,total_amount) VALUES (?,?,?,?,?,?)',(cid,beid,person['person_type_id'],person['quantity'],unit,_snapshot_line_amount(snapshot,str(person['name']))))
        for aid in [int(x) for x in (snapshot.get('selected_addons') or [])]:
            addon=c.execute('SELECT * FROM setup_addons WHERE company_id=? AND id=?',(cid,aid)).fetchone()
            if addon is None:continue
            qrow=c.execute('SELECT quantity FROM enquiry_addons WHERE enquiry_id=? AND company_id=? AND addon_id=?',(enquiry_id,cid,aid)).fetchone();qty=int(qrow['quantity']) if qrow else 0;rule=_addon_rule(database,cid,year,one(database,'SELECT * FROM setup_elements WHERE company_id=? AND id=?',(cid,enquiry['element_id'])),aid);amount=_snapshot_line_amount(snapshot,str(addon['name']));detail={'rule':dict(rule),'when':(snapshot.get('addon_when') or {}).get(str(aid)),'days':(snapshot.get('addon_days') or {}).get(str(aid),{}),'people':(snapshot.get('addon_people') or {}).get(str(aid),{}),'person_days':(snapshot.get('addon_person_days') or {}).get(str(aid),{}),'frozen_amount':amount};c.execute('INSERT INTO booking_addons(company_id,booking_element_id,addon_id,quantity,pricing_method_snapshot,unit_price_snapshot,total_amount,rule_snapshot_json) VALUES (?,?,?,?,?,?,?,?)',(cid,beid,aid,qty,addon['pricing_method'],float(rule.get('rate') or 0),amount,json.dumps(detail,separators=(',',':'))))
        c.execute("UPDATE enquiries SET status='converted',availability_expires_at=NULL,updated_at=? WHERE id=? AND company_id=?",(now,enquiry_id,cid));token=str(context['token']) if 'token' in context.keys() else ''
        if token:c.execute('DELETE FROM element_holds WHERE company_id=? AND element_id=? AND session_token=?',(cid,enquiry['element_id'],token))
    audit(database,context,cid,'ENQUIRY_CONVERTED_TO_BOOKING','enquiry',enquiry_id,after={'booking_id':booking_id,'reference':reference});audit(database,context,cid,'BOOKING_CREATED','booking',booking_id,after={'reference':reference,'enquiry_id':enquiry_id,'workflow_status_id':workflow_status_id,'total_amount':total});return booking_id
def _history(database,cid,bid):return rows(database,"SELECT a.*,u.first_name,u.last_name FROM audit_log a LEFT JOIN users u ON u.id=a.actor_user_id WHERE a.company_id=? AND a.entity_type='booking' AND a.entity_id=? ORDER BY a.id DESC",(cid,str(bid)))
def register_booking_routes(app):
    database=app.state.database
    @app.get('/operations/bookings',response_class=HTMLResponse)
    def booking_register(request:Request):
        context=context_for(database,request);cid=int(working_company(context));data=rows(database,'SELECT b.*,c.first_name,c.last_name,s.name AS workflow_name,s.colour FROM bookings b LEFT JOIN customer_records c ON c.id=b.customer_id AND c.company_id=b.company_id LEFT JOIN booking_status_definitions s ON s.id=b.workflow_status_id AND s.company_id=b.company_id WHERE b.company_id=? ORDER BY b.arrival_date,b.id',(cid,));trs=''.join(f'<tr><td><a href="/operations/bookings/{int(r["id"])}">{esc(r["reference"])}</a></td><td>{esc((str(r["first_name"] or "")+" "+str(r["last_name"] or "")).strip() or "Customer")}</td><td>{esc(r["workflow_name"] or r["status"])}</td><td>{_fmt_day(r["arrival_date"])}</td><td>{_fmt_day(r["departure_date"])}</td><td>{_money(r["total_amount"])}</td></tr>' for r in data) or '<tr><td colspan="6">No Bookings yet.</td></tr>';return layout('Bookings',f'<h1>Bookings</h1><div class="card"><table><tbody>{trs}</tbody></table></div>',context)
    @app.post('/operations/enquiries/{enquiry_id}/convert')
    async def convert(enquiry_id:int,request:Request):
        context=context_for(database,request);cid=int(working_company(context));data=await form_data(request);require_csrf(context,data)
        try:bid=convert_enquiry(database,context,cid,enquiry_id,int(data.get('workflow_status_id','')))
        except (TypeError,ValueError) as exc:return RedirectResponse(f'/operations/enquiries/{enquiry_id}?convert_error={esc(str(exc))}',303)
        return RedirectResponse(f'/operations/bookings/{bid}?created=1',303)
    @app.post('/operations/bookings/{bid}/payments')
    async def payment(bid:int,request:Request):
        context=context_for(database,request);cid=int(working_company(context));data=await form_data(request);require_csrf(context,data)
        try:amount=round(float(str(data.get('amount','')).replace(',','.')),2);assert amount>0
        except (ValueError,AssertionError):return RedirectResponse(f'/operations/bookings/{bid}?message=Enter+a+valid+payment+amount',303)
        payment_date=str(data.get('payment_date','')).strip()
        try:datetime.strptime(payment_date,'%Y-%m-%d')
        except ValueError:return RedirectResponse(f'/operations/bookings/{bid}?message=Enter+a+valid+payment+date',303)
        with database.connect() as c:pid=int(c.execute('INSERT INTO booking_payments(company_id,booking_id,amount,payment_date,method,reference,notes,created_by_user_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)',(cid,bid,amount,payment_date,str(data.get('method','')).strip(),str(data.get('reference','')).strip(),str(data.get('notes','')).strip(),context['user_id'],iso_now())).lastrowid)
        audit(database,context,cid,'BOOKING_PAYMENT_RECORDED','booking',bid,after={'payment_id':pid,'amount':amount,'payment_date':payment_date});return RedirectResponse(f'/operations/bookings/{bid}?message=Payment+recorded',303)
def enquiry_conversion_panel(database,context,enquiry_id):
    cid=int(working_company(context));existing=one(database,'SELECT id,reference FROM bookings WHERE company_id=? AND enquiry_id=? ORDER BY id DESC LIMIT 1',(cid,enquiry_id))
    if existing:return f'<div class="card"><h2>Booking</h2><p>This Enquiry has been converted to <a href="/operations/bookings/{int(existing["id"])}"><strong>{esc(existing["reference"])}</strong></a>.</p></div>'
    statuses=_conversion_statuses(database,cid)
    if not statuses:return '<div class="card"><h2>Convert to Booking</h2><div class="error">Create an active Reserved/Confirmed Booking Status first.</div></div>'
    default=next((s for s in statuses if str(s['internal_state'])=='CONFIRMED'),statuses[0]);opts=''.join(f'<option value="{int(s["id"])}" {"selected" if int(s["id"])==int(default["id"]) else ""}>{esc(s["name"])}</option>' for s in statuses);return f'<div class="card"><h2>Convert to Booking</h2><form method="post" action="/operations/enquiries/{enquiry_id}/convert"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><select name="workflow_status_id">{opts}</select><button>Confirm / Convert to Booking</button></form></div>'
