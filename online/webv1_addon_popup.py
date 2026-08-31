from __future__ import annotations
from urllib.parse import quote_plus
from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from .app import COOKIE_NAME, esc, form_data
from .setup015_core import audit, context_for, require_csrf, rows, working_company
from .webv1_rule_resolver import resolve_element_item_rule

def initialise_addon_popup(database)->None:return None
def popup_addons_for_element(database,company_id,year,element):
    result=[];eid=int(element['id'])
    for addon in rows(database,'SELECT a.*,COALESCE(p.show_popup,1) AS show_popup FROM setup_addons a LEFT JOIN setup_element_popup_items p ON p.company_id=a.company_id AND p.element_id=? AND p.addon_id=a.id WHERE a.company_id=? AND a.active=1 ORDER BY lower(a.name)',(eid,company_id)):
        if not int(addon['show_popup']):continue
        rule=resolve_element_item_rule(database,company_id,year,element,int(addon['id']))
        if bool(rule['allowed']):result.append({'id':int(addon['id']),'name':str(addon['name']),'available':True})
    return result
def _remove_element_list(text):
    marker='<div class="card"><table><thead><tr><th>Name</th><th>Element Type</th>';start=text.find(marker)
    if start<0:return text
    end=text.find('</div>',start);return text if end<0 else text[:start]+text[end+6:]
def register_addon_popup_routes(app):
    database=app.state.database
    @app.post('/setup/elements/popup')
    async def toggle(request:Request):
        context=context_for(database,request);cid=int(working_company(context));data=await form_data(request);require_csrf(context,data);re=data.get('element_id','');ra=data.get('addon_id','')
        if not re.isdigit() or not ra.isdigit():return RedirectResponse('/setup/elements?message='+quote_plus('Invalid popup item.'),303)
        eid,aid=int(re),int(ra)
        with database.connect() as c:
            if c.execute('SELECT id FROM setup_elements WHERE id=? AND company_id=?',(eid,cid)).fetchone() is None or c.execute('SELECT id FROM setup_addons WHERE id=? AND company_id=? AND active=1',(aid,cid)).fetchone() is None:return RedirectResponse('/setup/elements?message='+quote_plus('Element or popup item was not found.'),303)
            existing=c.execute('SELECT show_popup FROM setup_element_popup_items WHERE company_id=? AND element_id=? AND addon_id=?',(cid,eid,aid)).fetchone();old=int(existing['show_popup']) if existing else 1;new=0 if old else 1;c.execute('INSERT INTO setup_element_popup_items(company_id,element_id,addon_id,show_popup) VALUES (?,?,?,?) ON CONFLICT(company_id,element_id,addon_id) DO UPDATE SET show_popup=excluded.show_popup',(cid,eid,aid,new))
        audit(database,context,cid,'ELEMENT_POPUP_CHANGED','element',eid,{'addon_id':aid,'show_popup':old},{'addon_id':aid,'show_popup':new});return RedirectResponse(f'/setup/elements?edit={eid}',303)
    @app.middleware('http')
    async def element_popup_html(request,call_next):
        response=await call_next(request)
        if request.url.path!='/setup/elements' or request.method!='GET' or response.status_code!=200 or 'text/html' not in response.headers.get('content-type',''):return response
        body=b''
        async for chunk in response.body_iterator:body+=chunk if isinstance(chunk,bytes) else str(chunk).encode()
        text=body.decode();context=database.session_context(request.cookies.get(COOKIE_NAME));raw=request.query_params.get('edit','')
        if context and raw.isdigit():
            cid=context['acting_company_id'] if context['role']=='supervisor' else context['company_id']
            if cid and rows(database,'SELECT id FROM setup_elements WHERE company_id=? AND id=?',(int(cid),int(raw))):
                eid=int(raw);settings={int(r['addon_id']):int(r['show_popup']) for r in rows(database,'SELECT addon_id,show_popup FROM setup_element_popup_items WHERE company_id=? AND element_id=?',(int(cid),eid))};controls='<div class="card element-popup-settings"><h2>Calendar popup information</h2><table><tbody>'
                for addon in rows(database,'SELECT id,name FROM setup_addons WHERE company_id=? AND active=1 ORDER BY lower(name)',(int(cid),)):
                    aid=int(addon['id']);controls+=f'<tr><td>{esc(addon["name"])}</td><td><form method="post" action="/setup/elements/popup"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="element_id" value="{eid}"><input type="hidden" name="addon_id" value="{aid}"><button>{"✓ Yes" if settings.get(aid,1) else "No"}</button></form></td></tr>'
                controls+='</tbody></table></div>';text=_remove_element_list(text);text=text.replace('</main>',controls+'</main>',1)
        headers={k:v for k,v in response.headers.items() if k.lower() not in {'content-length','content-type'}};return Response(content=text,status_code=response.status_code,headers=headers,media_type='text/html')
