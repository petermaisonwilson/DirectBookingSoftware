from __future__ import annotations

import sqlite3

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, form_data, layout
from .setup015_catalogue import error_box, field_style, setup_nav
from .setup015_core import ELEMENT_PRICING_METHODS, audit, context_for, require_csrf, rows, working_company
from .setup015_maintenance import _confirm_form


def _page(database, context, submitted=None, errors=None, message: str = '', edit: int = 0, saved: int = 0) -> str:
    cid = int(working_company(context)); submitted = submitted or {}; errors = errors or set()
    element_rows = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? ORDER BY element_type,name', (cid,))
    current = next((r for r in element_rows if int(r['id']) == int(edit or 0)), None)
    active_types = list(rows(database, 'SELECT * FROM setup_element_types WHERE company_id=? AND active=1 ORDER BY name', (cid,)))
    selected_type = submitted.get('element_type', current['element_type'] if current else '')
    if current and selected_type and not any(str(t['name']) == selected_type for t in active_types):
        active_types.append({'name': selected_type})
    name = submitted.get('name', current['name'] if current else '')
    method = submitted.get('pricing_method', current['pricing_method'] if current else ELEMENT_PRICING_METHODS[0])
    type_options = '<option value="">-- choose Element Type --</option>' + ''.join(
        f'<option value="{esc(t["name"])}" {"selected" if t["name"] == selected_type else ""}>{esc(t["name"])}</option>' for t in active_types
    )
    method_options = ''.join(f'<option {"selected" if item == method else ""}>{esc(item)}</option>' for item in ELEMENT_PRICING_METHODS)

    body = f'<h1>Elements</h1>{setup_nav()}{error_box(message)}' + ('<div class="ok">Saved and audited.</div>' if saved else '')
    if not active_types:
        body += '<div class="error"><strong>Create an Element Type first.</strong><br>Elements can only be assigned to a Client-defined Element Type.</div>'
    body += f'''<div class="card"><h2>{"Edit" if current else "Add"} Element</h2>
    <p class="muted">Element prices are entered in <strong>Seasonal pricing</strong> for each pricing year.</p>
    <form method="post" action="/setup/elements"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="id" value="{esc(current["id"] if current else "")}">
    <div class="grid"><div><label>Name</label><input style="{field_style("name" in errors)}" name="name" value="{esc(name)}"></div><div><label>Element Type</label><select style="{field_style("element_type" in errors)}" name="element_type">{type_options}</select></div><div><label>Pricing method</label><select style="{field_style("pricing_method" in errors)}" name="pricing_method">{method_options}</select></div></div>
    <p><button {"disabled" if not active_types else ""}>Save Element</button></p></form></div>'''

    body += '<div class="card"><table><thead><tr><th>Name</th><th>Element Type</th><th>Pricing method</th><th>Actions</th></tr></thead><tbody>'
    for row in element_rows:
        iid = int(row['id']); active = bool(row['active']); display_name = esc(row['name']) + ('' if active else ' <span class="muted">(Inactive)</span>')
        actions = f'<a href="/setup/elements?edit={iid}">Edit</a> &nbsp; <a href="/setup/elements/availability?element_id={iid}">Availability</a> &nbsp; '
        actions += _confirm_form(context, '/setup/maintenance/catalog/toggle', {'kind': 'element', 'id': iid}, 'Deactivate' if active else 'Reactivate', secondary=True)
        actions += ' &nbsp; ' + _confirm_form(context, '/setup/maintenance/catalog/delete', {'kind': 'element', 'id': iid}, 'Delete', secondary=True, confirm='Delete this Element? Used Elements are protected and cannot be deleted.')
        body += f'<tr><td>{display_name}</td><td>{esc(row["element_type"])}</td><td>{esc(row["pricing_method"])}</td><td>{actions}</td></tr>'
    if not element_rows:
        body += '<tr><td colspan="4">No Elements yet.</td></tr>'
    body += '</tbody></table></div>'

    if current:
        body += f'<div class="card"><h2>Availability</h2><p><strong>Available throughout the operating season</strong> unless a dated closure is added.</p><p><a class="button secondary" href="/setup/elements/availability?element_id={int(current["id"])}">Manage closed dates</a></p></div>'
    return layout('Elements', body, context)


def register_elements_no_base_price(app) -> None:
    database = app.state.database

    @app.get('/setup/elements', response_class=HTMLResponse)
    def elements_page(request: Request, edit: int = 0, saved: int = 0, message: str = ''):
        context = context_for(database, request)
        return HTMLResponse(_page(database, context, message=message, edit=edit, saved=saved))

    @app.post('/setup/elements')
    async def elements_save(request: Request):
        context = context_for(database, request); cid = int(working_company(context)); data = await form_data(request); require_csrf(context, data)
        name = data.get('name', '').strip(); element_type = data.get('element_type', '').strip(); method = data.get('pricing_method', ''); errors = set()
        if not name: errors.add('name')
        if not element_type or not rows(database, 'SELECT id FROM setup_element_types WHERE company_id=? AND name=?', (cid, element_type)): errors.add('element_type')
        if method not in ELEMENT_PRICING_METHODS: errors.add('pricing_method')
        edit_id = int(data['id']) if data.get('id', '').isdigit() else 0
        if errors:
            return HTMLResponse(_page(database, context, data, errors, 'Complete every highlighted Element field with a valid value.', edit=edit_id), 400)
        try:
            with database.connect() as connection:
                if edit_id:
                    old = connection.execute('SELECT * FROM setup_elements WHERE id=? AND company_id=?', (edit_id, cid)).fetchone()
                    if not old:
                        return HTMLResponse(_page(database, context, data, {'name'}, 'Element was not found.'), 404)
                    before = dict(old); entity_id = edit_id
                    connection.execute('UPDATE setup_elements SET name=?,element_type=?,pricing_method=? WHERE id=? AND company_id=?', (name, element_type, method, edit_id, cid))
                else:
                    before = None
                    entity_id = connection.execute('INSERT INTO setup_elements(company_id,name,element_type,pricing_method) VALUES (?,?,?,?)', (cid, name, element_type, method)).lastrowid
        except sqlite3.IntegrityError:
            return HTMLResponse(_page(database, context, data, {'name'}, 'An Element with that name already exists.', edit=edit_id), 400)
        audit(database, context, cid, 'ELEMENT_SAVED', 'element', entity_id, before, {'name': name, 'element_type': element_type, 'pricing_method': method})
        return RedirectResponse('/setup/elements?saved=1', 303)
