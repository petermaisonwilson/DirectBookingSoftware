from __future__ import annotations
from urllib.parse import quote_plus
from fastapi import HTTPException,Request
from fastapi.responses import HTMLResponse,RedirectResponse
from .app import esc,form_data,layout
from .db_compat import IntegrityError
from .setup015_core import ADDON_PRICING_METHODS,audit,context_for,require_csrf,rows,selected_year,valid_money,valid_whole,working_company,years

def initialise_features_extras(database):
    with database.connect() as c:c.execute("UPDATE setup_addons SET item_kind='Extra' WHERE item_kind IS NULL OR item_kind NOT IN ('Feature','Extra')")
def _nav():return '<div class="card"><a href="/setup">Setup home</a></div>'
def _features_page(database,context,submitted=None,message='',edit=0):
    cid=int(working_company(context));submitted=submitted or {};items=rows(database,'SELECT * FROM setup_addons WHERE company_id=? ORDER BY active DESC,item_kind,lower(name)',(cid,));current=next((r for r in items if int(r['id'])==int(edit or 0)),None)
    def pick(name,default=''):return submitted.get(name,default) if name in submitted else (current[name] if current is not None and name in current.keys() else default)
    name=str(pick('name',''));kind=str(pick('item_kind','Feature' if int(pick('ask_before_availability',0) or 0) else 'Extra'));group=str(pick('feature_group',''));method=str(pick('pricing_method',ADDON_PRICING_METHODS[0]));ask=bool(int(pick('ask_before_availability',1 if kind=='Feature' else 0) or 0));options=''.join(f'<option {"selected" if method==m else ""}>{esc(m)}</option>' for m in ADDON_PRICING_METHODS);error=f'<div class="error">{esc(message)}</div>' if message else '';body=f'''<h1>Features & Extras</h1>{_nav()}{error}<div class="card"><form method="post" action="/setup/addons"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><input type="hidden" name="id" value="{int(current['id']) if current else ''}"><label>Name</label><input name="name" value="{esc(name)}"><label>Type</label><select name="item_kind"><option {'selected' if kind=='Feature' else ''}>Feature</option><option {'selected' if kind=='Extra' else ''}>Extra</option></select><label>Feature group</label><input name="feature_group" value="{esc(group)}"><label>Pricing method</label><select name="pricing_method">{options}</select><label><input type="checkbox" name="ask_before_availability" {'checked' if ask else ''}> Ask before Availability</label><button>SAVE</button></form></div><div class="card"><table><tbody>'''
    for r in items:body+=f'<tr><td>{esc(r["name"])}</td><td>{esc(r["item_kind"])}</td><td><a href="/setup/addons?edit={int(r["id"])}">Edit</a></td></tr>'
    return layout('Features & Extras',body+'</tbody></table></div>',context)
def _rules_page(database,context,year=None,element_type='',element_id=0,submitted=None,message=''):
    cid=int(working_company(context));selected=selected_year(database,cid,year);types=[str(r['name']) for r in rows(database,'SELECT name FROM setup_element_types WHERE company_id=? AND active=1 ORDER BY lower(name)',(cid,))];typ=element_type if element_type in types else (types[0] if types else '');elements=rows(database,'SELECT * FROM setup_elements WHERE company_id=? AND active=1 AND element_type=? ORDER BY lower(name)',(cid,typ)) if typ else [];chosen=next((e for e in elements if int(e['id'])==int(element_id or 0)),elements[0] if elements else None);addons=rows(database,'SELECT * FROM setup_addons WHERE company_id=? AND active=1 ORDER BY item_kind DESC,feature_group,lower(name)',(cid,));body=f'<h1>Feature / Extra Rules</h1>{_nav()}'
    if selected is None or not typ:return layout('Feature / Extra Rules',body,context)
    chosen_id=int(chosen['id']) if chosen else 0;body+=f'<form method="post" action="/setup/addon-rules"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="year" value="{selected}"><input type="hidden" name="element_type" value="{esc(typ)}"><input type="hidden" name="element_id" value="{chosen_id}"><div class="card"><table><tbody>'
    with database.connect() as c:type_map={int(r['addon_id']):r for r in c.execute('SELECT * FROM setup_type_addons WHERE company_id=? AND year=? AND element_type=?',(cid,selected,typ)).fetchall()};override_map={int(r['addon_id']):r for r in c.execute('SELECT * FROM setup_element_addons WHERE company_id=? AND year=? AND element_id=?',(cid,selected,chosen_id)).fetchall()} if chosen else {}
    for a in addons:
        aid=int(a['id']);base=type_map.get(aid);yes=bool(base and int(base['allowed']));body+=f'<tr><td>{esc(a["name"])}</td><td><input type="checkbox" name="ty_{aid}" {"checked" if yes else ""}></td><td><input name="tymin_{aid}" value="{esc(base["min_qty"] if base and base["min_qty"] is not None else "")}"></td><td><input name="tymax_{aid}" value="{esc(base["max_qty"] if base and base["max_qty"] is not None else "")}"></td><td><input name="tyrate_{aid}" value="{esc(base["rate"] if base and base["rate"] is not None else "")}"></td></tr>';ov=override_map.get(aid);state=str(ov['state']) if ov else 'I';body+=f'<tr><td>Override {esc(a["name"])}</td><td><select name="ov_{aid}"><option value="I" {"selected" if state=="I" else ""}>Default</option><option value="Y" {"selected" if state=="Y" else ""}>Yes</option><option value="N" {"selected" if state=="N" else ""}>No</option></select></td><td><input name="ovmin_{aid}" value="{esc(ov["min_qty"] if ov and ov["min_qty"] is not None else "")}"></td><td><input name="ovmax_{aid}" value="{esc(ov["max_qty"] if ov and ov["max_qty"] is not None else "")}"></td><td><input name="ovrate_{aid}" value="{esc(ov["rate"] if ov and ov["rate"] is not None else "")}"></td></tr>'
    return layout('Feature / Extra Rules',body+'</tbody></table><button>SAVE CHANGES</button></div></form>',context)
def _remove_route(app,path):app.router.routes[:]=[r for r in app.router.routes if getattr(r,'path',None)!=path]
def register_features_extras_routes(app):
    database=app.state.database;_remove_route(app,'/setup/addons');_remove_route(app,'/setup/addon-rules')
    @app.get('/setup/addons',response_class=HTMLResponse)
    def page(request:Request,edit:int=0):return _features_page(database,context_for(database,request),edit=edit)
    @app.post('/setup/addons')
    async def save(request:Request):
        context=context_for(database,request);cid=int(working_company(context));data=await form_data(request);require_csrf(context,data);name=str(data.get('name','')).strip();kind=str(data.get('item_kind','Feature'));group=str(data.get('feature_group','')).strip();method=str(data.get('pricing_method',''));raw=str(data.get('id',''));ask=1 if 'ask_before_availability' in data else 0
        if kind=='Extra':group=''
        try:
            with database.connect() as c:
                if raw.isdigit():old=c.execute('SELECT * FROM setup_addons WHERE company_id=? AND id=?',(cid,int(raw))).fetchone();entity_id=int(raw);before=dict(old);c.execute('UPDATE setup_addons SET name=?,pricing_method=?,item_kind=?,feature_group=?,ask_before_availability=? WHERE company_id=? AND id=?',(name,method,kind,group,ask,cid,entity_id))
                else:before=None;entity_id=c.execute('INSERT INTO setup_addons(company_id,name,pricing_method,item_kind,feature_group,ask_before_availability) VALUES (?,?,?,?,?,?)',(cid,name,method,kind,group,ask)).lastrowid
        except IntegrityError:return HTMLResponse(_features_page(database,context,data,'That Feature / Extra name already exists.',int(raw) if raw.isdigit() else 0),400)
        return RedirectResponse('/setup/addons',303)
    @app.get('/setup/addon-rules',response_class=HTMLResponse)
    def rules(request:Request,year:str='',element_type:str='',element:int=0):
        context=context_for(database,request);return _rules_page(database,context,selected_year(database,working_company(context),year),element_type,element)
    @app.post('/setup/addon-rules')
    async def save_rules(request:Request):
        context=context_for(database,request);cid=int(working_company(context));data=await form_data(request);require_csrf(context,data);year=int(data.get('year','0'));typ=str(data.get('element_type',''));eid=int(data.get('element_id','0') or 0);addons=rows(database,'SELECT id FROM setup_addons WHERE company_id=? AND active=1',(cid,))
        with database.connect() as c:
            for a in addons:
                aid=int(a['id']);allowed=1 if f'ty_{aid}' in data else 0;mn=int(data.get(f'tymin_{aid}','0') or 0) if allowed else None;mx=int(data.get(f'tymax_{aid}','0') or 0) if allowed else None;rate=float(str(data.get(f'tyrate_{aid}','0') or 0).replace(',','.')) if allowed else None;c.execute('INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(company_id,year,element_type,addon_id) DO UPDATE SET allowed=excluded.allowed,min_qty=excluded.min_qty,max_qty=excluded.max_qty,rate=excluded.rate',(cid,year,typ,aid,allowed,mn,mx,rate));state=str(data.get(f'ov_{aid}','I'))
                if state=='I':c.execute('DELETE FROM setup_element_addons WHERE company_id=? AND year=? AND element_id=? AND addon_id=?',(cid,year,eid,aid))
                else:c.execute('INSERT INTO setup_element_addons(company_id,year,element_id,addon_id,state,min_qty,max_qty,rate) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(company_id,year,element_id,addon_id) DO UPDATE SET state=excluded.state,min_qty=excluded.min_qty,max_qty=excluded.max_qty,rate=excluded.rate',(cid,year,eid,aid,state,int(data.get(f'ovmin_{aid}','0') or 0) if state=='Y' else None,int(data.get(f'ovmax_{aid}','0') or 0) if state=='Y' else None,float(str(data.get(f'ovrate_{aid}','0') or 0).replace(',','.')) if state=='Y' else None))
        return RedirectResponse(f'/setup/addon-rules?year={year}&element_type={quote_plus(typ)}&element={eid}',303)
