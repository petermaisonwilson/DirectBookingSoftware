from __future__ import annotations

from datetime import date

from fastapi.responses import Response

from .app import COOKIE_NAME, esc
from .setup015_core import one, rows
from .webv1_booking_requirements import _saved_requirements
from .webv1_booking_requirements_core import element_reasons


def install_booking_requirements_ui(app) -> None:
    database=app.state.database
    @app.middleware('http')
    async def booking_requirements_ui(request,call_next):
        response=await call_next(request)
        if response.status_code>=400 or 'text/html' not in response.headers.get('content-type',''):return response
        path=request.url.path
        if path not in {'/setup/person-types','/setup/addons','/availability/calendar-v2'}:return response
        body=b''
        async for chunk in response.body_iterator:body+=chunk if isinstance(chunk,bytes) else str(chunk).encode('utf-8')
        text=body.decode('utf-8');headers={k:v for k,v in response.headers.items() if k.lower() not in {'content-length','content-type'}};context=database.session_context(request.cookies.get(COOKIE_NAME))
        if not context:return Response(content=text,status_code=response.status_code,headers=headers,media_type='text/html')
        cid=context['acting_company_id'] if context['role']=='supervisor' else context['company_id']
        if not cid:return Response(content=text,status_code=response.status_code,headers=headers,media_type='text/html')
        cid=int(cid);token=request.cookies.get(COOKIE_NAME,'')
        if path=='/setup/person-types':
            controls='<div class="card"><h2>Age question</h2><p class="muted">Privacy-by-design: ask for age only where it is genuinely needed. Date of birth is not collected.</p><table><thead><tr><th>Person Type</th><th>Ask for age at arrival</th></tr></thead><tbody>'
            for p in rows(database,'SELECT id,name,ask_age FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name',(cid,)):controls+=f'<tr><td>{esc(p["name"])}</td><td><form method="post" action="/setup/person-types/age-toggle"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="person_type_id" value="{int(p["id"])}"><button class="secondary">{"✓ Yes" if int(p["ask_age"] or 0) else "No"}</button></form></td></tr>'
            controls+='</tbody></table></div>';text=text.replace('<div class="card"><table>',controls+'<div class="card"><table>',1)
        elif path=='/setup/addons':
            controls='<div class="card"><h2>Ask before Availability</h2><p class="muted">Use this only for requirements that can make an Element unsuitable.</p><table><thead><tr><th>Add-on</th><th>Ask before Availability</th></tr></thead><tbody>'
            for a in rows(database,'SELECT id,name,ask_before_availability FROM setup_addons WHERE company_id=? AND active=1 ORDER BY name',(cid,)):controls+=f'<tr><td>{esc(a["name"])}</td><td><form method="post" action="/setup/addons/requirement-toggle"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="addon_id" value="{int(a["id"])}"><button class="secondary">{"✓ Yes" if int(a["ask_before_availability"] or 0) else "No"}</button></form></td></tr>'
            controls+='</tbody></table></div>';text=text.replace('<div class="card"><table>',controls+'<div class="card"><table>',1)
        else:
            people,addons,ready,_,_=_saved_requirements(database,cid,token)
            if not ready:return Response(content=text,status_code=response.status_code,headers=headers,media_type='text/html')
            summary=[]
            for pid,data in people.items():
                qty=int(data.get('quantity',0))
                if qty:
                    p=one(database,'SELECT name,ask_age FROM setup_person_types WHERE company_id=? AND id=?',(cid,pid));label=f'{qty} {str(p["name"]) if p else "person"}';ages=data.get('ages',[])
                    if ages:label+=' (age'+('s ' if len(ages)!=1 else ' ')+', '.join(str(x) for x in ages)+')'
                    summary.append(label)
            for aid,qty in addons.items():
                if int(qty):
                    a=one(database,'SELECT name FROM setup_addons WHERE company_id=? AND id=?',(cid,aid));summary.append(f'{str(a["name"]) if a else "Requirement"} {int(qty)}')
            summary_html=' · '.join(esc(x) for x in summary) or 'No special requirements';edit_hold=request.query_params.get('edit_hold','');change_href='/availability/start'+(f'?edit_hold={edit_hold}' if edit_hold.isdigit() else '')
            card=f'<div class="card requirement-summary"><strong>Your requirements:</strong> {summary_html} <a class="button secondary" style="margin-left:10px" href="{change_href}">Change</a></div>';text=text.replace('<h1>Availability Calendar</h1>','<h1>Availability Calendar</h1>'+card,1)
            raw_day=request.query_params.get('arrival') or request.query_params.get('start') or date.today().isoformat()
            try:year=date.fromisoformat(raw_day).year
            except ValueError:year=date.today().year
            unsuitable={}
            for element in rows(database,'SELECT * FROM setup_elements WHERE company_id=? AND active=1',(cid,)):
                reasons=element_reasons(database,cid,year,element,people,addons)
                if reasons:unsuitable[int(element['id'])]=reasons
            for eid,reasons in unsuitable.items():
                marker=f'<div class="cal-row element-row" data-element="{eid}"';replacement=marker.replace('class="cal-row element-row"','class="cal-row element-row party-unsuitable"');text=text.replace(marker,replacement,1);reason_text='Not suitable: '+' · '.join(reasons);row_start=text.find(replacement)
                if row_start>=0:
                    name_end=text.find('</div>',row_start)
                    if name_end>=0:text=text[:name_end]+f'<small class="party-reason">{esc(reason_text)}</small>'+text[name_end:]
            legend_marker='<span class="legend mini available-key">Available</span>';text=text.replace(legend_marker,legend_marker+'<span class="legend mini party-unsuitable-key">Not suitable for your party</span>',1)
            injection='''<style id="booking-requirements-style">.party-unsuitable-key{background:#eadcf4}#calendar-scroll .element-row.party-unsuitable .cal-cell.available{background:#eadcf4!important;pointer-events:none;cursor:not-allowed}#calendar-scroll .element-row.party-unsuitable .selection-action{display:none!important}.party-reason{display:block;color:#6d3f7c;font-size:10px;line-height:1.15;margin-top:2px}</style>''';text=text.replace('</body>',injection+'</body>',1)
        return Response(content=text,status_code=response.status_code,headers=headers,media_type='text/html')
