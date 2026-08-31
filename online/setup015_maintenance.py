from __future__ import annotations
from datetime import date
from urllib.parse import quote_plus
from fastapi import Request
from fastapi.responses import HTMLResponse,RedirectResponse
from .app import esc,form_data,layout
from .setup015_annual import _addon_page,_pricing_page
from .setup015_catalogue import _element_types_page,_elements_page,_years_page,error_box,field_style,setup_nav
from .setup015_core import ADDON_PRICING_METHODS,audit,context_for,require_csrf,rows,selected_year,working_company,years

def _table_exists(connection,table):return connection.table_exists(table)
def _count_if_table(connection,table,where,params):return int(connection.execute(f'SELECT COUNT(*) AS n FROM {table} WHERE {where}',params).fetchone()['n']) if _table_exists(connection,table) else 0
def _delete_if_table(connection,table,where,params):
    if _table_exists(connection,table):connection.execute(f'DELETE FROM {table} WHERE {where}',params)
def _append_before_main_end(html,extra):return html.replace('</main>',extra+'</main>',1) if '</main>' in html else html+extra
def _confirm_form(context,action,hidden,label,*,secondary=False,confirm=''):
    fields=''.join(f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">' for k,v in hidden.items());cls=' class="secondary"' if secondary else '';onsubmit=f' onsubmit="return confirm(\'{esc(confirm)}\')"' if confirm else '';return f'<form method="post" action="{action}" style="display:inline"{onsubmit}><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}">{fields}<button{cls}>{esc(label)}</button></form>'
def _historical_count(c,kind,item_id,cid):
    checks={'element':[('enquiry_requests','element_id'),('booking_elements','element_id')],'person':[('enquiry_people','person_type_id'),('booking_people','person_type_id')],'addon':[('enquiry_addons','addon_id'),('booking_addons','addon_id')]}.get(kind,[]);return sum(_count_if_table(c,t,f'company_id=? AND {col}=?',(cid,item_id)) for t,col in checks)
def _year_has_history(c,cid,year):return _count_if_table(c,'enquiries',"company_id=? AND substr(arrival_date,1,4)=?",(cid,str(year)))+_count_if_table(c,'bookings',"company_id=? AND substr(arrival_date,1,4)=?",(cid,str(year)))>0
def register_setup_maintenance_routes(app):
    database=app.state.database
    @app.post('/setup/maintenance/catalog/save')
    async def catalog_save(request:Request):
        context=context_for(database,request);cid=working_company(context);data=await form_data(request);require_csrf(context,data);kind=data.get('kind','');raw=data.get('id','');iid=int(raw) if raw.isdigit() else 0;name=data.get('name','').strip();table='setup_person_types' if kind=='person' else 'setup_addons';path='/setup/person-types' if kind=='person' else '/setup/addons'
        if kind not in {'person','addon'} or not name:return RedirectResponse(path,303)
        short=data.get('short_name','').strip()[:8];method=data.get('pricing_method','')
        try:
            with database.connect() as c:
                before=c.execute(f'SELECT * FROM {table} WHERE id=? AND company_id=?',(iid,cid)).fetchone() if iid else None
                if kind=='person':entity_id=iid if iid else int(c.execute('INSERT INTO setup_person_types(company_id,name,short_name) VALUES (?,?,?)',(cid,name,short)).lastrowid);c.execute('UPDATE setup_person_types SET name=?,short_name=? WHERE id=? AND company_id=?',(name,short,entity_id,cid)) if iid else None
                else:entity_id=iid if iid else int(c.execute('INSERT INTO setup_addons(company_id,name,pricing_method) VALUES (?,?,?)',(cid,name,method)).lastrowid);c.execute('UPDATE setup_addons SET name=?,pricing_method=? WHERE id=? AND company_id=?',(name,method,entity_id,cid)) if iid else None
        except Exception:return RedirectResponse(path+'?message='+quote_plus('That name already exists or could not be saved.'),303)
        return RedirectResponse(path,303)
    @app.post('/setup/maintenance/catalog/toggle')
    async def toggle(request:Request):
        context=context_for(database,request);cid=working_company(context);data=await form_data(request);require_csrf(context,data);kind=data.get('kind','');iid=int(data.get('id','0'));table={'element':'setup_elements','person':'setup_person_types','addon':'setup_addons'}[kind];path={'element':'/setup/elements','person':'/setup/person-types','addon':'/setup/addons'}[kind]
        with database.connect() as c:row=c.execute(f'SELECT active FROM {table} WHERE id=? AND company_id=?',(iid,cid)).fetchone();c.execute(f'UPDATE {table} SET active=? WHERE id=? AND company_id=?',(0 if row['active'] else 1,iid,cid))
        return RedirectResponse(path,303)
    @app.post('/setup/maintenance/catalog/delete')
    async def delete(request:Request):
        context=context_for(database,request);cid=working_company(context);data=await form_data(request);require_csrf(context,data);kind=data.get('kind','');iid=int(data.get('id','0'));table={'element':'setup_elements','person':'setup_person_types','addon':'setup_addons'}[kind];path={'element':'/setup/elements','person':'/setup/person-types','addon':'/setup/addons'}[kind]
        with database.connect() as c:
            if _historical_count(c,kind,iid,cid):return RedirectResponse(path+'?message='+quote_plus('This item has saved history and cannot be deleted.'),303)
            c.execute(f'DELETE FROM {table} WHERE id=? AND company_id=?',(iid,cid))
        return RedirectResponse(path,303)
