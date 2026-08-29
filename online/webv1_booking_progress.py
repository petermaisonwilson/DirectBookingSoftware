from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.parse import quote_plus

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import COOKIE_NAME, esc, form_data, layout
from .setup015_core import require_csrf, rows
from .webv1_availability import _session_company


def _fmt_user_date(value):
    try:return date.fromisoformat(value).strftime('%d/%m/%Y')
    except (TypeError,ValueError):return value or ''

def _display_end(value,pricing_method):
    try:end=date.fromisoformat(value)
    except (TypeError,ValueError):return value or ''
    if str(pricing_method or '').strip().lower()=='per day':end-=timedelta(days=1)
    return end.strftime('%d/%m/%Y')

def _held_items(database,company_id,token):
    return rows(database,'''SELECT h.id,h.element_id,h.arrival_date,h.departure_date,h.expires_at,e.name AS element_name,e.element_type,e.pricing_method FROM element_holds h JOIN setup_elements e ON e.id=h.element_id AND e.company_id=h.company_id WHERE h.company_id=? AND h.session_token=? ORDER BY h.created_at,h.id''',(company_id,token))

def _item_requirements(database,hold_id):
    details=[]
    for p in rows(database,'''SELECT hp.quantity,hp.ages_json,pt.name FROM hold_requirement_people hp LEFT JOIN setup_person_types pt ON pt.id=hp.person_type_id AND pt.company_id=hp.company_id WHERE hp.hold_id=? AND hp.quantity>0 ORDER BY pt.name''',(hold_id,)):
        label=f'{int(p["quantity"])} {str(p["name"] or "person")}'
        try:ages=json.loads(p['ages_json'] or '[]')
        except json.JSONDecodeError:ages=[]
        if ages:label+=' (age'+('s ' if len(ages)!=1 else ' ')+', '.join(str(x) for x in ages)+')'
        details.append(label)
    for a in rows(database,'''SELECT ha.quantity,sa.name FROM hold_requirement_addons ha LEFT JOIN setup_addons sa ON sa.id=ha.addon_id AND sa.company_id=ha.company_id WHERE ha.hold_id=? AND ha.quantity>0 ORDER BY sa.name''',(hold_id,)):details.append(f'{str(a["name"] or "Requirement")} {int(a["quantity"])}')
    return details

def _edit_url(item):return '/availability/basket/edit?hold_id='+str(int(item['id']))

def booking_progress_strip(database,context,company_id,token):
    items=_held_items(database,company_id,token)
    if not items:return ''
    chips=[]
    for item in items:
        chips.append('<div class="booking-progress-item">'+f'<strong>{esc(item["element_name"])}</strong> <span>{_fmt_user_date(str(item["arrival_date"]))}–{_display_end(str(item["departure_date"]),str(item["pricing_method"]))}</span> <a class="button secondary mini-action" href="{_edit_url(item)}">EDIT</a> '+'<form method="post" action="/availability/basket/remove-view" class="inline-form">'+f'<input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="hold_id" value="{int(item["id"])}"><button type="submit" class="secondary mini-action">REMOVE</button></form></div>')
    return '<div class="card persistent-booking-progress"><div class="booking-progress-heading"><strong>Booking in progress</strong><span class="booking-progress-actions"><form method="post" action="/availability/new-booking" class="inline-form">'+f'<input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><button type="submit">NEW BOOKING</button></form> '+'<a class="button secondary" href="/availability/basket/review">VIEW BASKET</a></span></div>'+''.join(chips)+'''<style>.persistent-booking-progress{border-color:#9bb3c9;background:#f7fbff}.booking-progress-heading{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:8px}.booking-progress-actions{display:inline-flex;gap:6px;align-items:center;flex-wrap:wrap}.booking-progress-item{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:7px 0;border-top:1px solid #d8e2eb}.booking-progress-item:first-of-type{border-top:0}.inline-form{display:inline;margin:0}.mini-action{font-size:12px;padding:5px 8px}</style></div>'''

def register_booking_progress_routes(app):
    database=app.state.database
    @app.post('/availability/new-booking')
    async def new_booking_requirements(request:Request):
        context,company_id=_session_company(database,request);data=await form_data(request);require_csrf(context,data);token=request.cookies.get(COOKIE_NAME,'')
        with database.connect() as c:
            c.execute('DELETE FROM booking_requirement_people WHERE company_id=? AND session_token=?',(company_id,token));c.execute('DELETE FROM booking_requirement_addons WHERE company_id=? AND session_token=?',(company_id,token));c.execute('''INSERT INTO booking_requirement_sessions(session_token,company_id,ready,arrival_date,departure_date,updated_at) VALUES (?,?,0,'','',CURRENT_TIMESTAMP) ON CONFLICT(session_token,company_id) DO UPDATE SET ready=0,arrival_date='',departure_date='',updated_at=CURRENT_TIMESTAMP''',(token,company_id))
        return RedirectResponse('/availability/start',303)
    @app.get('/availability/basket/edit')
    def edit_basket_item(request:Request,hold_id:int):
        context,company_id=_session_company(database,request);token=request.cookies.get(COOKIE_NAME,'')
        with database.connect() as c:
            item=c.execute('''SELECT h.*,e.element_type FROM element_holds h JOIN setup_elements e ON e.id=h.element_id AND e.company_id=h.company_id WHERE h.id=? AND h.company_id=? AND h.session_token=?''',(hold_id,company_id,token)).fetchone()
            if item is None:return RedirectResponse('/availability/basket/review',303)
            c.execute('DELETE FROM booking_requirement_people WHERE company_id=? AND session_token=?',(company_id,token));c.execute('DELETE FROM booking_requirement_addons WHERE company_id=? AND session_token=?',(company_id,token))
            c.execute('''INSERT INTO booking_requirement_people(session_token,company_id,person_type_id,quantity,ages_json) SELECT ?,company_id,person_type_id,quantity,ages_json FROM hold_requirement_people WHERE hold_id=?''',(token,hold_id));c.execute('''INSERT INTO booking_requirement_addons(session_token,company_id,addon_id,quantity) SELECT ?,company_id,addon_id,quantity FROM hold_requirement_addons WHERE hold_id=?''',(token,hold_id))
            c.execute('''INSERT INTO booking_requirement_sessions(session_token,company_id,ready,arrival_date,departure_date,updated_at) VALUES (?,?,1,?,?,CURRENT_TIMESTAMP) ON CONFLICT(session_token,company_id) DO UPDATE SET ready=1,arrival_date=excluded.arrival_date,departure_date=excluded.departure_date,updated_at=CURRENT_TIMESTAMP''',(token,company_id,str(item['arrival_date']),str(item['departure_date'])))
        q='element_type='+quote_plus(str(item['element_type']))+'&arrival='+quote_plus(str(item['arrival_date']))+'&departure='+quote_plus(str(item['departure_date']))+'&edit_hold='+str(hold_id)
        return RedirectResponse('/availability/calendar-v2?'+q,303)
    @app.post('/availability/basket/remove-view')
    async def remove_from_progress(request:Request):
        context,company_id=_session_company(database,request);data=await form_data(request);require_csrf(context,data);token=request.cookies.get(COOKIE_NAME,'')
        try:hold_id=int(data.get('hold_id',''))
        except (TypeError,ValueError):return RedirectResponse('/availability/basket/review',303)
        with database.connect() as c:c.execute('DELETE FROM hold_requirement_people WHERE hold_id=?',(hold_id,));c.execute('DELETE FROM hold_requirement_addons WHERE hold_id=?',(hold_id,));c.execute('DELETE FROM element_holds WHERE id=? AND company_id=? AND session_token=?',(hold_id,company_id,token))
        return_to=str(data.get('return_to','') or '');return RedirectResponse(return_to if return_to.startswith('/') else '/availability/basket/review',303)
    @app.get('/availability/basket/review',response_class=HTMLResponse)
    def basket_review(request:Request):
        context,company_id=_session_company(database,request);token=request.cookies.get(COOKIE_NAME,'');items=_held_items(database,company_id,token)
        if not items:return HTMLResponse(layout('Basket','<h1>Basket</h1><div class="card"><p>Your basket is empty.</p><p><a class="button" href="/availability/start">Start booking</a></p></div>',context))
        rows_html=''
        for item in items:
            hid=int(item['id']);details=_item_requirements(database,hid);detail_html=' · '.join(esc(x) for x in details) or 'No special requirements';rows_html+='<tr>'+f'<td><strong>{esc(item["element_name"])}</strong><br><span class="muted">{esc(item["element_type"])}</span></td><td>{_fmt_user_date(str(item["arrival_date"]))}</td><td>{_display_end(str(item["departure_date"]),str(item["pricing_method"]))}</td><td>{detail_html}</td><td><a class="button secondary mini-action" href="{_edit_url(item)}">EDIT</a> '+'<form method="post" action="/availability/basket/remove-view" class="inline-form">'+f'<input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="hold_id" value="{hid}"><input type="hidden" name="return_to" value="/availability/basket/review"><button class="secondary mini-action" type="submit">REMOVE</button></form></td></tr>'
        next_text='Review the held Elements above. The next workflow stage will create an Offer or Confirm the Booking from this verified basket.' if str(context['role']) in {'operator','supervisor'} else 'Review the held Elements above. The next workflow stage will confirm the booking from this verified basket.'
        body='<h1>Basket</h1>'+booking_progress_strip(database,context,company_id,token)+'<div class="card"><h2>Verify booking contents</h2><table><thead><tr><th>Element</th><th>Arrival</th><th>End / Departure</th><th>Requirements</th><th>Actions</th></tr></thead><tbody>'+rows_html+'</tbody></table>'+f'<p class="muted">{esc(next_text)}</p><p><a class="button secondary" href="/availability/calendar-v2">BACK TO AVAILABILITY</a></p></div><style>.inline-form{{display:inline;margin:0}}.mini-action{{font-size:12px;padding:5px 8px}}</style>'
        return HTMLResponse(layout('Basket',body,context))
