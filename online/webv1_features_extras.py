from __future__ import annotations

import sqlite3
from urllib.parse import quote_plus

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, form_data, layout
from .setup015_core import (
    ADDON_PRICING_METHODS,
    audit,
    context_for,
    require_csrf,
    rows,
    selected_year,
    valid_money,
    valid_whole,
    working_company,
    years,
)


def _ensure_column(connection, table: str, column: str, definition: str) -> None:
    names = {str(r['name']) for r in connection.execute(f'PRAGMA table_info({table})').fetchall()}
    if column not in names:
        connection.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')


def initialise_features_extras(database) -> None:
    """Extend the existing Add-on engine without breaking existing rules/pricing."""
    with database.connect() as c:
        _ensure_column(c, 'setup_addons', 'item_kind', "TEXT NOT NULL DEFAULT 'Extra'")
        _ensure_column(c, 'setup_addons', 'feature_group', "TEXT NOT NULL DEFAULT ''")
        _ensure_column(c, 'setup_addons', 'show_in_info', 'INTEGER NOT NULL DEFAULT 1')
        # Anything already used as a pre-Availability requirement is, by definition,
        # a Feature. Existing optional Add-ons remain Extras.
        c.execute("UPDATE setup_addons SET item_kind='Feature' WHERE ask_before_availability=1")
        c.execute("UPDATE setup_addons SET item_kind='Extra' WHERE item_kind NOT IN ('Feature','Extra') OR item_kind IS NULL")


def _setup_nav() -> str:
    links = [
        ('Setup home', '/setup'), ('Element Types', '/setup/element-types'), ('Elements', '/setup/elements'),
        ('Person Types', '/setup/person-types'), ('Features & Extras', '/setup/addons'),
        ('Feature / Extra Timings', '/setup/addons/when'), ('Years', '/setup/years'),
        ('Seasonal pricing', '/setup/pricing'), ('Occupancy', '/setup/occupancy'),
        ('Feature / Extra Rules', '/setup/addon-rules'), ('Price / Rules test', '/setup/price-test'),
    ]
    return '<div class="card" style="display:flex;gap:8px;flex-wrap:wrap">' + ''.join(
        f'<a class="button secondary" href="{href}">{label}</a>' for label, href in links
    ) + '</div>'


def _features_page(database, context, submitted=None, message: str = '', edit: int = 0) -> str:
    cid = int(working_company(context)); submitted = submitted or {}
    items = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? ORDER BY active DESC,item_kind,name COLLATE NOCASE', (cid,))
    current = next((r for r in items if int(r['id']) == int(edit or 0)), None)
    def value(key, default=''):
        if key in submitted: return submitted.get(key, default)
        return current[key] if current is not None and key in current.keys() else default
    name = str(value('name', ''))
    method = str(value('pricing_method', ADDON_PRICING_METHODS[0]))
    kind = str(value('item_kind', 'Feature' if value('ask_before_availability', 0) else 'Extra'))
    group = str(value('feature_group', ''))
    ask = bool(int(value('ask_before_availability', 1 if kind == 'Feature' else 0) or 0))
    show = bool(int(value('show_in_info', 1) or 0))
    methods = ''.join(f'<option {"selected" if m == method else ""}>{esc(m)}</option>' for m in ADDON_PRICING_METHODS)
    body = f'''<h1>Features & Extras</h1>{_setup_nav()}{f'<div class="error">{esc(message)}</div>' if message else ''}
    <div class="card"><p><strong>Features</strong> describe what a guest needs and can make an Element suitable or unsuitable. Features may be grouped, for example <em>Vehicle Type</em>, where the customer chooses one option only.</p>
    <p><strong>Extras</strong> are optional purchases offered after an Element has been chosen, for example Breakfast or BBQ hire.</p></div>
    <div class="card"><h2>{'Edit' if current else 'Add'} Feature / Extra</h2>
    <form method="post" action="/setup/addons"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><input type="hidden" name="id" value="{int(current['id']) if current else ''}">
    <div class="grid">
      <div><label>Name</label><input name="name" value="{esc(name)}" required></div>
      <div><label>Type</label><select id="item-kind" name="item_kind"><option {'selected' if kind == 'Feature' else ''}>Feature</option><option {'selected' if kind == 'Extra' else ''}>Extra</option></select></div>
      <div id="feature-group-box"><label>Feature group <span class="muted">(optional)</span></label><input name="feature_group" value="{esc(group)}" placeholder="e.g. Vehicle Type"><small class="muted">Items in the same group are shown as a one-choice list.</small></div>
      <div><label>Pricing method</label><select name="pricing_method">{methods}</select></div>
      <div id="ask-box"><label><input style="width:auto" type="checkbox" name="ask_before_availability" {'checked' if ask else ''}> Ask before Availability</label><small class="muted">Use where this Feature affects suitability.</small></div>
      <div><label><input style="width:auto" type="checkbox" name="show_in_info" {'checked' if show else ''}> Show in Availability information</label></div>
    </div><p><button>{'SAVE CHANGES' if current else 'ADD FEATURE / EXTRA'}</button>{' <a class="button secondary" href="/setup/addons">Cancel</a>' if current else ''}</p></form></div>
    <div class="card"><h2>Existing Features & Extras</h2><table><thead><tr><th>Name</th><th>Type</th><th>Group</th><th>Ask before Availability</th><th>Show in info</th><th>Pricing</th><th></th></tr></thead><tbody>'''
    for r in items:
        body += f'<tr><td>{esc(r["name"])}</td><td>{esc(r["item_kind"])}</td><td>{esc(r["feature_group"] or "—")}</td><td>{"✓" if int(r["ask_before_availability"] or 0) else "—"}</td><td>{"✓" if int(r["show_in_info"] or 0) else "—"}</td><td>{esc(r["pricing_method"])}</td><td><a href="/setup/addons?edit={int(r["id"])}">Edit</a></td></tr>'
    body += '''</tbody></table></div>
    <script>(()=>{const kind=document.getElementById('item-kind'), group=document.getElementById('feature-group-box'), ask=document.getElementById('ask-box');function sync(){const f=kind.value==='Feature';group.style.display=f?'block':'none';ask.style.display=f?'block':'none';if(!f){group.querySelector('input').value='';ask.querySelector('input').checked=false;}}kind.addEventListener('change',sync);sync();})();</script>'''
    return layout('Features & Extras', body, context)


def _year_select(available: list[int], selected: int | None, element_type: str, element_id: int) -> str:
    opts = ''.join(f'<option value="{y}" {"selected" if y == selected else ""}>{y}</option>' for y in available)
    return f'''<form id="year-form" method="get" action="/setup/addon-rules" class="card"><label>Pricing year</label><select name="year" onchange="this.form.submit()">{opts}</select><input type="hidden" name="element_type" value="{esc(element_type)}"><input type="hidden" name="element" value="{element_id or ''}"></form>'''


def _rules_page(database, context, year: int | None, element_type: str = '', element_id: int = 0, submitted=None, errors=None, message='') -> str:
    cid = int(working_company(context)); submitted = submitted or {}; errors = errors or set()
    available = years(database, cid); selected = selected_year(database, cid, year)
    types = [str(r['name']) for r in rows(database, 'SELECT name FROM setup_element_types WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (cid,))]
    typ = element_type if element_type in types else (types[0] if types else '')
    elements = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1 AND element_type=? ORDER BY name COLLATE NOCASE', (cid, typ)) if typ else []
    chosen = next((e for e in elements if int(e['id']) == int(element_id or 0)), elements[0] if elements else None)
    addons = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND active=1 ORDER BY item_kind DESC,feature_group,name COLLATE NOCASE', (cid,))
    tabs = ''.join(f'<a class="button {"" if t == typ else "secondary"}" data-dirty-nav href="/setup/addon-rules?year={selected or ""}&element_type={quote_plus(t)}">{esc(t)}</a>' for t in types)
    body = f'''<h1>Feature / Extra Rules</h1>{_setup_nav()}{f'<div class="error">{esc(message)}</div>' if message else ''}{_year_select(available, selected, typ, int(chosen['id']) if chosen else 0)}
    <div class="card"><h2>Element Type</h2><div style="display:flex;gap:8px;flex-wrap:wrap">{tabs or '<span class="muted">Create an Element Type first.</span>'}</div></div>'''
    if selected is None or not typ:
        return layout('Feature / Extra Rules', body, context)

    body += f'''<form id="rules-form" method="post" action="/setup/addon-rules"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><input type="hidden" name="year" value="{selected}"><input type="hidden" name="element_type" value="{esc(typ)}"><input type="hidden" name="element_id" value="{int(chosen['id']) if chosen else 0}">
    <div class="card"><div style="display:flex;justify-content:space-between;gap:12px;align-items:center"><div><h2 style="margin:0">{esc(typ)} defaults</h2><p class="muted" style="margin:4px 0 0">Set the normal rule once for this Element Type.</p></div><button type="submit">SAVE CHANGES</button></div>
    <table><thead><tr><th>Feature / Extra</th><th>Type</th><th>Allowed</th><th>Min</th><th>Max</th><th>Price €</th></tr></thead><tbody>'''
    for a in addons:
        aid = int(a['id']); base = None
        with database.connect() as c:
            base = c.execute('SELECT * FROM setup_type_addons WHERE company_id=? AND year=? AND element_type=? AND addon_id=?', (cid, selected, typ, aid)).fetchone()
        yes = bool(base and int(base['allowed']))
        vals = {'min': '' if not base or base['min_qty'] is None else str(base['min_qty']), 'max': '' if not base or base['max_qty'] is None else str(base['max_qty']), 'rate': '' if not base or base['rate'] is None else f'{float(base["rate"]):.2f}'}
        if submitted:
            yes = f'ty_{aid}' in submitted; vals = {k: submitted.get(f'ty{k}_{aid}', '') for k in ('min','max','rate')}
        body += f'<tr><td><strong>{esc(a["name"])}</strong>{f"<small style=\"display:block\">{esc(a[\"feature_group\"])}</small>" if a["feature_group"] else ""}</td><td>{esc(a["item_kind"])}</td><td><input style="width:auto" type="checkbox" name="ty_{aid}" {"checked" if yes else ""}></td><td><input name="tymin_{aid}" value="{esc(vals["min"])}"></td><td><input name="tymax_{aid}" value="{esc(vals["max"])}"></td><td><input name="tyrate_{aid}" value="{esc(vals["rate"])}"></td></tr>'
    body += '</tbody></table></div>'

    options = ''.join(f'<option value="{int(e["id"])}" {"selected" if chosen and int(e["id"]) == int(chosen["id"]) else ""}>{esc(e["name"])}</option>' for e in elements)
    body += f'''<div class="card"><div style="display:flex;justify-content:space-between;gap:12px;align-items:end;flex-wrap:wrap"><div><h2 style="margin:0">Individual Element override</h2><label style="margin-top:8px">Element</label><select id="element-override-select">{options}</select></div><button type="submit">SAVE CHANGES</button></div><p class="muted"><strong>Default</strong> inherits the {esc(typ)} rule. Choose Yes or No only when this Element differs.</p>
    <table><thead><tr><th>Feature / Extra</th><th>Rule</th><th>Min</th><th>Max</th><th>Price €</th></tr></thead><tbody>'''
    if chosen:
        for a in addons:
            aid = int(a['id'])
            with database.connect() as c:
                base = c.execute('SELECT * FROM setup_element_addons WHERE company_id=? AND year=? AND element_id=? AND addon_id=?', (cid, selected, int(chosen['id']), aid)).fetchone()
            state = 'I' if not base else str(base['state']); vals = {'min':'','max':'','rate':''}
            if base:
                vals = {'min': '' if base['min_qty'] is None else str(base['min_qty']), 'max': '' if base['max_qty'] is None else str(base['max_qty']), 'rate': '' if base['rate'] is None else f'{float(base["rate"]):.2f}'}
            if submitted:
                state = submitted.get(f'ov_{aid}', 'I'); vals = {k: submitted.get(f'ov{k}_{aid}', '') for k in ('min','max','rate')}
            radios = ' '.join(f'<label style="display:inline;font-weight:normal;margin-right:8px"><input style="width:auto" type="radio" name="ov_{aid}" value="{s}" {"checked" if state == s else ""}> {label}</label>' for s,label in (('I','Default'),('Y','Yes'),('N','No')))
            body += f'<tr><td><strong>{esc(a["name"])}</strong></td><td>{radios}</td><td><input name="ovmin_{aid}" value="{esc(vals["min"])}"></td><td><input name="ovmax_{aid}" value="{esc(vals["max"])}"></td><td><input name="ovrate_{aid}" value="{esc(vals["rate"])}"></td></tr>'
    body += '</tbody></table><p><button type="submit">SAVE CHANGES</button></p></div></form>'
    body += f'''<script>(()=>{{let dirty=false;const form=document.getElementById('rules-form');form.querySelectorAll('input,select').forEach(x=>x.addEventListener('change',()=>dirty=true));form.addEventListener('submit',()=>dirty=false);document.querySelectorAll('[data-dirty-nav]').forEach(a=>a.addEventListener('click',e=>{{if(dirty&&!confirm('You have unsaved changes. Leave without saving?'))e.preventDefault();}}));const sel=document.getElementById('element-override-select');if(sel)sel.addEventListener('change',()=>{{if(dirty&&!confirm('You have unsaved changes. Leave without saving?')){{sel.value={int(chosen['id']) if chosen else 0};return;}}window.location='/setup/addon-rules?year={selected}&element_type='+encodeURIComponent('{typ}')+'&element='+sel.value;}});}})();</script>'''
    return layout('Feature / Extra Rules', body, context)


def _remove_routes(app, path: str) -> None:
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, 'path', None) != path]


def register_features_extras_routes(app) -> None:
    database = app.state.database
    _remove_routes(app, '/setup/addons')
    _remove_routes(app, '/setup/addon-rules')

    @app.get('/setup/addons', response_class=HTMLResponse)
    def features_extras(request: Request, edit: int = 0):
        return _features_page(database, context_for(database, request), edit=edit)

    @app.post('/setup/addons')
    async def features_extras_save(request: Request):
        context = context_for(database, request); cid = int(working_company(context)); data = await form_data(request); require_csrf(context, data)
        name = str(data.get('name','')).strip(); kind = str(data.get('item_kind','Feature')).strip(); group = str(data.get('feature_group','')).strip(); method = str(data.get('pricing_method','')).strip(); raw_id = str(data.get('id',''))
        if not name or kind not in {'Feature','Extra'} or method not in ADDON_PRICING_METHODS:
            return HTMLResponse(_features_page(database, context, data, 'Complete the name, Type and Pricing method.', int(raw_id) if raw_id.isdigit() else 0), 400)
        ask = 1 if kind == 'Feature' and 'ask_before_availability' in data else 0
        show = 1 if 'show_in_info' in data else 0
        if kind == 'Extra': group = ''
        try:
            with database.connect() as c:
                if raw_id.isdigit():
                    old = c.execute('SELECT * FROM setup_addons WHERE company_id=? AND id=?', (cid, int(raw_id))).fetchone()
                    if old is None: raise HTTPException(status_code=404, detail='Feature / Extra not found')
                    entity_id = int(raw_id); before = dict(old)
                    c.execute('UPDATE setup_addons SET name=?,pricing_method=?,item_kind=?,feature_group=?,ask_before_availability=?,show_in_info=? WHERE company_id=? AND id=?', (name, method, kind, group, ask, show, cid, entity_id))
                else:
                    before = None
                    entity_id = c.execute('INSERT INTO setup_addons(company_id,name,pricing_method,item_kind,feature_group,ask_before_availability,show_in_info) VALUES (?,?,?,?,?,?,?)', (cid,name,method,kind,group,ask,show)).lastrowid
        except sqlite3.IntegrityError:
            return HTMLResponse(_features_page(database, context, data, 'That Feature / Extra name already exists.', int(raw_id) if raw_id.isdigit() else 0), 400)
        audit(database, context, cid, 'FEATURE_EXTRA_SAVED', 'addon', entity_id, before, {'name':name,'item_kind':kind,'feature_group':group,'ask_before_availability':ask,'show_in_info':show})
        return RedirectResponse('/setup/addons', 303)

    @app.get('/setup/addon-rules', response_class=HTMLResponse)
    def feature_rules(request: Request, year: str = '', element_type: str = '', element: int = 0):
        context = context_for(database, request)
        return _rules_page(database, context, selected_year(database, working_company(context), year), element_type, element)

    @app.post('/setup/addon-rules')
    async def feature_rules_save(request: Request):
        context = context_for(database, request); cid = int(working_company(context)); data = await form_data(request); require_csrf(context, data)
        try: year = int(data.get('year',''))
        except ValueError: return HTMLResponse(_rules_page(database, context, None, message='Choose a valid pricing year.'), 400)
        typ = str(data.get('element_type','')).strip(); eid = int(data.get('element_id','0') or 0)
        addons = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND active=1 ORDER BY name', (cid,)); errors=set(); type_values=[]; override_values=[]
        for a in addons:
            aid=int(a['id']); allowed=1 if f'ty_{aid}' in data else 0
            if allowed:
                try: mn=valid_whole(data.get(f'tymin_{aid}','')); mx=valid_whole(data.get(f'tymax_{aid}','')); rate=valid_money(data.get(f'tyrate_{aid}',''))
                except (TypeError,ValueError): errors.add(f'ty_{aid}'); continue
                if mx<mn: errors.add(f'ty_{aid}'); continue
            else: mn=mx=rate=None
            type_values.append((aid,allowed,mn,mx,rate))
            if eid:
                state=str(data.get(f'ov_{aid}','I'))
                if state=='Y':
                    try: omn=valid_whole(data.get(f'ovmin_{aid}','')); omx=valid_whole(data.get(f'ovmax_{aid}','')); orate=valid_money(data.get(f'ovrate_{aid}',''))
                    except (TypeError,ValueError): errors.add(f'ov_{aid}'); continue
                    if omx<omn: errors.add(f'ov_{aid}'); continue
                else: omn=omx=orate=None
                override_values.append((aid,state,omn,omx,orate))
        if errors:
            return HTMLResponse(_rules_page(database, context, year, typ, eid, data, errors, 'Every allowed Yes rule needs valid Min, Max and Price values; Max cannot be lower than Min.'), 400)
        with database.connect() as c:
            for aid,allowed,mn,mx,rate in type_values:
                c.execute('INSERT OR REPLACE INTO setup_type_addons VALUES (?,?,?,?,?,?,?,?)', (cid,year,typ,aid,allowed,mn,mx,rate))
            for aid,state,mn,mx,rate in override_values:
                if state=='I': c.execute('DELETE FROM setup_element_addons WHERE company_id=? AND year=? AND element_id=? AND addon_id=?', (cid,year,eid,aid))
                else: c.execute('INSERT OR REPLACE INTO setup_element_addons VALUES (?,?,?,?,?,?,?,?)', (cid,year,eid,aid,state,mn,mx,rate))
        audit(database, context, cid, 'FEATURE_EXTRA_RULES_SAVED', 'pricing_year', year, None, {'element_type':typ,'element_id':eid,'items':len(addons)})
        return RedirectResponse(f'/setup/addon-rules?year={year}&element_type={quote_plus(typ)}&element={eid}',303)
