from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import COOKIE_NAME, esc, form_data, layout
from .database import iso_now
from .setup015_calculator import _addon_rule
from .setup015_core import audit, require_csrf, rows
from .webv1_availability import _session_company
from .webv1_customers import customer_matches
from .webv1_enquiry_builder import _calculate, _save


def _fmt_user_date(value):
    try:return date.fromisoformat(value).strftime('%d/%m/%Y')
    except (TypeError,ValueError):return value or ''


def _display_end(value,pricing_method):
    try:end=date.fromisoformat(value)
    except (TypeError,ValueError):return value or ''
    if str(pricing_method or '').strip().lower()=='per day':end-=timedelta(days=1)
    return end.strftime('%d/%m/%Y')


def _held_items(database,company_id,token):
    return rows(database,'''SELECT h.id,h.element_id,h.arrival_date,h.departure_date,h.expires_at,h.lead_name,e.name AS element_name,e.element_type,e.pricing_method FROM element_holds h JOIN setup_elements e ON e.id=h.element_id AND e.company_id=h.company_id WHERE h.company_id=? AND h.session_token=? ORDER BY h.created_at,h.id''',(company_id,token))


def _item_requirements(database,item,company_id):
    details=[];hold_id=int(item['id']);element_id=int(item['element_id'])
    try:year=date.fromisoformat(str(item['arrival_date'])).year
    except ValueError:year=date.today().year
    for p in rows(database,'''SELECT hp.quantity,hp.ages_json,pt.name,l.min_count,l.max_count FROM hold_requirement_people hp LEFT JOIN setup_person_types pt ON pt.id=hp.person_type_id AND pt.company_id=hp.company_id LEFT JOIN setup_person_limits l ON l.company_id=hp.company_id AND l.year=? AND l.element_id=? AND l.person_type_id=hp.person_type_id WHERE hp.hold_id=? AND hp.quantity>0 AND l.person_type_id IS NOT NULL AND (l.max_count>0 OR l.min_count>0) ORDER BY pt.name''',(year,element_id,hold_id)):
        label=f'{int(p["quantity"])} {str(p["name"] or "person")}'
        try:ages=json.loads(p['ages_json'] or '[]')
        except json.JSONDecodeError:ages=[]
        if ages:label+=' (age'+('s ' if len(ages)!=1 else ' ')+', '.join(str(x) for x in ages)+')'
        details.append(label)
    element_rows=rows(database,'SELECT * FROM setup_elements WHERE company_id=? AND id=?',(company_id,element_id));element=element_rows[0] if element_rows else None
    if element:
        for a in rows(database,'''SELECT ha.addon_id,ha.quantity,sa.name FROM hold_requirement_addons ha LEFT JOIN setup_addons sa ON sa.id=ha.addon_id AND sa.company_id=ha.company_id WHERE ha.hold_id=? AND ha.quantity>0 AND sa.ask_before_availability=1 ORDER BY sa.name''',(hold_id,)):
            if _addon_rule(database,company_id,year,element,int(a['addon_id'])).get('allowed'):
                details.append(f'{str(a["name"] or "Requirement")} {int(a["quantity"])}')
    return details


def _edit_url(item):
    return '/availability/start?edit_hold='+str(int(item['id']))


def booking_progress_strip(database,context,company_id,token):
    items=_held_items(database,company_id,token)
    if not items:return ''
    chips=[]
    for item in items:
        details=_item_requirements(database,item,company_id);detail_html=' · '.join(esc(x) for x in details) or 'No special requirements';lead=str(item['lead_name'] or '').strip();heading=(esc(lead)+' — ' if lead else '')+esc(item['element_name'])
        chips.append('<div class="booking-progress-item">'+f'<strong>{heading}</strong> <span>{_fmt_user_date(str(item["arrival_date"]))}–{_display_end(str(item["departure_date"]),str(item["pricing_method"]))}</span> <span class="booking-progress-requirements">{detail_html}</span> <a class="button secondary mini-action" href="{_edit_url(item)}">EDIT BOOKING</a> '+'<form method="post" action="/availability/basket/remove-view" class="inline-form">'+f'<input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="hold_id" value="{int(item["id"])}"><button type="submit" class="secondary mini-action">REMOVE</button></form></div>')
    return '<div class="card persistent-booking-progress"><div class="booking-progress-heading"><strong>Booking in progress</strong><span class="booking-progress-actions"><form method="post" action="/availability/new-booking" class="inline-form">'+f'<input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><button type="submit">NEW BOOKING</button></form> '+'<a class="button secondary" href="/availability/basket/review">VIEW BASKET</a></span></div>'+''.join(chips)+'''<style>.persistent-booking-progress{border-color:#9bb3c9;background:#f7fbff}.booking-progress-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:8px}.booking-progress-actions{display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap}.booking-progress-item{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:7px 0;border-top:1px solid #d8e2eb}.booking-progress-item:first-of-type{border-top:0}.booking-progress-requirements{color:#52606d;font-size:13px}.inline-form{display:inline;margin:0}.mini-action{font-size:12px;padding:5px 8px}</style></div>'''


def _load_item_into_working(database,company_id,token,hold_id):
    with database.connect() as c:
        item=c.execute('''SELECT h.*,e.name AS element_name,e.element_type,e.pricing_method FROM element_holds h JOIN setup_elements e ON e.id=h.element_id AND e.company_id=h.company_id WHERE h.id=? AND h.company_id=? AND h.session_token=?''',(hold_id,company_id,token)).fetchone()
        if item is None:return None
        c.execute('DELETE FROM booking_requirement_people WHERE company_id=? AND session_token=?',(company_id,token));c.execute('DELETE FROM booking_requirement_addons WHERE company_id=? AND session_token=?',(company_id,token))
        c.execute('''INSERT INTO booking_requirement_people(session_token,company_id,person_type_id,quantity,ages_json) SELECT ?,company_id,person_type_id,quantity,ages_json FROM hold_requirement_people WHERE hold_id=?''',(token,hold_id))
        c.execute('''INSERT INTO booking_requirement_addons(session_token,company_id,addon_id,quantity) SELECT ?,company_id,addon_id,quantity FROM hold_requirement_addons WHERE hold_id=?''',(token,hold_id))
        c.execute('''INSERT INTO booking_requirement_sessions(session_token,company_id,ready,arrival_date,departure_date,lead_name,updated_at) VALUES (?,?,1,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(session_token,company_id) DO UPDATE SET ready=1,arrival_date=excluded.arrival_date,departure_date=excluded.departure_date,lead_name=excluded.lead_name,updated_at=CURRENT_TIMESTAMP''',(token,company_id,str(item['arrival_date']),str(item['departure_date']),str(item['lead_name'] or '')))
        return item


def _hold_enquiry_values(database,company_id,token,hold_id):
    items=[r for r in _held_items(database,company_id,token) if int(r['id'])==int(hold_id)]
    if not items:return None,None
    item=items[0]
    values={'arrival_date':str(item['arrival_date']),'departure_date':str(item['departure_date']),'party_size':'','source':'Availability','notes':'','element_type':str(item['element_type']),'element_id':str(int(item['element_id']))}
    total=0
    for p in rows(database,'SELECT person_type_id,quantity FROM hold_requirement_people WHERE hold_id=? AND company_id=?',(hold_id,company_id)):
        qty=int(p['quantity'] or 0);values[f'person_{int(p["person_type_id"])}']=str(qty);total+=qty
    values['party_size']=str(total)
    for a in rows(database,'SELECT addon_id,quantity FROM hold_requirement_addons WHERE hold_id=? AND company_id=?',(hold_id,company_id)):
        aid=int(a['addon_id']);qty=int(a['quantity'] or 0)
        if qty>0:
            values[f'addon_{aid}']=str(qty);values[f'addon_selected_{aid}']='1';values[f'addon_when_{aid}']='every_day'
    return item,values


def _customer_values(data):
    keys=('first_name','last_name','email','mobile_phone','fixed_phone','address1','address2','town','postcode','country','notes')
    return {key:str(data.get(key,'') or '').strip() for key in keys}


def _customer_stage(database,context,company_id,token,hold_id,values,error='',matches=None):
    item,enquiry_values=_hold_enquiry_values(database,company_id,token,hold_id)
    if item is None:return layout('Basket','<h1>Basket</h1><div class="error">That held Element has expired or been removed.</div>',context)
    details=' · '.join(esc(x) for x in _item_requirements(database,item,company_id)) or 'No special requirements'
    match_html=''
    if matches:
        rows_html=''
        for row,reasons in matches:
            history=f"{int(row['booking_count'] or 0)} previous Booking(s), {int(row['enquiry_count'] or 0)} Enquiry(ies)"
            rows_html+=f'<tr><td><strong>{esc(str(row["first_name"] or "")+" "+str(row["last_name"] or ""))}</strong></td><td>{esc(" + ".join(reasons))}</td><td>{esc(history)}</td><td><button type="submit" name="existing_customer_id" value="{int(row["id"])}">USE THIS CUSTOMER</button></td></tr>'
        match_html=f'<div class="card"><h2>Possible existing Customer</h2><p>DBS found existing Customer records matching the email or telephone entered. Choose one deliberately, or create a separate Customer.</p><table><thead><tr><th>Customer</th><th>Match</th><th>History</th><th>Action</th></tr></thead><tbody>{rows_html}</tbody></table><p><button class="secondary" type="submit" name="confirm_new" value="1">CREATE SEPARATE CUSTOMER &amp; SAVE ENQUIRY</button></p></div>'
    error_html=f'<div class="error">{esc(error)}</div>' if error else ''
    body=f'''<h1>Customer Details</h1><p><a href="/availability/basket/review">← Back to Basket</a></p>{error_html}
    <div class="card"><h2>Enquiry being saved</h2><p><strong>{esc(item['element_name'])}</strong> — {_fmt_user_date(str(item['arrival_date']))} to {_display_end(str(item['departure_date']),str(item['pricing_method']))}<br>{details}</p><p class="muted">Nothing is written to the Enquiry Register until SAVE ENQUIRY is completed.</p></div>
    <form method="post" action="/availability/basket/customer"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><input type="hidden" name="hold_id" value="{int(hold_id)}">{match_html}
    <div class="card"><h2>Lead Customer</h2><div class="grid">
    <div><label>Family name *</label><input name="last_name" required value="{esc(values.get('last_name',''))}"></div>
    <div><label>First name *</label><input name="first_name" required value="{esc(values.get('first_name',''))}"></div>
    <div><label>Email address *</label><input type="email" name="email" required value="{esc(values.get('email',''))}"></div>
    <div><label>Mobile telephone</label><input name="mobile_phone" value="{esc(values.get('mobile_phone',''))}"></div>
    <div><label>Fixed telephone</label><input name="fixed_phone" value="{esc(values.get('fixed_phone',''))}"></div>
    <div><label>Address line 1</label><input name="address1" value="{esc(values.get('address1',''))}"></div>
    <div><label>Address line 2</label><input name="address2" value="{esc(values.get('address2',''))}"></div>
    <div><label>Town / City</label><input name="town" value="{esc(values.get('town',''))}"></div>
    <div><label>Postcode</label><input name="postcode" value="{esc(values.get('postcode',''))}"></div>
    <div><label>Country</label><input name="country" value="{esc(values.get('country',''))}"></div></div>
    <label>Enquiry notes</label><textarea name="notes" rows="4" style="width:100%;padding:9px;border:1px solid #aeb8c4;border-radius:6px">{esc(values.get('notes',''))}</textarea>
    <p><button type="submit">SAVE ENQUIRY</button></p></div></form>'''
    return layout('Customer Details',body,context)


def register_booking_progress_routes(app):
    database=app.state.database

    @app.post('/availability/new-booking')
    async def new_booking_requirements(request:Request):
        context,company_id=_session_company(database,request);data=await form_data(request);require_csrf(context,data);token=request.cookies.get(COOKIE_NAME,'')
        with database.connect() as c:
            c.execute('DELETE FROM booking_requirement_people WHERE company_id=? AND session_token=?',(company_id,token));c.execute('DELETE FROM booking_requirement_addons WHERE company_id=? AND session_token=?',(company_id,token));c.execute('''INSERT INTO booking_requirement_sessions(session_token,company_id,ready,arrival_date,departure_date,lead_name,updated_at) VALUES (?,?,0,'','','',CURRENT_TIMESTAMP) ON CONFLICT(session_token,company_id) DO UPDATE SET ready=0,arrival_date='',departure_date='',lead_name='',updated_at=CURRENT_TIMESTAMP''',(token,company_id))
        return RedirectResponse('/availability/start',303)

    @app.get('/availability/basket/edit',response_class=HTMLResponse)
    def edit_basket_item(request:Request,hold_id:int,saved:int=0):
        context,company_id=_session_company(database,request);token=request.cookies.get(COOKIE_NAME,'');item=_load_item_into_working(database,company_id,token,hold_id)
        if item is None:return RedirectResponse('/availability/basket/review',303)
        return RedirectResponse(f'/availability/start?edit_hold={int(hold_id)}',303)

    @app.post('/availability/basket/remove-view')
    async def remove_from_progress(request:Request):
        context,company_id=_session_company(database,request);data=await form_data(request);require_csrf(context,data);token=request.cookies.get(COOKIE_NAME,'')
        try:hold_id=int(data.get('hold_id',''))
        except (TypeError,ValueError):return RedirectResponse('/availability/basket/review',303)
        with database.connect() as c:
            c.execute('DELETE FROM hold_requirement_people WHERE hold_id=?',(hold_id,));c.execute('DELETE FROM hold_requirement_addons WHERE hold_id=?',(hold_id,));c.execute('DELETE FROM element_holds WHERE id=? AND company_id=? AND session_token=?',(hold_id,company_id,token))
        return_to=str(data.get('return_to','') or '');return RedirectResponse(return_to if return_to.startswith('/') else '/availability/basket/review',303)

    @app.get('/availability/basket/customer',response_class=HTMLResponse)
    def basket_customer(request:Request,hold_id:int):
        context,company_id=_session_company(database,request);token=request.cookies.get(COOKIE_NAME,'')
        return HTMLResponse(_customer_stage(database,context,company_id,token,hold_id,{}))

    @app.post('/availability/basket/customer',response_class=HTMLResponse)
    async def basket_customer_save(request:Request):
        context,company_id=_session_company(database,request);data=await form_data(request);require_csrf(context,data);token=request.cookies.get(COOKIE_NAME,'')
        try:hold_id=int(data.get('hold_id',''))
        except (TypeError,ValueError):return RedirectResponse('/availability/basket/review',303)
        item,enquiry_values=_hold_enquiry_values(database,company_id,token,hold_id)
        if item is None:return RedirectResponse('/availability/basket/review',303)
        values=_customer_values(data)
        if not values['first_name'] or not values['last_name']:
            return HTMLResponse(_customer_stage(database,context,company_id,token,hold_id,values,'Enter both Family name and First name.'),400)
        if not values['email']:
            return HTMLResponse(_customer_stage(database,context,company_id,token,hold_id,values,'Email address is compulsory.'),400)
        if not values['mobile_phone'] and not values['fixed_phone']:
            return HTMLResponse(_customer_stage(database,context,company_id,token,hold_id,values,'Enter a mobile or fixed telephone number.'),400)
        existing_id=0
        try:existing_id=int(data.get('existing_customer_id','') or 0)
        except (TypeError,ValueError):existing_id=0
        customer_id=None
        if existing_id:
            with database.connect() as c: existing=c.execute('SELECT id FROM customer_records WHERE id=? AND company_id=? AND active=1',(existing_id,company_id)).fetchone()
            if existing is None:return HTMLResponse(_customer_stage(database,context,company_id,token,hold_id,values,'That Customer record is no longer available.'),409)
            customer_id=existing_id
        else:
            matches=customer_matches(database,company_id,email=values['email'],mobile_phone=values['mobile_phone'],fixed_phone=values['fixed_phone'])
            if matches and str(data.get('confirm_new','') or '')!='1':
                return HTMLResponse(_customer_stage(database,context,company_id,token,hold_id,values,'Possible existing Customer found. Choose the correct master Customer or deliberately create a separate Customer.',matches),409)
            now=iso_now()
            with database.connect() as c:
                customer_id=int(c.execute('''INSERT INTO customer_records(company_id,first_name,last_name,email,phone,mobile_phone,fixed_phone,address1,address2,town,postcode,country,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(company_id,values['first_name'],values['last_name'],values['email'],values['mobile_phone'] or values['fixed_phone'],values['mobile_phone'],values['fixed_phone'],values['address1'],values['address2'],values['town'],values['postcode'],values['country'],'',now,now)).lastrowid)
            audit(database,context,company_id,'CUSTOMER_CREATED','customer',customer_id,after={k:values[k] for k in values if k!='notes'})
        enquiry_values['notes']=values['notes'];calculation,_,calc_error=_calculate(database,company_id,enquiry_values)
        if calc_error:
            return HTMLResponse(_customer_stage(database,context,company_id,token,hold_id,values,'The held booking cannot yet be saved as an Enquiry: '+calc_error),409)
        enquiry_id=_save(database,context,company_id,int(customer_id),enquiry_values,calculation)
        return RedirectResponse(f'/operations/enquiries/{enquiry_id}?saved=1',303)

    @app.get('/availability/basket/review',response_class=HTMLResponse)
    def basket_review(request:Request):
        context,company_id=_session_company(database,request);token=request.cookies.get(COOKIE_NAME,'');items=_held_items(database,company_id,token)
        if not items:return HTMLResponse(layout('Basket','<h1>Basket</h1><div class="card"><p>Your basket is empty.</p><p><a class="button" href="/availability/start">Start booking</a></p></div>',context))
        rows_html=''
        for item in items:
            hid=int(item['id']);details=_item_requirements(database,item,company_id);detail_html=' · '.join(esc(x) for x in details) or 'No special requirements';lead=esc(str(item['lead_name'] or '').strip() or '—');rows_html+='<tr>'+f'<td><strong>{lead}</strong></td><td><strong>{esc(item["element_name"])}</strong><br><span class="muted">{esc(item["element_type"])}</span></td><td>{_fmt_user_date(str(item["arrival_date"]))}</td><td>{_display_end(str(item["departure_date"]),str(item["pricing_method"]))}</td><td>{detail_html}</td><td><a class="button" href="/availability/basket/customer?hold_id={hid}">CUSTOMER DETAILS / SAVE ENQUIRY</a> <a class="button secondary mini-action" href="{_edit_url(item)}">EDIT BOOKING</a> '+'<form method="post" action="/availability/basket/remove-view" class="inline-form">'+f'<input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="hold_id" value="{hid}"><input type="hidden" name="return_to" value="/availability/basket/review"><button class="secondary mini-action" type="submit">REMOVE</button></form></td></tr>'
        next_text='Each held booking now continues directly to Customer Details. Customer matching is checked before the Client deliberately saves the Enquiry.' if str(context['role']) in {'operator','supervisor'} else 'Review the held Elements above.'
        body='<h1>Basket</h1>'+booking_progress_strip(database,context,company_id,token)+'<div class="card"><h2>Verify booking contents</h2><table><thead><tr><th>Name</th><th>Element</th><th>Arrival</th><th>End / Departure</th><th>Requirements</th><th>Actions</th></tr></thead><tbody>'+rows_html+'</tbody></table>'+f'<p class="muted">{esc(next_text)}</p><p><a class="button secondary" href="/availability/start">NEW / CHANGE REQUIREMENTS</a></p></div><style>.inline-form{{display:inline;margin:0}}.mini-action{{font-size:12px;padding:5px 8px}}</style>'
        return HTMLResponse(layout('Basket',body,context))
