from __future__ import annotations
from datetime import date,timedelta
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse,RedirectResponse
from .app import esc,form_data,layout
from .setup015_core import audit,context_for,require_csrf,rows,working_company
from .webv1_addon_person import addon_person_mode,addon_person_rates

def initialise_addon_when(database):
    with database.connect() as c:c.execute("INSERT INTO setup_addon_when_options(company_id,addon_id,option_code,label,active,sort_order) SELECT company_id,id,'every_day','Every day',1,1 FROM setup_addons ON CONFLICT DO NOTHING");c.execute("INSERT INTO setup_addon_when_options(company_id,addon_id,option_code,label,active,sort_order) SELECT company_id,id,'selected_days','Selected days',0,2 FROM setup_addons ON CONFLICT DO NOTHING")
def when_options(database,cid,aid,*,active_only=True):
    sql='SELECT * FROM setup_addon_when_options WHERE company_id=? AND addon_id=?';params=[cid,aid]
    if active_only:sql+=' AND active=1'
    return rows(database,sql+' ORDER BY sort_order,option_code',tuple(params))
def when_payload(database,cid,addons):return {str(int(a['id'])):[{'code':str(r['option_code']),'label':str(r['label'])} for r in when_options(database,cid,int(a['id']))] for a in addons}
def stay_dates(arrival,departure):
    if not arrival or not departure:return []
    try:start=date.fromisoformat(arrival);end=date.fromisoformat(departure)
    except ValueError:return []
    return [(start+timedelta(days=i)).isoformat() for i in range((end-start).days)] if end>start else []
def register_addon_when_routes(app):
    database=app.state.database
    @app.get('/setup/addons/when',response_class=HTMLResponse)
    def page(request:Request,saved:int=0,year:int=0,error:str=''):
        context=context_for(database,request);cid=int(working_company(context));addons=rows(database,'SELECT * FROM setup_addons WHERE company_id=? ORDER BY active DESC,lower(name)',(cid,));people=rows(database,'SELECT * FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY lower(name)',(cid,));available=[int(r['year']) for r in rows(database,'SELECT year FROM setup_years WHERE company_id=? ORDER BY year DESC',(cid,))];selected=year if year in available else (available[0] if available else 0);body='<h1>Add-on Timings & Person Pricing</h1>'
        for addon in addons:
            aid=int(addon['id']);opts={str(r['option_code']):r for r in when_options(database,cid,aid,active_only=False)};mode=addon_person_mode(database,cid,aid);rates=addon_person_rates(database,cid,aid,selected) if selected else {};body+=f'<div class="card"><h2>{esc(addon["name"])}</h2><form method="post" action="/setup/addons/when"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="addon_id" value="{aid}"><input type="hidden" name="year" value="{selected}"><label><input type="checkbox" name="every_active" value="1" {"checked" if opts.get("every_day") and opts["every_day"]["active"] else ""}> Every day</label><input name="every_label" value="{esc(opts.get("every_day")["label"] if opts.get("every_day") else "Every day")}"><label><input type="checkbox" name="selected_active" value="1" {"checked" if opts.get("selected_days") and opts["selected_days"]["active"] else ""}> Selected days</label><input name="selected_label" value="{esc(opts.get("selected_days")["label"] if opts.get("selected_days") else "Selected days")}"><select name="pricing_mode"><option value="single" {"selected" if mode=="single" else ""}>One price for everyone</option><option value="person_type" {"selected" if mode=="person_type" else ""}>Price by Person Type</option></select>'
            for p in people:body+=f'<label>{esc(p["short_name"] or p["name"])} price</label><input name="person_rate_{int(p["id"])}" value="{rates.get(int(p["id"]),"")}">'
            body+='<button>Save Add-on options</button></form></div>'
        return layout('Add-on Timings & Person Pricing',body,context)
    @app.post('/setup/addons/when')
    async def save(request:Request):
        context=context_for(database,request);cid=int(working_company(context));data=await form_data(request);require_csrf(context,data)
        try:aid=int(data.get('addon_id',''))
        except ValueError:raise HTTPException(status_code=400,detail='Invalid Add-on')
        every=1 if data.get('every_active')=='1' else 0;selected_active=1 if data.get('selected_active')=='1' else 0
        if not every and not selected_active:every=1
        mode=data.get('pricing_mode','single');year=int(data.get('year','0') or 0)
        with database.connect() as c:
            c.execute('INSERT INTO setup_addon_when_options(company_id,addon_id,option_code,label,active,sort_order) VALUES (?,?,?,?,?,1) ON CONFLICT(company_id,addon_id,option_code) DO UPDATE SET label=excluded.label,active=excluded.active',(cid,aid,'every_day',data.get('every_label','Every day'),every));c.execute('INSERT INTO setup_addon_when_options(company_id,addon_id,option_code,label,active,sort_order) VALUES (?,?,?,?,?,2) ON CONFLICT(company_id,addon_id,option_code) DO UPDATE SET label=excluded.label,active=excluded.active',(cid,aid,'selected_days',data.get('selected_label','Selected days'),selected_active));c.execute('INSERT INTO setup_addon_person_pricing(company_id,addon_id,pricing_mode) VALUES (?,?,?) ON CONFLICT(company_id,addon_id) DO UPDATE SET pricing_mode=excluded.pricing_mode',(cid,aid,mode))
            if mode=='person_type' and year:
                for p in rows(database,'SELECT id FROM setup_person_types WHERE company_id=? AND active=1',(cid,)):
                    raw=str(data.get(f'person_rate_{int(p["id"])}','')).strip()
                    if raw:c.execute('INSERT INTO setup_addon_person_rates(company_id,addon_id,year,person_type_id,rate) VALUES (?,?,?,?,?) ON CONFLICT(company_id,addon_id,year,person_type_id) DO UPDATE SET rate=excluded.rate',(cid,aid,year,int(p['id']),float(raw.replace(',','.'))))
        return RedirectResponse(f'/setup/addons/when?saved=1&year={year}',303)
