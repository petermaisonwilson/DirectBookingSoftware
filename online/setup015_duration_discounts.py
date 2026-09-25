from __future__ import annotations
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from .app import esc, form_data, layout
from .database import iso_now
from .setup015_catalogue import setup_nav
from .setup015_core import audit, context_for, one, require_csrf, rows, working_company

TYPES=('Percentage','Fixed amount','Free nights')
SCOPES=('All elements','Element Type','Element')

def register_duration_discount_routes(app) -> None:
    database=app.state.database
    @app.get('/setup/duration-discounts',response_class=HTMLResponse)
    def page(request:Request):
        ctx=context_for(database,request); cid=int(working_company(ctx))
        rules=rows(database,'SELECT d.*,e.name AS element_name FROM setup_duration_discounts d LEFT JOIN setup_elements e ON e.id=d.element_id AND e.company_id=d.company_id WHERE d.company_id=? ORDER BY d.active DESC,d.min_nights,d.name',(cid,))
        elements=rows(database,'SELECT id,name,element_type FROM setup_elements WHERE company_id=? AND active=1 ORDER BY element_type,name',(cid,))
        types=sorted({str(e['element_type']) for e in elements},key=str.casefold)
        scope_opts=''.join(f'<option>{esc(x)}</option>' for x in SCOPES); type_opts='<option value="">—</option>'+''.join(f'<option>{esc(x)}</option>' for x in types); element_opts='<option value="">—</option>'+''.join(f'<option value="{int(e["id"])}">{esc(e["name"])}</option>' for e in elements)
        rows_html=''.join(f'<tr><td>{esc(r["name"])}</td><td>{int(r["min_nights"])}+</td><td>{esc(r["discount_type"])} {float(r["discount_value"]):g}</td><td>{esc(r["scope_type"])}</td><td>{"Active" if int(r["active"]) else "Inactive"}</td><td><form method="post" action="/setup/duration-discounts/toggle"><input type="hidden" name="csrf" value="{esc(ctx["csrf_token"])}"><input type="hidden" name="id" value="{int(r["id"])}"><button class="secondary">{"Deactivate" if int(r["active"]) else "Activate"}</button></form></td></tr>' for r in rules) or '<tr><td colspan="6">No duration discounts configured.</td></tr>'
        body=f'''<h1>Duration Discounts</h1>{setup_nav()}<div class="card"><p>Only the single best eligible duration discount is applied. Discounts never stack.</p><form method="post" action="/setup/duration-discounts"><input type="hidden" name="csrf" value="{esc(ctx["csrf_token"])}"><div class="grid"><div><label>Name</label><input name="name" required></div><div><label>Minimum nights</label><input name="min_nights" type="number" min="1" required></div><div><label>Discount type</label><select name="discount_type">{''.join(f'<option>{x}</option>' for x in TYPES)}</select></div><div><label>Value</label><input name="discount_value" type="number" min="0" step="0.01" required></div><div><label>Scope</label><select name="scope_type">{scope_opts}</select></div><div><label>Element Type (when scoped)</label><select name="element_type">{type_opts}</select></div><div><label>Element (when scoped)</label><select name="element_id">{element_opts}</select></div></div><p><button>Add Duration Discount</button></p></form></div><div class="card"><table><thead><tr><th>Name</th><th>Stay</th><th>Discount</th><th>Scope</th><th>Status</th><th></th></tr></thead><tbody>{rows_html}</tbody></table></div>'''
        return HTMLResponse(layout('Duration Discounts',body,ctx))
    @app.post('/setup/duration-discounts')
    async def save(request:Request):
        ctx=context_for(database,request); cid=int(working_company(ctx)); data=await form_data(request); require_csrf(ctx,data)
        name=str(data.get('name','')).strip(); scope=str(data.get('scope_type','')); dtype=str(data.get('discount_type',''))
        try: nights=int(data.get('min_nights','0')); value=float(str(data.get('discount_value','0')).replace(',','.'))
        except ValueError: return RedirectResponse('/setup/duration-discounts',303)
        if not name or nights<1 or value<0 or scope not in SCOPES or dtype not in TYPES: return RedirectResponse('/setup/duration-discounts',303)
        etype=str(data.get('element_type','')).strip(); raw=str(data.get('element_id','')).strip(); eid=int(raw) if raw.isdigit() else None
        if scope=='Element Type' and not etype: return RedirectResponse('/setup/duration-discounts',303)
        if scope=='Element' and (eid is None or one(database,'SELECT id FROM setup_elements WHERE company_id=? AND id=?',(cid,eid)) is None): return RedirectResponse('/setup/duration-discounts',303)
        with database.connect() as c:
            rid=int(c.execute('INSERT INTO setup_duration_discounts(company_id,name,min_nights,discount_type,discount_value,scope_type,element_type,element_id,active,created_at) VALUES (?,?,?,?,?,?,?,?,1,?)',(cid,name,nights,dtype,value,scope,etype if scope=='Element Type' else '',eid if scope=='Element' else None,iso_now())).lastrowid)
        audit(database,ctx,cid,'DURATION_DISCOUNT_CREATED','duration_discount',rid,after={'name':name,'min_nights':nights,'discount_type':dtype,'discount_value':value,'scope_type':scope,'element_type':etype,'element_id':eid})
        return RedirectResponse('/setup/duration-discounts',303)
    @app.post('/setup/duration-discounts/toggle')
    async def toggle(request:Request):
        ctx=context_for(database,request); cid=int(working_company(ctx)); data=await form_data(request); require_csrf(ctx,data)
        raw=str(data.get('id','')); rid=int(raw) if raw.isdigit() else 0; rule=one(database,'SELECT * FROM setup_duration_discounts WHERE company_id=? AND id=?',(cid,rid))
        if rule:
            active=0 if int(rule['active']) else 1
            with database.connect() as c: c.execute('UPDATE setup_duration_discounts SET active=? WHERE company_id=? AND id=?',(active,cid,rid))
            audit(database,ctx,cid,'DURATION_DISCOUNT_STATUS_CHANGED','duration_discount',rid,before={'active':int(rule['active'])},after={'active':active})
        return RedirectResponse('/setup/duration-discounts',303)
