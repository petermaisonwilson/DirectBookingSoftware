from __future__ import annotations
from datetime import date
from fastapi import Request
from fastapi.responses import HTMLResponse,RedirectResponse
from .app import esc,form_data,layout
from .db_compat import IntegrityError
from .setup015_core import ADDON_PRICING_METHODS,ELEMENT_PRICING_METHODS,audit,context_for,copy_previous_year,require_csrf,rows,valid_money,working_company,years
from .webv1_ordering import person_type_rows,setup_sortable_table_bits,sortable_menu_html

def setup_nav():return '<div class="card"><a href="/setup">Setup home</a> · <a href="/setup/element-types">Element Types</a> · <a href="/setup/elements">Elements</a> · <a href="/setup/person-types">Person Types</a> · <a href="/setup/addons">Features & Extras</a> · <a href="/setup/years">Years</a> · <a href="/setup/pricing">Seasonal pricing</a></div>'
def error_box(message):return f'<div class="error">{esc(message)}</div>' if message else ''
def field_style(error,extra=''):return (extra+';border:2px solid #b42318;background:#fff1f0' if error else extra).lstrip(';')
def _element_types_page(database,context,submitted=None,errors=None,message='',edit=0):
    cid=working_company(context);items=rows(database,'SELECT * FROM setup_element_types WHERE company_id=? ORDER BY active DESC,lower(name)',(cid,));current=next((r for r in items if int(r['id'])==int(edit or 0)),None);name=(submitted or {}).get('name',current['name'] if current else '');body=f'<h1>Element Types</h1>{setup_nav()}{error_box(message)}<div class="card"><form method="post" action="/setup/element-types"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="id" value="{esc(current["id"] if current else "")}"><label>Name</label><input name="name" value="{esc(name)}"><button>Save</button></form></div><div class="card"><table><tbody>'
    for r in items:body+=f'<tr><td>{esc(r["name"])}</td><td><a href="/setup/element-types?edit={int(r["id"])}">Rename</a></td></tr>'
    return layout('Element Types',body+'</tbody></table></div>',context)
def _elements_page(database,context,submitted=None,errors=None,message='',edit=0,saved=0):
    cid=working_company(context);items=rows(database,'SELECT * FROM setup_elements WHERE company_id=? ORDER BY element_type,lower(name)',(cid,));current=next((r for r in items if int(r['id'])==int(edit or 0)),None);types=rows(database,'SELECT * FROM setup_element_types WHERE company_id=? AND active=1 ORDER BY lower(name)',(cid,));submitted=submitted or {};name=submitted.get('name',current['name'] if current else '');typ=submitted.get('element_type',current['element_type'] if current else '');method=submitted.get('pricing_method',current['pricing_method'] if current else ELEMENT_PRICING_METHODS[0]);options=''.join(f'<option {"selected" if str(t["name"])==typ else ""}>{esc(t["name"])}</option>' for t in types);methods=''.join(f'<option {"selected" if m==method else ""}>{m}</option>' for m in ELEMENT_PRICING_METHODS);body=f'<h1>Elements</h1>{setup_nav()}{error_box(message)}<div class="card"><form method="post" action="/setup/elements"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="id" value="{esc(current["id"] if current else "")}"><label>Name</label><input name="name" value="{esc(name)}"><label>Element Type</label><select name="element_type">{options}</select><label>Pricing method</label><select name="pricing_method">{methods}</select><button>Save Element</button></form></div><div class="card"><table><tbody>'
    for r in items:body+=f'<tr><td>{esc(r["name"])}</td><td>{esc(r["element_type"])}</td><td><a href="/setup/elements?edit={int(r["id"])}">Edit</a></td></tr>'
    return layout('Elements',body+'</tbody></table></div>',context)
def _years_page(database,context,submitted=None,errors=None,message=''):
    available=years(database,working_company(context));return layout('Pricing years',f'<h1>Pricing years</h1>{setup_nav()}{error_box(message)}<div class="card"><form method="post" action="/setup/years/new"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input name="year" value="{date.today().year}"><button>Create blank year</button></form><p>{", ".join(map(str,available))}</p></div>',context)
def register_catalogue_routes(app):
    database=app.state.database
    @app.get('/setup',response_class=HTMLResponse)
    def home(request:Request):
        context=context_for(database,request);company=database.company(working_company(context));cards=[('element_types','<h2>Element Types</h2><a href="/setup/element-types">Open</a>'),('elements','<h2>Elements</h2><a href="/setup/elements">Open</a>'),('person_types','<h2>Person Types</h2><a href="/setup/person-types">Open</a>'),('features','<h2>Features & Extras</h2><a href="/setup/addons">Open</a>'),('years','<h2>Annual setup</h2><a href="/setup/years">Open</a>')];return layout('Setup',f'<h1>{esc(company["name"])} — Setup</h1>'+sortable_menu_html(database,context,'setup',cards),context)
    @app.get('/setup/element-types',response_class=HTMLResponse)
    def types(request:Request,edit:int=0):return _element_types_page(database,context_for(database,request),edit=edit)
    @app.post('/setup/element-types')
    async def save_type(request:Request):
        context=context_for(database,request);cid=working_company(context);data=await form_data(request);require_csrf(context,data);name=data.get('name','').strip();raw=data.get('id','')
        try:
            with database.connect() as c:
                if raw.isdigit():old=c.execute('SELECT name FROM setup_element_types WHERE id=? AND company_id=?',(int(raw),cid)).fetchone();c.execute('UPDATE setup_element_types SET name=? WHERE id=? AND company_id=?',(name,int(raw),cid));c.execute('UPDATE setup_elements SET element_type=? WHERE company_id=? AND element_type=?',(name,cid,old['name']));entity=int(raw)
                else:entity=int(c.execute('INSERT INTO setup_element_types(company_id,name) VALUES (?,?)',(cid,name)).lastrowid)
        except IntegrityError:return HTMLResponse(_element_types_page(database,context,data,{'name'},'That Element Type already exists.'),400)
        return RedirectResponse('/setup/element-types',303)
    @app.get('/setup/elements',response_class=HTMLResponse)
    def elements(request:Request,edit:int=0):return _elements_page(database,context_for(database,request),edit=edit)
    @app.post('/setup/elements')
    async def save_element(request:Request):
        context=context_for(database,request);cid=working_company(context);data=await form_data(request);require_csrf(context,data);name=data.get('name','').strip();typ=data.get('element_type','').strip();method=data.get('pricing_method','');raw=data.get('id','')
        try:
            with database.connect() as c:
                if raw.isdigit():c.execute('UPDATE setup_elements SET name=?,element_type=?,pricing_method=? WHERE id=? AND company_id=?',(name,typ,method,int(raw),cid))
                else:c.execute('INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price) VALUES (?,?,?,?,0)',(cid,name,typ,method))
        except IntegrityError:return HTMLResponse(_elements_page(database,context,data,{'name'},'That Element name already exists.'),400)
        return RedirectResponse('/setup/elements',303)
    @app.get('/setup/years',response_class=HTMLResponse)
    def year_page(request:Request):return _years_page(database,context_for(database,request))
    @app.post('/setup/years/new')
    async def new_year(request:Request):
        context=context_for(database,request);cid=working_company(context);data=await form_data(request);require_csrf(context,data)
        try:y=int(data.get('year',''));assert 2000<=y<=2200
        except (ValueError,AssertionError):return HTMLResponse(_years_page(database,context,data,{'new_year'},'Enter a valid year.'),400)
        try:
            with database.connect() as c:c.execute('INSERT INTO setup_years(company_id,year) VALUES (?,?)',(cid,y))
        except IntegrityError:return HTMLResponse(_years_page(database,context,data,{'new_year'},'That year already exists.'),400)
        return RedirectResponse('/setup/years',303)
