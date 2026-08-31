from __future__ import annotations
import json
from datetime import date
from urllib.parse import quote_plus
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse,JSONResponse,RedirectResponse
from .app import esc,form_data,layout
from .setup015_catalogue import setup_nav
from .setup015_core import audit,context_for,require_csrf,working_company,years
from .setup015_readiness import element_missing_items,incomplete_elements
CURRENCIES={'EUR':('€','Euro'),'GBP':('£','Pound sterling'),'USD':('$','US dollar'),'CHF':('CHF ','Swiss franc'),'AUD':('A$','Australian dollar'),'CAD':('C$','Canadian dollar'),'NZD':('NZ$','New Zealand dollar')}
def initialise_pricing_usability(database):
    with database.connect() as c:c.execute("UPDATE companies SET base_currency='EUR' WHERE base_currency IS NULL OR TRIM(base_currency)='' ")
def company_currency(database,cid):
    with database.connect() as c:row=c.execute('SELECT base_currency FROM companies WHERE id=?',(cid,)).fetchone()
    code=str(row['base_currency'] if row and row['base_currency'] else 'EUR').upper();return code if code in CURRENCIES else 'EUR'
def currency_symbol(database,cid):return CURRENCIES[company_currency(database,cid)][0]
def _append_duration_to_result(database,cid,result):
    if not result or not result.get('lines'):return
    nights=int(result.get('nights') or 0);eid=int(result.get('element_id') or 0)
    with database.connect() as c:element=c.execute('SELECT name,pricing_method FROM setup_elements WHERE company_id=? AND id=?',(cid,eid)).fetchone();addons={str(r['name']):(int(r['id']),str(r['pricing_method'])) for r in c.execute('SELECT id,name,pricing_method FROM setup_addons WHERE company_id=?',(cid,)).fetchall()}
    def basis(method):
        if method in {'Per night','Per person per night'}:return f'{nights} night(s)'
        if method in {'Per day','Per quantity per day'}:return f'{nights} day(s)'
        if method in {'Per stay','Per package','Fixed once'}:return '1 stay'
        if method=='Per person':return 'per person for stay'
        if method=='Per quantity per night':return f'{nights} night(s)'
        if method=='Per quantity':return 'quantity only'
        return f'{nights} night(s)' if nights else 'stay'
    for line in result['lines']:
        rule=str(line.get('rule') or '')
        if 'Duration:' in rule:continue
        item=str(line.get('item') or '');method=str(element['pricing_method']) if element and item==str(element['name']) else (addons[item][1] if item in addons else str(element['pricing_method']) if element else '')
        line['rule']=(rule+'; ' if rule else '')+'Duration: '+basis(method)
def install_pricing_calculation_transparency():
    from . import webv1_enquiry_builder as builder,setup015_calculator as calculator,webv1_bookings as bookings
    if not getattr(builder,'_duration_wrapper_installed',False):
        original=builder._calculate
        def wrapped(database,cid,values):result,errors,message=original(database,cid,values);_append_duration_to_result(database,cid,result);return result,errors,message
        builder._calculate=wrapped;builder._duration_wrapper_installed=True
    if not getattr(calculator,'_duration_wrapper_installed',False):
        original=calculator._page
        def wrapped_page(database,context,element_id=0,submitted=None,errors=None,message='',result=None):
            if result:result=dict(result);result['lines']=[dict(x) for x in result.get('lines',[])];result.setdefault('element_id',element_id);_append_duration_to_result(database,int(working_company(context)),result)
            return original(database,context,element_id,submitted,errors,message,result)
        calculator._page=wrapped_page;calculator._duration_wrapper_installed=True
    if not getattr(bookings,'_base_currency_wrapper_installed',False):
        original=bookings.convert_enquiry
        def convert(database,context,cid,enquiry_id,workflow_status_id):
            bid=original(database,context,cid,enquiry_id,workflow_status_id)
            with database.connect() as c:c.execute('UPDATE bookings SET currency=? WHERE id=? AND company_id=?',(company_currency(database,cid),bid,cid))
            return bid
        bookings.convert_enquiry=convert;bookings._base_currency_wrapper_installed=True
def register_pricing_usability_routes(app):
    database=app.state.database
    @app.post('/company/base-currency')
    async def set_currency(request:Request):
        context=context_for(database,request)
        if context['role']!='supervisor':raise HTTPException(status_code=403,detail='Supervisor only')
        data=await form_data(request);require_csrf(context,data);cid=int(working_company(context));code=str(data.get('base_currency','')).upper().strip()
        if code not in CURRENCIES:return RedirectResponse('/company/settings',303)
        with database.connect() as c:
            if int(c.execute('SELECT COUNT(*) AS n FROM bookings WHERE company_id=?',(cid,)).fetchone()['n']):return RedirectResponse('/company/settings',303)
            c.execute('UPDATE companies SET base_currency=? WHERE id=?',(code,cid))
        return RedirectResponse('/company/settings?saved=1',303)
    @app.get('/setup/guidance')
    def guidance(request:Request,year:str='',focus_element_id:int=0):
        context=context_for(database,request);cid=int(working_company(context));available=years(database,cid)
        try:selected=int(year) if year else (available[-1] if available else 0)
        except ValueError:selected=available[-1] if available else 0
        if selected not in available:selected=available[-1] if available else 0
        incomplete=incomplete_elements(database,cid,selected) if selected else [];focus=None
        if focus_element_id and selected:
            with database.connect() as c:element=c.execute('SELECT id,name FROM setup_elements WHERE company_id=? AND id=? AND active=1',(cid,focus_element_id)).fetchone()
            if element:focus={'id':int(element['id']),'name':str(element['name']),'count':len(element_missing_items(database,cid,selected,int(element['id'])))}
        return JSONResponse({'year':selected,'incomplete':incomplete,'focus':focus})
    @app.get('/setup/audit/element',response_class=HTMLResponse)
    def element_audit(request:Request,year:int,element_id:int):
        context=context_for(database,request);cid=int(working_company(context))
        with database.connect() as c:element=c.execute('SELECT id,name FROM setup_elements WHERE company_id=? AND id=? AND active=1',(cid,element_id)).fetchone()
        if element is None:return HTMLResponse(layout('Element Setup Audit','<div class="error">Element not found.</div>',context),404)
        missing=element_missing_items(database,cid,year,element_id);body=f'<h1>Setup Audit — {esc(element["name"])}</h1>{setup_nav()}'
        if missing:
            body+=f'<div class="error"><strong>{len(missing)} missing item(s).</strong></div><div class="card"><table><tbody>'
            for item in missing:body+=f'<tr><td>{esc(item["category"])}</td><td><a href="{esc(item["href"])}">{esc(item["text"])}</a></td></tr>'
            body+='</tbody></table></div>'
        else:body+='<div class="ok"><h2>Element active</h2></div>'
        return layout('Element Setup Audit',body,context)
