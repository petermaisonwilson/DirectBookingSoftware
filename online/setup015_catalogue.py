from __future__ import annotations

import sqlite3
from datetime import date

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, form_data, layout
from .setup015_core import (
    ADDON_PRICING_METHODS,
    ELEMENT_PRICING_METHODS,
    audit,
    context_for,
    copy_previous_year,
    require_csrf,
    rows,
    valid_money,
    working_company,
    years,
)
from .webv1_ordering import person_type_rows, setup_sortable_table_bits, sortable_menu_html


def setup_nav() -> str:
    links = [
        ("Setup home", "/setup"),
        ("Element Types", "/setup/element-types"),
        ("Elements", "/setup/elements"),
        ("Person Types", "/setup/person-types"),
        ("Add-ons", "/setup/addons"),
        ("Add-on Timings", "/setup/addons/when"),
        ("Years", "/setup/years"),
        ("Seasonal pricing", "/setup/pricing"),
        ("Occupancy", "/setup/occupancy"),
        ("Add-on rules", "/setup/addon-rules"),
        ("Price / Rules test", "/setup/price-test"),
    ]
    script = '''<script>
    document.addEventListener("DOMContentLoaded", function () {
      document.querySelectorAll("main form").forEach(function (form) { form.noValidate = true; });
    });
    </script>'''
    return (
        '<div class="card" style="display:flex;gap:8px;flex-wrap:wrap">'
        + ''.join(f'<a class="button secondary" href="{href}">{label}</a>' for label, href in links)
        + '</div>'
        + script
    )


def error_box(message: str) -> str:
    if not message:
        return ''
    return '<div class="error"><strong>Please correct the highlighted field(s).</strong><br>' + esc(message) + '</div>'


def field_style(error: bool, extra: str = '') -> str:
    style = extra
    if error:
        style += ';border:2px solid #b42318;background:#fff1f0'
    return style.lstrip(';')


def _element_types_page(database, context, submitted=None, errors=None, message='', edit=0):
    cid = working_company(context)
    submitted = submitted or {}
    errors = errors or set()
    type_rows = rows(database, 'SELECT * FROM setup_element_types WHERE company_id=? ORDER BY active DESC,name', (cid,))
    current = next((r for r in type_rows if int(r['id']) == int(edit or 0)), None)
    name = submitted.get('name', current['name'] if current else '')
    body = f'<h1>Element Types</h1>{setup_nav()}{error_box(message)}'
    body += f'''<div class="card"><h2>{"Rename" if current else "Add"} Element Type</h2>
    <p>Define each grouping once here. Elements then choose from this controlled list, avoiding duplicates such as <strong>Camping</strong> and <strong>Campings</strong>.</p>
    <form method="post" action="/setup/element-types"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="id" value="{esc(current["id"] if current else "")}">
    <label>Name</label><input style="{field_style("name" in errors)}" name="name" value="{esc(name)}"><p><button>{"Save name" if current else "Add Element Type"}</button></p></form></div>'''
    body += '<div class="card"><table><thead><tr><th>Name</th><th>Status</th><th>Used by</th><th></th></tr></thead><tbody>'
    for t in type_rows:
        used = rows(database, 'SELECT id FROM setup_elements WHERE company_id=? AND element_type=?', (cid, t['name']))
        toggle = 'Reactivate' if not t['active'] else 'Deactivate'
        body += f'<tr><td>{esc(t["name"])}</td><td>{"Active" if t["active"] else "Inactive"}</td><td>{len(used)} Element(s)</td><td><a href="/setup/element-types?edit={t["id"]}">Rename</a> &nbsp; <form method="post" action="/setup/element-types/toggle" style="display:inline"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="id" value="{t["id"]}"><button class="secondary">{toggle}</button></form></td></tr>'
    body += '</tbody></table></div>'
    return layout('Element Types', body, context)


def _elements_page(database, context, submitted=None, errors=None, message='', edit=0, saved=0):
    cid = working_company(context)
    submitted = submitted or {}
    errors = errors or set()
    element_rows = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? ORDER BY element_type,name', (cid,))
    current = next((r for r in element_rows if int(r['id']) == int(edit or 0)), None)
    active_types = list(rows(database, 'SELECT * FROM setup_element_types WHERE company_id=? AND active=1 ORDER BY name', (cid,)))
    selected_type = submitted.get('element_type', current['element_type'] if current else '')
    if current and selected_type and not any(str(t['name']) == selected_type for t in active_types):
        active_types.append({'name': selected_type})
    name = submitted.get('name', current['name'] if current else '')
    method = submitted.get('pricing_method', current['pricing_method'] if current else ELEMENT_PRICING_METHODS[0])
    base_price = submitted.get('base_price', f'{float(current["base_price"]):.2f}' if current else '0.00')
    type_options = '<option value="">-- choose Element Type --</option>' + ''.join(f'<option value="{esc(t["name"])}" {"selected" if t["name"] == selected_type else ""}>{esc(t["name"])}</option>' for t in active_types)
    method_options = ''.join(f'<option {"selected" if item == method else ""}>{item}</option>' for item in ELEMENT_PRICING_METHODS)
    body = f'<h1>Elements</h1>{setup_nav()}{error_box(message)}' + ('<div class="ok">Saved and audited.</div>' if saved else '')
    if not active_types:
        body += '<div class="error"><strong>Create an Element Type first.</strong><br>Elements can only be assigned to a Client-defined Element Type.</div>'
    body += f'''<div class="card"><h2>{"Edit" if current else "Add"} Element</h2><form method="post" action="/setup/elements"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="id" value="{esc(current["id"] if current else "")}"><div class="grid"><div><label>Name</label><input style="{field_style("name" in errors)}" name="name" value="{esc(name)}"></div><div><label>Element Type</label><select style="{field_style("element_type" in errors)}" name="element_type">{type_options}</select></div><div><label>Pricing method</label><select style="{field_style("pricing_method" in errors)}" name="pricing_method">{method_options}</select></div><div><label>Base price</label><input style="{field_style("base_price" in errors)}" name="base_price" value="{esc(base_price)}"></div></div><p><button {"disabled" if not active_types else ""}>Save Element</button></p></form></div>'''
    body += '<div class="card"><table><thead><tr><th>Name</th><th>Element Type</th><th>Pricing</th><th>Base price</th><th></th></tr></thead><tbody>'
    body += ''.join(f'<tr><td>{esc(r["name"])}</td><td>{esc(r["element_type"])}</td><td>{esc(r["pricing_method"])}</td><td>€{float(r["base_price"]):.2f}</td><td><a href="/setup/elements?edit={r["id"]}">Edit</a></td></tr>' for r in element_rows) or '<tr><td colspan="5">No Elements yet.</td></tr>'
    body += '</tbody></table></div>'
    return layout('Elements', body, context)


def _catalog_page(database, context, table, title, action, methods=(), submitted=None, errors=None, message=''):
    cid = working_company(context)
    submitted = submitted or {}
    errors = errors or set()
    is_person = table == 'setup_person_types'
    catalog_rows = person_type_rows(database, cid) if is_person else rows(database, f'SELECT * FROM {table} WHERE company_id=? ORDER BY name', (cid,))
    method = submitted.get('pricing_method', methods[0] if methods else '')
    method_html = ''
    if methods:
        method_html = '<div><label>Pricing method</label><select style="' + field_style('pricing_method' in errors) + '" name="pricing_method">' + ''.join(f'<option {"selected" if item == method else ""}>{item}</option>' for item in methods) + '</select></div>'
    short_html = f'<div><label>Short name</label><input name="short_name" value="{esc(submitted.get("short_name", ""))}" maxlength="8"></div>' if is_person else ''
    order_heading, order_script = setup_sortable_table_bits(context, 'person_types') if is_person else ('', '')
    body = f'<h1>{title}</h1>{setup_nav()}{error_box(message)}<div class="card"><form method="post" action="{action}"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><div class="grid"><div><label>Name</label><input style="{field_style("name" in errors)}" name="name" value="{esc(submitted.get("name", ""))}"></div>{short_html}{method_html}</div><p><button>Add {title[:-1] if title.endswith("s") else title}</button></p></form></div><div class="card">'
    if is_person:
        body += '<p class="muted">Drag Person Types into the order you want them shown throughout Setup and Booking Requirements. The order is shared by the Client and a Supervisor working in Support Mode.</p>'
    body += f'<table><thead><tr>{order_heading}<th>Name</th>'
    if is_person: body += '<th>Short name</th>'
    if methods: body += '<th>Pricing method</th>'
    tbody = ' id="setup-sort-person_types"' if is_person else ''
    body += f'</tr></thead><tbody{tbody}>'
    for r in catalog_rows:
        drag = f' draggable="true" data-item-id="{int(r["id"])}"' if is_person else ''
        body += f'<tr{drag}>'
        if is_person: body += '<td><span class="row-sort-handle" title="Drag to reorder">☰ Drag</span></td>'
        body += f'<td>{esc(r["name"])}</td>'
        if is_person: body += f'<td>{esc(r["short_name"])}</td>'
        if methods: body += f'<td>{esc(r["pricing_method"])}</td>'
        body += '</tr>'
    body += '</tbody></table></div>' + order_script
    return layout(title, body, context)


def _years_page(database, context, submitted=None, errors=None, message=''):
    cid = working_company(context)
    submitted = submitted or {}
    errors = errors or set()
    available = years(database, cid)
    blank_year = submitted.get('new_year', str(date.today().year))
    copy_year = submitted.get('copy_year', str((max(available) + 1) if available else date.today().year + 1))
    body = f'<h1>Pricing years</h1>{setup_nav()}{error_box(message)}'
    body += f'<div class="card"><h2>Create blank year</h2><form method="post" action="/setup/years/new"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><label>Year</label><input style="{field_style("new_year" in errors)}" type="number" name="year" value="{esc(blank_year)}"><p><button>Create blank year</button></p></form></div>'
    body += f'<div class="card"><h2>Copy previous year</h2><form method="post" action="/setup/years/copy"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><label>New year</label><input style="{field_style("copy_year" in errors)}" type="number" name="year" value="{esc(copy_year)}"><p><button>Copy previous year</button></p></form></div>'
    body += '<div class="card"><h2>Existing years</h2><p>' + (', '.join(str(y) for y in available) if available else 'None yet.') + '</p></div>'
    return layout('Pricing years', body, context)


def register_catalogue_routes(app) -> None:
    database = app.state.database

    @app.get('/setup', response_class=HTMLResponse)
    def setup_home(request: Request):
        context = context_for(database, request); cid = working_company(context); company = database.company(cid)
        cards = [
            ('element_types', '<h2>Element Types</h2><p>Create the Client-controlled groups used by Elements.</p><a class="button" href="/setup/element-types">Open</a>'),
            ('elements', '<h2>Elements</h2><p>Bookable things with their own dates and controlled Element Type.</p><a class="button" href="/setup/elements">Open</a>'),
            ('person_types', '<h2>Person Types</h2><p>Adult, Child and any other occupant types you choose.</p><a class="button" href="/setup/person-types">Open</a>'),
            ('occupancy', '<h2>Occupancy</h2><p>Set total and Person Type limits and Person pricing for each Element.</p><a class="button" href="/setup/occupancy">Open</a>'),
            ('features_extras', '<h2>Features & Extras</h2><p>Define requirements and optional extras used by availability and bookings.</p><a class="button" href="/setup/addons">Open</a>'),
            ('feature_extra_rules', '<h2>Feature / Extra Rules</h2><p>Set Element Type defaults and Individual Element overrides.</p><a class="button" href="/setup/addon-rules">Open</a>'),
            ('years', '<h2>Annual setup</h2><p>Years, seasons and annual pricing structure.</p><a class="button" href="/setup/years">Open</a>'),
            ('pricing', '<h2>Seasonal pricing</h2><p>Set Element prices for each configured season.</p><a class="button" href="/setup/pricing">Open</a>'),
            ('price_test', '<h2>Price / Rules test</h2><p>Test real dates, people, Features / Extras, occupancy and calculated price.</p><a class="button" href="/setup/price-test">Open</a>'),
        ]
        body = f'<h1>{esc(company["name"])} — Setup</h1>' + sortable_menu_html(database, context, 'setup', cards)
        return layout('Setup', body, context)

    @app.get('/setup/element-types', response_class=HTMLResponse)
    def element_types(request: Request, edit: int = 0):
        context = context_for(database, request); return _element_types_page(database, context, edit=edit)

    @app.post('/setup/element-types')
    async def element_types_save(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data)
        name = data.get('name', '').strip(); raw_id = data.get('id', '')
        if not name:
            return HTMLResponse(_element_types_page(database, context, data, {'name'}, 'Element Type name cannot be empty.', int(raw_id) if raw_id.isdigit() else 0), 400)
        try:
            with database.connect() as c:
                if raw_id.isdigit():
                    old = c.execute('SELECT * FROM setup_element_types WHERE id=? AND company_id=?', (int(raw_id), cid)).fetchone()
                    if not old: return HTMLResponse(_element_types_page(database, context, data, {'name'}, 'Element Type was not found.'), 404)
                    before = dict(old); old_name = old['name']; entity_id = int(raw_id)
                    c.execute('UPDATE setup_element_types SET name=? WHERE id=? AND company_id=?', (name, entity_id, cid))
                    c.execute('UPDATE setup_elements SET element_type=? WHERE company_id=? AND element_type=?', (name, cid, old_name))
                    c.execute('UPDATE setup_type_addons SET element_type=? WHERE company_id=? AND element_type=?', (name, cid, old_name))
                else:
                    before = None; entity_id = c.execute('INSERT INTO setup_element_types(company_id,name) VALUES (?,?)', (cid, name)).lastrowid
        except sqlite3.IntegrityError:
            return HTMLResponse(_element_types_page(database, context, data, {'name'}, 'That Element Type already exists for this Client.'), 400)
        audit(database, context, cid, 'ELEMENT_TYPE_SAVED', 'element_type', entity_id, before, {'name': name})
        return RedirectResponse('/setup/element-types', 303)

    @app.post('/setup/element-types/toggle')
    async def element_type_toggle(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data)
        try: type_id = int(data.get('id', ''))
        except ValueError: return HTMLResponse(_element_types_page(database, context, message='Invalid Element Type.'), 400)
        with database.connect() as c:
            old = c.execute('SELECT * FROM setup_element_types WHERE id=? AND company_id=?', (type_id, cid)).fetchone()
            if not old: return HTMLResponse(_element_types_page(database, context, message='Element Type was not found.'), 404)
            new_state = 0 if old['active'] else 1
            c.execute('UPDATE setup_element_types SET active=? WHERE id=? AND company_id=?', (new_state, type_id, cid))
        audit(database, context, cid, 'ELEMENT_TYPE_STATUS_CHANGED', 'element_type', type_id, dict(old), {'active': new_state})
        return RedirectResponse('/setup/element-types', 303)

    @app.get('/setup/elements', response_class=HTMLResponse)
    def elements(request: Request, edit: int = 0, saved: int = 0):
        context = context_for(database, request); return _elements_page(database, context, edit=edit, saved=saved)

    @app.post('/setup/elements')
    async def elements_save(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data)
        name = data.get('name', '').strip(); element_type = data.get('element_type', '').strip(); method = data.get('pricing_method', ''); errors = set()
        if not name: errors.add('name')
        if not element_type or not rows(database, 'SELECT id FROM setup_element_types WHERE company_id=? AND name=?', (cid, element_type)): errors.add('element_type')
        if method not in ELEMENT_PRICING_METHODS: errors.add('pricing_method')
        try: base_price = valid_money(data.get('base_price', ''))
        except (TypeError, ValueError): base_price = 0; errors.add('base_price')
        edit_id = int(data['id']) if data.get('id', '').isdigit() else 0
        if errors: return HTMLResponse(_elements_page(database, context, data, errors, 'Complete every highlighted Element field with a valid value.', edit=edit_id), 400)
        try:
            with database.connect() as c:
                if edit_id:
                    old = c.execute('SELECT * FROM setup_elements WHERE id=? AND company_id=?', (edit_id, cid)).fetchone()
                    if not old: return HTMLResponse(_elements_page(database, context, data, {'name'}, 'Element was not found.'), 404)
                    before = dict(old); entity_id = edit_id
                    c.execute('UPDATE setup_elements SET name=?,element_type=?,pricing_method=?,base_price=? WHERE id=? AND company_id=?', (name, element_type, method, base_price, edit_id, cid))
                else:
                    before = None; entity_id = c.execute('INSERT INTO setup_elements(company_id,name,element_type,pricing_method,base_price) VALUES (?,?,?,?,?)', (cid, name, element_type, method, base_price)).lastrowid
        except sqlite3.IntegrityError:
            return HTMLResponse(_elements_page(database, context, data, {'name'}, 'An Element with that name already exists.', edit=edit_id), 400)
        audit(database, context, cid, 'ELEMENT_SAVED', 'element', entity_id, before, {'name': name, 'element_type': element_type, 'pricing_method': method, 'base_price': base_price})
        return RedirectResponse('/setup/elements?saved=1', 303)

    @app.get('/setup/person-types', response_class=HTMLResponse)
    def person_types(request: Request):
        context = context_for(database, request); return _catalog_page(database, context, 'setup_person_types', 'Person Types', '/setup/person-types')

    @app.post('/setup/person-types')
    async def person_types_save(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data); name = data.get('name', '').strip()
        if not name: return HTMLResponse(_catalog_page(database, context, 'setup_person_types', 'Person Types', '/setup/person-types', submitted=data, errors={'name'}, message='Person Type name cannot be empty.'), 400)
        try:
            with database.connect() as c: entity_id = c.execute('INSERT INTO setup_person_types(company_id,name,short_name) VALUES (?,?,?)', (cid, name, data.get('short_name', '').strip())).lastrowid
        except sqlite3.IntegrityError:
            return HTMLResponse(_catalog_page(database, context, 'setup_person_types', 'Person Types', '/setup/person-types', submitted=data, errors={'name'}, message='That Person Type already exists.'), 400)
        audit(database, context, cid, 'PERSON_TYPE_ADDED', 'person_type', entity_id, None, {'name': name}); return RedirectResponse('/setup/person-types', 303)

    @app.get('/setup/addons', response_class=HTMLResponse)
    def addons(request: Request):
        context = context_for(database, request); return _catalog_page(database, context, 'setup_addons', 'Add-ons', '/setup/addons', ADDON_PRICING_METHODS)

    @app.post('/setup/addons')
    async def addons_save(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data); name = data.get('name', '').strip(); method = data.get('pricing_method', ''); errors = set()
        if not name: errors.add('name')
        if method not in ADDON_PRICING_METHODS: errors.add('pricing_method')
        if errors: return HTMLResponse(_catalog_page(database, context, 'setup_addons', 'Add-ons', '/setup/addons', ADDON_PRICING_METHODS, data, errors, 'Complete every highlighted Add-on field.'), 400)
        try:
            with database.connect() as c: entity_id = c.execute('INSERT INTO setup_addons(company_id,name,pricing_method) VALUES (?,?,?)', (cid, name, method)).lastrowid
        except sqlite3.IntegrityError:
            return HTMLResponse(_catalog_page(database, context, 'setup_addons', 'Add-ons', '/setup/addons', ADDON_PRICING_METHODS, data, {'name'}, 'That Add-on already exists.'), 400)
        audit(database, context, cid, 'ADDON_ADDED', 'addon', entity_id, None, {'name': name, 'pricing_method': method}); return RedirectResponse('/setup/addons', 303)

    @app.get('/setup/years', response_class=HTMLResponse)
    def years_page(request: Request):
        context = context_for(database, request); return _years_page(database, context)

    @app.post('/setup/years/new')
    async def year_new(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data)
        try: year = int(data.get('year', ''))
        except ValueError: return HTMLResponse(_years_page(database, context, {'new_year': data.get('year', '')}, {'new_year'}, 'Enter a valid year.'), 400)
        try:
            with database.connect() as c:
                c.execute('INSERT INTO setup_years(company_id,year) VALUES (?,?)', (cid, year)); c.execute('INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)', (cid, year, f'All Year {year}', f'{year}-01-01', f'{year}-12-31'))
        except sqlite3.IntegrityError:
            return HTMLResponse(_years_page(database, context, {'new_year': str(year)}, {'new_year'}, 'That pricing year already exists.'), 400)
        audit(database, context, cid, 'PRICING_YEAR_CREATED', 'pricing_year', year, None, {'year': year}); return RedirectResponse('/setup/years', 303)

    @app.post('/setup/years/copy')
    async def year_copy(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data)
        try: target = int(data.get('year', ''))
        except ValueError: return HTMLResponse(_years_page(database, context, {'copy_year': data.get('year', '')}, {'copy_year'}, 'Enter a valid new year.'), 400)
        try: source = copy_previous_year(database, cid, target)
        except ValueError as exc: return HTMLResponse(_years_page(database, context, {'copy_year': str(target)}, {'copy_year'}, str(exc)), 400)
        audit(database, context, cid, 'PRICING_YEAR_COPIED', 'pricing_year', target, {'source': source}, {'year': target}); return RedirectResponse('/setup/years', 303)
