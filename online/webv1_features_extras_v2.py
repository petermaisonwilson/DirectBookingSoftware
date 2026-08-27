from __future__ import annotations

import sqlite3
from urllib.parse import quote_plus

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, form_data, layout
from .setup015_core import ADDON_PRICING_METHODS, audit, context_for, require_csrf, rows, selected_year, valid_money, valid_whole, working_company, years


def _ensure_column(c, table: str, column: str, definition: str) -> None:
    names = {str(r['name']) for r in c.execute(f'PRAGMA table_info({table})').fetchall()}
    if column not in names:
        c.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')


def initialise_features_extras(database) -> None:
    with database.connect() as c:
        _ensure_column(c, 'setup_addons', 'item_kind', "TEXT NOT NULL DEFAULT 'Extra'")
        _ensure_column(c, 'setup_addons', 'feature_group', "TEXT NOT NULL DEFAULT ''")
        c.execute("UPDATE setup_addons SET item_kind='Feature' WHERE ask_before_availability=1")
        c.execute("UPDATE setup_addons SET item_kind='Extra' WHERE item_kind IS NULL OR item_kind NOT IN ('Feature','Extra')")


def _nav() -> str:
    links = [
        ('Setup home','/setup'),('Element Types','/setup/element-types'),('Elements','/setup/elements'),
        ('Person Types','/setup/person-types'),('Features & Extras','/setup/addons'),
        ('Feature / Extra Timings','/setup/addons/when'),('Years','/setup/years'),
        ('Seasonal pricing','/setup/pricing'),('Occupancy','/setup/occupancy'),
        ('Feature / Extra Rules','/setup/addon-rules'),('Price / Rules test','/setup/price-test'),
    ]
    return '<div class="card" style="display:flex;gap:8px;flex-wrap:wrap">' + ''.join(
        f'<a class="button secondary" href="{h}">{esc(t)}</a>' for t,h in links
    ) + '</div>'


def _features_page(database, context, submitted=None, message='', edit=0) -> str:
    cid = int(working_company(context)); submitted = submitted or {}
    items = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? ORDER BY active DESC,item_kind,name COLLATE NOCASE', (cid,))
    current = next((r for r in items if int(r['id']) == int(edit or 0)), None)
    def pick(name, default=''):
        if name in submitted:
            return submitted.get(name, default)
        if current is not None and name in current.keys():
            return current[name]
        return default
    name = str(pick('name',''))
    kind = str(pick('item_kind','Feature' if int(pick('ask_before_availability',0) or 0) else 'Extra'))
    group = str(pick('feature_group',''))
    method = str(pick('pricing_method',ADDON_PRICING_METHODS[0]))
    ask = bool(int(pick('ask_before_availability',1 if kind == 'Feature' else 0) or 0))
    method_options = ''.join(f'<option {"selected" if method == m else ""}>{esc(m)}</option>' for m in ADDON_PRICING_METHODS)
    error = f'<div class="error">{esc(message)}</div>' if message else ''
    body = f'''<h1>Features & Extras</h1>{_nav()}{error}
    <div class="card"><p><strong>Features</strong> describe what a guest needs and can make an Element suitable or unsuitable. Put related Features in the same group, for example <strong>Vehicle Type</strong>. A group is presented to the guest as a single-choice list.</p><p><strong>Extras</strong> are optional purchases offered after an Element has been selected, for example Breakfast or BBQ hire.</p></div>
    <div class="card"><h2>{'Edit' if current else 'Add'} Feature / Extra</h2><form method="post" action="/setup/addons">
    <input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><input type="hidden" name="id" value="{int(current['id']) if current else ''}">
    <div class="grid"><div><label>Name</label><input name="name" value="{esc(name)}"></div>
    <div><label>Type</label><select id="feature-kind" name="item_kind"><option {'selected' if kind == 'Feature' else ''}>Feature</option><option {'selected' if kind == 'Extra' else ''}>Extra</option></select></div>
    <div id="feature-group"><label>Feature group <span class="muted">(optional)</span></label><input name="feature_group" value="{esc(group)}" placeholder="e.g. Vehicle Type"><small class="muted">Same group = one choice only.</small></div>
    <div><label>Pricing method</label><select name="pricing_method">{method_options}</select></div>
    <div id="feature-ask"><label><input style="width:auto" type="checkbox" name="ask_before_availability" {'checked' if ask else ''}> Ask before Availability</label><small class="muted">Tick when this Feature should be part of suitability checking.</small></div></div>
    <p><button>{'SAVE CHANGES' if current else 'ADD FEATURE / EXTRA'}</button>{' <a class="button secondary" href="/setup/addons">Cancel</a>' if current else ''}</p></form></div>
    <div class="card"><h2>Existing Features & Extras</h2><table><thead><tr><th>Name</th><th>Type</th><th>Group</th><th>Ask before Availability</th><th>Pricing</th><th></th></tr></thead><tbody>'''
    for r in items:
        group_text = str(r['feature_group'] or '—')
        body += f'<tr><td>{esc(r["name"])}</td><td>{esc(r["item_kind"])}</td><td>{esc(group_text)}</td><td>{"✓" if int(r["ask_before_availability"] or 0) else "—"}</td><td>{esc(r["pricing_method"])}</td><td><a href="/setup/addons?edit={int(r["id"])}">Edit</a></td></tr>'
    body += '''</tbody></table></div><script>(()=>{const k=document.getElementById('feature-kind'),g=document.getElementById('feature-group'),a=document.getElementById('feature-ask');function s(){const f=k.value==='Feature';g.style.display=f?'block':'none';a.style.display=f?'block':'none';if(!f){g.querySelector('input').value='';a.querySelector('input').checked=false;}}k.addEventListener('change',s);s();})();</script>'''
    return layout('Features & Extras', body, context)


def _rules_page(database, context, year=None, element_type='', element_id=0, submitted=None, message='') -> str:
    cid = int(working_company(context)); submitted = submitted or {}
    available_years = years(database, cid); selected = selected_year(database, cid, year)
    types = [str(r['name']) for r in rows(database, 'SELECT name FROM setup_element_types WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (cid,))]
    typ = element_type if element_type in types else (types[0] if types else '')
    elements = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1 AND element_type=? ORDER BY name COLLATE NOCASE', (cid,typ)) if typ else []
    chosen = next((e for e in elements if int(e['id']) == int(element_id or 0)), elements[0] if elements else None)
    addons = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND active=1 ORDER BY item_kind DESC,feature_group,name COLLATE NOCASE', (cid,))
    year_options = ''.join(f'<option value="{y}" {"selected" if y == selected else ""}>{y}</option>' for y in available_years)
    tabs = ''.join(f'<a data-dirty-nav class="button {"" if t == typ else "secondary"}" href="/setup/addon-rules?year={selected or ""}&element_type={quote_plus(t)}">{esc(t)}</a>' for t in types)
    error = f'<div class="error">{esc(message)}</div>' if message else ''
    body = f'''<h1>Feature / Extra Rules</h1>{_nav()}{error}<div class="card"><form method="get" action="/setup/addon-rules"><label>Pricing year</label><select name="year" onchange="this.form.submit()">{year_options}</select><input type="hidden" name="element_type" value="{esc(typ)}"></form></div>
    <div class="card"><h2>Element Type</h2><div style="display:flex;gap:8px;flex-wrap:wrap">{tabs or '<span class="muted">Create an Element Type first.</span>'}</div></div>'''
    if selected is None or not typ:
        return layout('Feature / Extra Rules', body, context)
    chosen_id = int(chosen['id']) if chosen else 0
    body += f'''<form id="rules-form" method="post" action="/setup/addon-rules"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><input type="hidden" name="year" value="{selected}"><input type="hidden" name="element_type" value="{esc(typ)}"><input type="hidden" name="element_id" value="{chosen_id}">
    <div class="card"><div style="display:flex;justify-content:space-between;align-items:center;gap:12px"><div><h2 style="margin:0">{esc(typ)} defaults</h2><p class="muted" style="margin:4px 0 0">Set the normal rule once for the whole Element Type.</p></div><button>SAVE CHANGES</button></div>
    <table><thead><tr><th>Feature / Extra</th><th>Type</th><th>Allowed</th><th>Min</th><th>Max</th><th>Price €</th></tr></thead><tbody>'''
    with database.connect() as c:
        type_map = {int(r['addon_id']):r for r in c.execute('SELECT * FROM setup_type_addons WHERE company_id=? AND year=? AND element_type=?',(cid,selected,typ)).fetchall()}
    for a in addons:
        aid = int(a['id']); base = type_map.get(aid); yes = bool(base and int(base['allowed']))
        mn = '' if not base or base['min_qty'] is None else str(base['min_qty']); mx = '' if not base or base['max_qty'] is None else str(base['max_qty']); rate = '' if not base or base['rate'] is None else f'{float(base["rate"]):.2f}'
        if submitted:
            yes = f'ty_{aid}' in submitted; mn=submitted.get(f'tymin_{aid}',''); mx=submitted.get(f'tymax_{aid}',''); rate=submitted.get(f'tyrate_{aid}','')
        group_note = f'<small style="display:block;color:#66717f">{esc(a["feature_group"])}</small>' if a['feature_group'] else ''
        body += f'<tr><td><strong>{esc(a["name"])}</strong>{group_note}</td><td>{esc(a["item_kind"])}</td><td><input style="width:auto" type="checkbox" name="ty_{aid}" {"checked" if yes else ""}></td><td><input name="tymin_{aid}" value="{esc(mn)}"></td><td><input name="tymax_{aid}" value="{esc(mx)}"></td><td><input name="tyrate_{aid}" value="{esc(rate)}"></td></tr>'
    body += '</tbody></table></div>'
    option_html = ''.join(f'<option value="{int(e["id"])}" {"selected" if chosen and int(e["id"]) == chosen_id else ""}>{esc(e["name"])}</option>' for e in elements)
    body += f'''<div class="card"><div style="display:flex;justify-content:space-between;align-items:end;gap:12px;flex-wrap:wrap"><div><h2 style="margin:0">Individual Element override</h2><label style="margin-top:8px">Element</label><select id="element-select">{option_html}</select></div><button>SAVE CHANGES</button></div><p class="muted"><strong>Default</strong> inherits the {esc(typ)} rule. Override only the Elements that differ.</p><table><thead><tr><th>Feature / Extra</th><th>Rule</th><th>Min</th><th>Max</th><th>Price €</th></tr></thead><tbody>'''
    override_map = {}
    if chosen:
        with database.connect() as c:
            override_map = {int(r['addon_id']):r for r in c.execute('SELECT * FROM setup_element_addons WHERE company_id=? AND year=? AND element_id=?',(cid,selected,chosen_id)).fetchall()}
    for a in addons:
        aid=int(a['id']); base=override_map.get(aid); state='I' if not base else str(base['state']); mn=mx=rate=''
        if base:
            mn='' if base['min_qty'] is None else str(base['min_qty']); mx='' if base['max_qty'] is None else str(base['max_qty']); rate='' if base['rate'] is None else f'{float(base["rate"]):.2f}'
        if submitted:
            state=submitted.get(f'ov_{aid}','I'); mn=submitted.get(f'ovmin_{aid}',''); mx=submitted.get(f'ovmax_{aid}',''); rate=submitted.get(f'ovrate_{aid}','')
        radios=' '.join(f'<label style="display:inline;font-weight:normal;margin-right:8px"><input style="width:auto" type="radio" name="ov_{aid}" value="{code}" {"checked" if state==code else ""}> {label}</label>' for code,label in (('I','Default'),('Y','Yes'),('N','No')))
        body += f'<tr><td><strong>{esc(a["name"])}</strong></td><td>{radios}</td><td><input name="ovmin_{aid}" value="{esc(mn)}"></td><td><input name="ovmax_{aid}" value="{esc(mx)}"></td><td><input name="ovrate_{aid}" value="{esc(rate)}"></td></tr>'
    body += f'''</tbody></table><p><button>SAVE CHANGES</button></p></div></form><script>(()=>{{let dirty=false;const form=document.getElementById('rules-form');form.querySelectorAll('input,select').forEach(x=>x.addEventListener('change',()=>dirty=true));form.addEventListener('submit',()=>dirty=false);document.querySelectorAll('[data-dirty-nav]').forEach(a=>a.addEventListener('click',e=>{{if(dirty&&!confirm('You have unsaved changes. Leave without saving?'))e.preventDefault();}}));const sel=document.getElementById('element-select');if(sel)sel.addEventListener('change',()=>{{if(dirty&&!confirm('You have unsaved changes. Leave without saving?')){{sel.value='{chosen_id}';return;}}window.location='/setup/addon-rules?year={selected}&element_type='+encodeURIComponent('{typ}')+'&element='+sel.value;}});}})();</script>'''
    return layout('Feature / Extra Rules', body, context)


def _remove_route(app, path: str) -> None:
    app.router.routes[:] = [r for r in app.router.routes if getattr(r,'path',None) != path]


def register_features_extras_routes(app) -> None:
    database = app.state.database
    _remove_route(app,'/setup/addons'); _remove_route(app,'/setup/addon-rules')

    @app.get('/setup/addons', response_class=HTMLResponse)
    def page(request: Request, edit: int=0):
        return _features_page(database, context_for(database,request), edit=edit)

    @app.post('/setup/addons')
    async def save(request: Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        name=str(data.get('name','')).strip(); kind=str(data.get('item_kind','Feature')); group=str(data.get('feature_group','')).strip(); method=str(data.get('pricing_method','')); raw_id=str(data.get('id',''))
        if not name or kind not in {'Feature','Extra'} or method not in ADDON_PRICING_METHODS:
            return HTMLResponse(_features_page(database,context,data,'Complete the name, Type and Pricing method.',int(raw_id) if raw_id.isdigit() else 0),400)
        ask=1 if kind=='Feature' and 'ask_before_availability' in data else 0
        if kind=='Extra': group=''
        try:
            with database.connect() as c:
                if raw_id.isdigit():
                    old=c.execute('SELECT * FROM setup_addons WHERE company_id=? AND id=?',(cid,int(raw_id))).fetchone()
                    if old is None: raise HTTPException(status_code=404,detail='Feature / Extra not found')
                    entity_id=int(raw_id); before=dict(old)
                    c.execute('UPDATE setup_addons SET name=?,pricing_method=?,item_kind=?,feature_group=?,ask_before_availability=? WHERE company_id=? AND id=?',(name,method,kind,group,ask,cid,entity_id))
                else:
                    before=None; entity_id=c.execute('INSERT INTO setup_addons(company_id,name,pricing_method,item_kind,feature_group,ask_before_availability) VALUES (?,?,?,?,?,?)',(cid,name,method,kind,group,ask)).lastrowid
        except sqlite3.IntegrityError:
            return HTMLResponse(_features_page(database,context,data,'That Feature / Extra name already exists.',int(raw_id) if raw_id.isdigit() else 0),400)
        audit(database,context,cid,'FEATURE_EXTRA_SAVED','addon',entity_id,before,{'name':name,'item_kind':kind,'feature_group':group,'ask_before_availability':ask})
        return RedirectResponse('/setup/addons',303)

    @app.get('/setup/addon-rules', response_class=HTMLResponse)
    def rules_page(request: Request, year: str='', element_type: str='', element: int=0):
        context=context_for(database,request); return _rules_page(database,context,selected_year(database,working_company(context),year),element_type,element)

    @app.post('/setup/addon-rules')
    async def rules_save(request: Request):
        context=context_for(database,request); cid=int(working_company(context)); data=await form_data(request); require_csrf(context,data)
        try: year=int(data.get('year',''))
        except ValueError: return HTMLResponse(_rules_page(database,context,message='Choose a valid pricing year.'),400)
        typ=str(data.get('element_type','')).strip(); eid=int(data.get('element_id','0') or 0); addons=rows(database,'SELECT * FROM setup_addons WHERE company_id=? AND active=1 ORDER BY name',(cid,)); errors=[]; type_values=[]; override_values=[]
        for a in addons:
            aid=int(a['id']); allowed=1 if f'ty_{aid}' in data else 0
            if allowed:
                try: mn=valid_whole(data.get(f'tymin_{aid}','')); mx=valid_whole(data.get(f'tymax_{aid}','')); rate=valid_money(data.get(f'tyrate_{aid}',''))
                except (TypeError,ValueError): errors.append(a['name']); continue
                if mx<mn: errors.append(a['name']); continue
            else: mn=mx=rate=None
            type_values.append((aid,allowed,mn,mx,rate))
            if eid:
                state=str(data.get(f'ov_{aid}','I'))
                if state=='Y':
                    try: omn=valid_whole(data.get(f'ovmin_{aid}','')); omx=valid_whole(data.get(f'ovmax_{aid}','')); orate=valid_money(data.get(f'ovrate_{aid}',''))
                    except (TypeError,ValueError): errors.append(a['name']); continue
                    if omx<omn: errors.append(a['name']); continue
                else: omn=omx=orate=None
                override_values.append((aid,state,omn,omx,orate))
        if errors:
            return HTMLResponse(_rules_page(database,context,year,typ,eid,data,'Complete valid Min, Max and Price values for every Yes rule. Max cannot be lower than Min.'),400)
        with database.connect() as c:
            for aid,allowed,mn,mx,rate in type_values:
                c.execute('INSERT OR REPLACE INTO setup_type_addons VALUES (?,?,?,?,?,?,?,?)',(cid,year,typ,aid,allowed,mn,mx,rate))
            for aid,state,mn,mx,rate in override_values:
                if state=='I': c.execute('DELETE FROM setup_element_addons WHERE company_id=? AND year=? AND element_id=? AND addon_id=?',(cid,year,eid,aid))
                else: c.execute('INSERT OR REPLACE INTO setup_element_addons VALUES (?,?,?,?,?,?,?,?)',(cid,year,eid,aid,state,mn,mx,rate))
        audit(database,context,cid,'FEATURE_EXTRA_RULES_SAVED','pricing_year',year,None,{'element_type':typ,'element_id':eid,'items':len(addons)})
        return RedirectResponse(f'/setup/addon-rules?year={year}&element_type={quote_plus(typ)}&element={eid}',303)
