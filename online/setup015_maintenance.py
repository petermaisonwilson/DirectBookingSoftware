from __future__ import annotations
from .sql import execute_sql, insert_sql, table_exists

from datetime import date
from urllib.parse import quote_plus

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, form_data, layout
from .setup015_annual import _addon_page, _pricing_page
from .setup015_catalogue import _element_types_page, _elements_page, _years_page, error_box, field_style, setup_nav
from .setup015_core import ADDON_PRICING_METHODS, audit, context_for, require_csrf, rows, selected_year, working_company, years


def _table_exists(connection, table: str) -> bool:
    return (True if table_exists(connection, table) else None) is not None


def _count_if_table(connection, table: str, where: str, params: tuple[object, ...]) -> int:
    if not _table_exists(connection, table):
        return 0
    return int(execute_sql(connection, f'SELECT COUNT(*) AS n FROM {table} WHERE {where}', params).fetchone()['n'])


def _delete_if_table(connection, table: str, where: str, params: tuple[object, ...]) -> None:
    if _table_exists(connection, table):
        execute_sql(connection, f'DELETE FROM {table} WHERE {where}', params)


def _append_before_main_end(html: str, extra: str) -> str:
    return html.replace('</main>', extra + '</main>', 1) if '</main>' in html else html + extra


def _confirm_form(context, action: str, hidden: dict[str, object], label: str, *, secondary: bool = False, confirm: str = '') -> str:
    fields = ''.join(f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">' for k, v in hidden.items())
    cls = ' class="secondary"' if secondary else ''
    onsubmit = f' onsubmit="return confirm(\'{esc(confirm)}\')"' if confirm else ''
    return f'<form method="post" action="{action}" style="display:inline"{onsubmit}><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}">{fields}<button{cls}>{esc(label)}</button></form>'


def _catalog_page(database, context, kind: str, *, edit: int = 0, message: str = '') -> str:
    cid = working_company(context)
    if kind == 'person':
        table = 'setup_person_types'; title = 'Person Types'; singular = 'Person Type'; methods: tuple[str, ...] = ()
    else:
        table = 'setup_addons'; title = 'Add-ons'; singular = 'Add-on'; methods = ADDON_PRICING_METHODS
    items = rows(database, f'SELECT * FROM {table} WHERE company_id=:q35_0 ORDER BY active DESC,lower(name)', (cid,))
    current = next((r for r in items if int(r['id']) == int(edit or 0)), None)
    name = str(current['name']) if current else ''
    short = str(current['short_name']) if current and kind == 'person' else ''
    method = str(current['pricing_method']) if current and kind == 'addon' else (methods[0] if methods else '')
    method_html = ''
    if kind == 'addon':
        method_html = '<div><label>Pricing method</label><select name="pricing_method">' + ''.join(f'<option {"selected" if m == method else ""}>{esc(m)}</option>' for m in methods) + '</select></div>'
    short_html = f'<div><label>Short name</label><input name="short_name" maxlength="8" value="{esc(short)}"><p class="muted">Maximum 8 characters.</p></div>' if kind == 'person' else ''
    body = f'<h1>{title}</h1>{setup_nav()}{error_box(message)}'
    body += f'''<div class="card"><h2>{"Edit" if current else "Add"} {singular}</h2>
    <form method="post" action="/setup/maintenance/catalog/save"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="kind" value="{kind}"><input type="hidden" name="id" value="{int(current["id"]) if current else ""}">
    <div class="grid"><div><label>Name</label><input name="name" value="{esc(name)}"></div>{short_html}{method_html}</div>
    <p><button>{"Save changes" if current else "Add " + singular}</button>{' &nbsp; <a class="button secondary" href="/setup/person-types">Cancel</a>' if current and kind == 'person' else (' &nbsp; <a class="button secondary" href="/setup/addons">Cancel</a>' if current else '')}</p></form></div>'''
    body += '<div class="card"><table><thead><tr><th>Name</th>'
    if kind == 'person': body += '<th>Short name</th>'
    if kind == 'addon': body += '<th>Pricing method</th>'
    body += '<th>Status</th><th>Actions</th></tr></thead><tbody>'
    for item in items:
        iid = int(item['id']); active = bool(item['active']); body += f'<tr><td>{esc(item["name"])}</td>'
        if kind == 'person': body += f'<td>{esc(item["short_name"])}</td>'
        if kind == 'addon': body += f'<td>{esc(item["pricing_method"])}</td>'
        body += f'<td>{"Active" if active else "Inactive"}</td><td><a href="/setup/{"person-types" if kind == "person" else "addons"}?edit={iid}">Edit</a> &nbsp; '
        body += _confirm_form(context, '/setup/maintenance/catalog/toggle', {'kind': kind, 'id': iid}, 'Deactivate' if active else 'Reactivate', secondary=True)
        body += ' &nbsp; ' + _confirm_form(context, '/setup/maintenance/catalog/delete', {'kind': kind, 'id': iid}, 'Delete', secondary=True, confirm=f'Delete this {singular}? Used records will be protected and cannot be deleted.')
        body += '</td></tr>'
    body += '</tbody></table></div>'
    return layout(title, body, context)


def _historical_count(connection, kind: str, item_id: int, company_id: int) -> int:
    checks: list[tuple[str, str, tuple[object, ...]]] = []
    if kind == 'element':
        checks = [('enquiry_requests', 'company_id=:extra10001_0 AND element_id=:extra10001_1', (company_id, item_id)), ('booking_elements', 'company_id=:extra10002_0 AND element_id=:extra10002_1', (company_id, item_id))]
    elif kind == 'person':
        checks = [
            ('enquiry_people', 'company_id=:extra10006_0 AND person_type_id=:extra10006_1', (company_id, item_id)),
            ('enquiry_addon_people', 'company_id=:extra10007_0 AND person_type_id=:extra10007_1', (company_id, item_id)),
            ('enquiry_addon_person_days', 'company_id=:extra10008_0 AND person_type_id=:extra10008_1', (company_id, item_id)),
            ('booking_people', 'company_id=:extra10009_0 AND person_type_id=:extra10009_1', (company_id, item_id)),
        ]
    elif kind == 'addon':
        checks = [
            ('enquiry_addons', 'company_id=:extra10015_0 AND addon_id=:extra10015_1', (company_id, item_id)),
            ('enquiry_selected_addons', 'company_id=:extra10016_0 AND addon_id=:extra10016_1', (company_id, item_id)),
            ('enquiry_addon_days', 'company_id=:extra10017_0 AND addon_id=:extra10017_1', (company_id, item_id)),
            ('enquiry_addon_people', 'company_id=:extra10018_0 AND addon_id=:extra10018_1', (company_id, item_id)),
            ('enquiry_addon_person_days', 'company_id=:extra10019_0 AND addon_id=:extra10019_1', (company_id, item_id)),
            ('booking_addons', 'company_id=:extra10020_0 AND addon_id=:extra10020_1', (company_id, item_id)),
        ]
    return sum(_count_if_table(connection, table, where, params) for table, where, params in checks)


def _year_has_history(connection, company_id: int, year: int) -> bool:
    start = f'{year:04d}-01-01'; end = f'{year + 1:04d}-01-01'
    for table in ('enquiries', 'bookings'):
        if not _table_exists(connection, table):
            continue
        row = execute_sql(connection, f'SELECT 1 FROM {table} WHERE company_id=:q39_0 AND arrival_date IS NOT NULL AND departure_date IS NOT NULL AND date(arrival_date) < date(:q39_1) AND date(departure_date) > date(:q39_2) LIMIT 1', (company_id, end, start)).fetchone()
        if row:
            return True
    return False


def register_setup_maintenance_routes(app) -> None:
    database = app.state.database

    @app.get('/setup/elements', response_class=HTMLResponse)
    def elements_maintenance(request: Request, edit: int = 0, saved: int = 0, message: str = ''):
        context = context_for(database, request); cid = working_company(context)
        html = _elements_page(database, context, message=message, edit=edit, saved=saved)
        for row in rows(database, 'SELECT * FROM setup_elements WHERE company_id=:q18_0 ORDER BY element_type,name', (cid,)):
            iid = int(row['id']); link = f'<a href="/setup/elements?edit={iid}">Edit</a>'
            toggle = _confirm_form(context, '/setup/maintenance/catalog/toggle', {'kind': 'element', 'id': iid}, 'Deactivate' if row['active'] else 'Reactivate', secondary=True)
            delete = _confirm_form(context, '/setup/maintenance/catalog/delete', {'kind': 'element', 'id': iid}, 'Delete', secondary=True, confirm='Delete this Element? Used Elements are protected and cannot be deleted.')
            html = html.replace(link, link + ' &nbsp; ' + toggle + ' &nbsp; ' + delete, 1)
            if not row['active']:
                html = html.replace(f'<td>{esc(row["name"])}</td>', f'<td>{esc(row["name"])} <span class="muted">(Inactive)</span></td>', 1)
        return HTMLResponse(html)

    @app.get('/setup/element-types', response_class=HTMLResponse)
    def element_types_maintenance(request: Request, edit: int = 0, message: str = ''):
        context = context_for(database, request); cid = working_company(context)
        html = _element_types_page(database, context, message=message, edit=edit)
        for row in rows(database, 'SELECT * FROM setup_element_types WHERE company_id=:q19_0 ORDER BY active DESC,name', (cid,)):
            iid = int(row['id']); toggle_label = 'Reactivate' if not row['active'] else 'Deactivate'
            old = f'<a href="/setup/element-types?edit={iid}">Rename</a> &nbsp; <form method="post" action="/setup/element-types/toggle" style="display:inline"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="id" value="{iid}"><button class="secondary">{toggle_label}</button></form>'
            delete = _confirm_form(context, '/setup/maintenance/element-types/delete', {'id': iid}, 'Delete', secondary=True, confirm='Delete this Element Type? Types still used by Elements or Add-on rules cannot be deleted.')
            html = html.replace(old, old + ' &nbsp; ' + delete, 1)
        return HTMLResponse(html)

    @app.get('/setup/person-types', response_class=HTMLResponse)
    def person_types_maintenance(request: Request, edit: int = 0, message: str = ''):
        return HTMLResponse(_catalog_page(database, context_for(database, request), 'person', edit=edit, message=message))

    @app.get('/setup/addons', response_class=HTMLResponse)
    def addons_maintenance(request: Request, edit: int = 0, message: str = ''):
        return HTMLResponse(_catalog_page(database, context_for(database, request), 'addon', edit=edit, message=message))

    @app.get('/setup/years', response_class=HTMLResponse)
    def years_maintenance(request: Request, message: str = ''):
        context = context_for(database, request); cid = working_company(context)
        html = _years_page(database, context, message=message)
        actions = '<div class="card"><h2>Year maintenance</h2><p>Years used by saved Enquiries or Bookings are protected.</p>'
        for y in years(database, cid):
            actions += f'<p><strong>{y}</strong> &nbsp; ' + _confirm_form(context, '/setup/maintenance/years/delete', {'year': y}, 'Delete year', secondary=True, confirm=f'Delete all Setup data for {y}?') + '</p>'
        actions += '</div>'
        return HTMLResponse(_append_before_main_end(html, actions))

    @app.get('/setup/pricing', response_class=HTMLResponse)
    def pricing_maintenance(request: Request, year: str = '', message: str = ''):
        context = context_for(database, request); cid = working_company(context); selected = selected_year(database, cid, year)
        html = _pricing_page(database, context, selected, message=message)
        if selected is not None:
            extra = '<div class="card"><h2>Season maintenance</h2><p>Seasons in years already used by Enquiries or Bookings are protected from destructive changes.</p><table><thead><tr><th>Season</th><th>Dates</th><th>Actions</th></tr></thead><tbody>'
            for s in rows(database, 'SELECT * FROM setup_seasons WHERE company_id=:q21_0 AND year=:q21_1 ORDER BY start_date', (cid, selected)):
                sid = int(s['id']); extra += f'<tr><td>{esc(s["name"])}</td><td>{esc(s["start_date"])} to {esc(s["end_date"])}</td><td><a href="/setup/maintenance/seasons/edit?id={sid}">Edit</a> &nbsp; ' + _confirm_form(context, '/setup/maintenance/seasons/delete', {'id': sid}, 'Delete', secondary=True, confirm='Delete this Season and its Element price cells?') + '</td></tr>'
            extra += '</tbody></table></div>'
            html = _append_before_main_end(html, extra)
        return HTMLResponse(html)

    @app.get('/setup/addon-rules', response_class=HTMLResponse)
    def addon_rules_maintenance(request: Request, year: str = '', message: str = ''):
        context = context_for(database, request); cid = working_company(context); selected = selected_year(database, cid, year)
        html = _addon_page(database, context, selected, message=message)
        if selected is not None:
            extra = '<div class="card"><h2>Rule reset</h2><p>Individual overrides already clear when set back to <strong>I = Inherit</strong>. Use this only if you want to remove every Add-on rule for the selected year.</p>' + _confirm_form(context, '/setup/maintenance/addon-rules/reset', {'year': selected}, 'Reset all Add-on rules for this year', secondary=True, confirm=f'Remove every Add-on rule for {selected}?') + '</div>'
            html = _append_before_main_end(html, extra)
        return HTMLResponse(html)

    @app.post('/setup/maintenance/catalog/save')
    async def catalog_save(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data)
        kind = data.get('kind', ''); raw_id = data.get('id', ''); item_id = int(raw_id) if raw_id.isdigit() else 0; name = data.get('name', '').strip()
        if kind not in {'person', 'addon'} or not name:
            path = '/setup/person-types' if kind == 'person' else '/setup/addons'; return RedirectResponse(path + '?message=' + quote_plus('Enter a name.'), 303)
        table = 'setup_person_types' if kind == 'person' else 'setup_addons'; path = '/setup/person-types' if kind == 'person' else '/setup/addons'
        short = data.get('short_name', '').strip()[:8]; method = data.get('pricing_method', '')
        if kind == 'addon' and method not in ADDON_PRICING_METHODS:
            return RedirectResponse(path + '?message=' + quote_plus('Choose a valid pricing method.'), 303)
        try:
            with database.connect() as c:
                before = execute_sql(c, f'SELECT * FROM {table} WHERE id=:q43_0 AND company_id=:q43_1', (item_id, cid)).fetchone() if item_id else None
                if item_id and before is None: return RedirectResponse(path + '?message=' + quote_plus('Item was not found.'), 303)
                if kind == 'person':
                    if item_id: execute_sql(c, 'UPDATE setup_person_types SET name=:q31_0,short_name=:q31_1 WHERE id=:q31_2 AND company_id=:q31_3', (name, short, item_id, cid)); entity_id = item_id
                    else: entity_id = int(insert_sql(c, 'INSERT INTO setup_person_types(company_id,name,short_name) VALUES (:q33_0,:q33_1,:q33_2)', (cid, name, short)).lastrowid)
                    after = {'name': name, 'short_name': short}
                else:
                    if item_id: execute_sql(c, 'UPDATE setup_addons SET name=:q32_0,pricing_method=:q32_1 WHERE id=:q32_2 AND company_id=:q32_3', (name, method, item_id, cid)); entity_id = item_id
                    else: entity_id = int(insert_sql(c, 'INSERT INTO setup_addons(company_id,name,pricing_method) VALUES (:q34_0,:q34_1,:q34_2)', (cid, name, method)).lastrowid)
                    after = {'name': name, 'pricing_method': method}
        except Exception:
            return RedirectResponse(path + '?message=' + quote_plus('That name already exists or could not be saved.'), 303)
        audit(database, context, cid, 'PERSON_TYPE_SAVED' if kind == 'person' else 'ADDON_SAVED', 'person_type' if kind == 'person' else 'addon', entity_id, dict(before) if before else None, after)
        return RedirectResponse(path, 303)

    @app.post('/setup/maintenance/catalog/toggle')
    async def catalog_toggle(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data)
        kind = data.get('kind', ''); raw_id = data.get('id', '')
        if kind not in {'element', 'person', 'addon'} or not raw_id.isdigit(): raise ValueError('Invalid maintenance item')
        item_id = int(raw_id); table = {'element':'setup_elements','person':'setup_person_types','addon':'setup_addons'}[kind]; path = {'element':'/setup/elements','person':'/setup/person-types','addon':'/setup/addons'}[kind]
        with database.connect() as c:
            before = execute_sql(c, f'SELECT * FROM {table} WHERE id=:q41_0 AND company_id=:q41_1', (item_id, cid)).fetchone()
            if before is None: return RedirectResponse(path + '?message=' + quote_plus('Item was not found.'), 303)
            new_state = 0 if before['active'] else 1; execute_sql(c, f'UPDATE {table} SET active=:q37_0 WHERE id=:q37_1 AND company_id=:q37_2', (new_state, item_id, cid))
        audit(database, context, cid, f'{kind.upper()}_STATUS_CHANGED', kind, item_id, dict(before), {'active': new_state})
        return RedirectResponse(path, 303)

    @app.post('/setup/maintenance/catalog/delete')
    async def catalog_delete(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data)
        kind = data.get('kind', ''); raw_id = data.get('id', '')
        if kind not in {'element', 'person', 'addon'} or not raw_id.isdigit(): raise ValueError('Invalid maintenance item')
        item_id = int(raw_id); table = {'element':'setup_elements','person':'setup_person_types','addon':'setup_addons'}[kind]; path = {'element':'/setup/elements','person':'/setup/person-types','addon':'/setup/addons'}[kind]
        with database.connect() as c:
            before = execute_sql(c, f'SELECT * FROM {table} WHERE id=:q42_0 AND company_id=:q42_1', (item_id, cid)).fetchone()
            if before is None: return RedirectResponse(path + '?message=' + quote_plus('Item was not found.'), 303)
            if _historical_count(c, kind, item_id, cid):
                return RedirectResponse(path + '?message=' + quote_plus('This item has saved Enquiry/Booking history and cannot be deleted. Deactivate it instead.'), 303)
            if kind == 'element':
                for child in ('setup_element_rates','setup_occupancy','setup_person_limits','setup_person_prices','setup_element_addons'): _delete_if_table(c, child, 'company_id=:extra10021_0 AND element_id=:extra10021_1', (cid, item_id))
            elif kind == 'person':
                for child in ('setup_person_limits','setup_person_prices','setup_addon_person_rates'): _delete_if_table(c, child, 'company_id=:extra10022_0 AND person_type_id=:extra10022_1', (cid, item_id))
            else:
                for child in ('setup_type_addons','setup_element_addons','setup_addon_when_options','setup_addon_person_rates','setup_addon_person_pricing'): _delete_if_table(c, child, 'company_id=:extra10023_0 AND addon_id=:extra10023_1', (cid, item_id))
            execute_sql(c, f'DELETE FROM {table} WHERE id=:q38_0 AND company_id=:q38_1', (item_id, cid))
        audit(database, context, cid, f'{kind.upper()}_DELETED', kind, item_id, dict(before), None)
        return RedirectResponse(path, 303)

    @app.post('/setup/maintenance/element-types/delete')
    async def element_type_delete(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data); raw_id = data.get('id','')
        if not raw_id.isdigit(): return RedirectResponse('/setup/element-types?message=' + quote_plus('Invalid Element Type.'), 303)
        item_id = int(raw_id)
        with database.connect() as c:
            before = execute_sql(c, 'SELECT * FROM setup_element_types WHERE id=:q27_0 AND company_id=:q27_1', (item_id, cid)).fetchone()
            if before is None: return RedirectResponse('/setup/element-types?message=' + quote_plus('Element Type was not found.'), 303)
            name = str(before['name']); used = _count_if_table(c, 'setup_elements', 'company_id=:extra10010_0 AND element_type=:extra10010_1', (cid, name)) + _count_if_table(c, 'setup_type_addons', 'company_id=:extra10011_0 AND element_type=:extra10011_1', (cid, name))
            if used: return RedirectResponse('/setup/element-types?message=' + quote_plus('This Element Type is still used by Elements or Add-on rules. Deactivate it instead.'), 303)
            execute_sql(c, 'DELETE FROM setup_element_types WHERE id=:q22_0 AND company_id=:q22_1', (item_id, cid))
        audit(database, context, cid, 'ELEMENT_TYPE_DELETED', 'element_type', item_id, dict(before), None); return RedirectResponse('/setup/element-types', 303)

    @app.post('/setup/maintenance/years/delete')
    async def year_delete(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data)
        try: year = int(data.get('year',''))
        except ValueError: return RedirectResponse('/setup/years?message=' + quote_plus('Invalid year.'), 303)
        with database.connect() as c:
            before = execute_sql(c, 'SELECT * FROM setup_years WHERE company_id=:q28_0 AND year=:q28_1', (cid, year)).fetchone()
            if before is None: return RedirectResponse('/setup/years?message=' + quote_plus('Year was not found.'), 303)
            if _year_has_history(c, cid, year): return RedirectResponse('/setup/years?message=' + quote_plus('This year is used by saved Enquiries or Bookings and cannot be deleted.'), 303)
            for table in ('setup_addon_person_rates','setup_element_addons','setup_type_addons','setup_person_prices','setup_person_limits','setup_occupancy','setup_element_rates','setup_seasons'): _delete_if_table(c, table, 'company_id=:extra10012_0 AND year=:extra10012_1', (cid, year))
            execute_sql(c, 'DELETE FROM setup_years WHERE company_id=:q23_0 AND year=:q23_1', (cid, year))
        audit(database, context, cid, 'PRICING_YEAR_DELETED', 'pricing_year', year, dict(before), None); return RedirectResponse('/setup/years', 303)

    @app.get('/setup/maintenance/seasons/edit', response_class=HTMLResponse)
    def season_edit(request: Request, id: int):
        context = context_for(database, request); cid = working_company(context); found = rows(database, 'SELECT * FROM setup_seasons WHERE company_id=:q20_0 AND id=:q20_1', (cid, id))
        if not found: return HTMLResponse(layout('Season not found', f'<h1>Season not found</h1>{setup_nav()}', context), 404)
        s = found[0]; body = f'''<h1>Edit Season</h1>{setup_nav()}<div class="card"><form method="post" action="/setup/maintenance/seasons/save"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="id" value="{id}"><div class="grid"><div><label>Name</label><input name="name" value="{esc(s["name"])}"></div><div><label>Start</label><input type="date" name="start_date" value="{esc(s["start_date"])}"></div><div><label>End</label><input type="date" name="end_date" value="{esc(s["end_date"])}"></div></div><p><button>Save Season</button> &nbsp; <a class="button secondary" href="/setup/pricing?year={int(s["year"])}">Cancel</a></p></form></div>'''; return HTMLResponse(layout('Edit Season', body, context))

    @app.post('/setup/maintenance/seasons/save')
    async def season_save(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data)
        try: sid = int(data.get('id','')); start = date.fromisoformat(data.get('start_date','')); end = date.fromisoformat(data.get('end_date',''))
        except ValueError: return RedirectResponse('/setup/pricing?message=' + quote_plus('Enter valid Season dates.'), 303)
        name = data.get('name','').strip()
        with database.connect() as c:
            before = execute_sql(c, 'SELECT * FROM setup_seasons WHERE company_id=:q29_0 AND id=:q29_1', (cid, sid)).fetchone()
            if before is None: return RedirectResponse('/setup/pricing?message=' + quote_plus('Season was not found.'), 303)
            year = int(before['year'])
            if _year_has_history(c, cid, year): return RedirectResponse(f'/setup/pricing?year={year}&message=' + quote_plus('This year has saved Enquiry/Booking history, so Season dates are protected.'), 303)
            if not name or end < start: return RedirectResponse(f'/setup/pricing?year={year}&message=' + quote_plus('Enter a name and an end date on or after the start date.'), 303)
            execute_sql(c, 'UPDATE setup_seasons SET name=:q24_0,start_date=:q24_1,end_date=:q24_2 WHERE company_id=:q24_3 AND id=:q24_4', (name, start.isoformat(), end.isoformat(), cid, sid))
        audit(database, context, cid, 'SEASON_SAVED', 'season', sid, dict(before), {'year': year, 'name': name, 'start_date': start.isoformat(), 'end_date': end.isoformat()}); return RedirectResponse(f'/setup/pricing?year={year}', 303)

    @app.post('/setup/maintenance/seasons/delete')
    async def season_delete(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data)
        try: sid = int(data.get('id',''))
        except ValueError: return RedirectResponse('/setup/pricing?message=' + quote_plus('Invalid Season.'), 303)
        with database.connect() as c:
            before = execute_sql(c, 'SELECT * FROM setup_seasons WHERE company_id=:q30_0 AND id=:q30_1', (cid, sid)).fetchone()
            if before is None: return RedirectResponse('/setup/pricing?message=' + quote_plus('Season was not found.'), 303)
            year = int(before['year'])
            if _year_has_history(c, cid, year): return RedirectResponse(f'/setup/pricing?year={year}&message=' + quote_plus('This year has saved Enquiry/Booking history, so the Season cannot be deleted.'), 303)
            _delete_if_table(c, 'setup_element_rates', 'company_id=:extra10003_0 AND year=:extra10003_1 AND season_id=:extra10003_2', (cid, year, sid)); execute_sql(c, 'DELETE FROM setup_seasons WHERE company_id=:q25_0 AND id=:q25_1', (cid, sid))
        audit(database, context, cid, 'SEASON_DELETED', 'season', sid, dict(before), None); return RedirectResponse(f'/setup/pricing?year={year}', 303)

    @app.post('/setup/maintenance/addon-rules/reset')
    async def addon_rules_reset(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data)
        try: year = int(data.get('year',''))
        except ValueError: return RedirectResponse('/setup/addon-rules?message=' + quote_plus('Invalid year.'), 303)
        with database.connect() as c:
            before = {'type_rules': _count_if_table(c, 'setup_type_addons', 'company_id=:extra10013_0 AND year=:extra10013_1', (cid, year)), 'element_overrides': _count_if_table(c, 'setup_element_addons', 'company_id=:extra10014_0 AND year=:extra10014_1', (cid, year))}
            _delete_if_table(c, 'setup_type_addons', 'company_id=:extra10004_0 AND year=:extra10004_1', (cid, year)); _delete_if_table(c, 'setup_element_addons', 'company_id=:extra10005_0 AND year=:extra10005_1', (cid, year))
        audit(database, context, cid, 'ADDON_RULES_RESET', 'pricing_year', year, before, {'type_rules': 0, 'element_overrides': 0}); return RedirectResponse(f'/setup/addon-rules?year={year}', 303)
