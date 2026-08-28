from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse

from .app import esc, layout
from .setup015_catalogue import error_box, setup_nav
from .setup015_core import context_for, working_company
from .setup015_maintenance import _confirm_form
from .webv1_ordering import person_type_rows, setup_sortable_table_bits


def _person_types_page(database, context, *, edit: int = 0, message: str = '') -> str:
    cid = working_company(context)
    items = person_type_rows(database, cid)
    current = next((r for r in items if int(r['id']) == int(edit or 0)), None)
    name = str(current['name']) if current else ''
    short = str(current['short_name']) if current else ''
    order_heading, order_script = setup_sortable_table_bits(context, 'person_types')

    body = f'<h1>Person Types</h1>{setup_nav()}{error_box(message)}'
    body += f'''<div class="card"><h2>{"Edit" if current else "Add"} Person Type</h2>
    <form method="post" action="/setup/maintenance/catalog/save"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="kind" value="person"><input type="hidden" name="id" value="{int(current["id"]) if current else ""}">
    <div class="grid"><div><label>Name</label><input name="name" value="{esc(name)}"></div><div><label>Short name</label><input name="short_name" maxlength="8" value="{esc(short)}"><p class="muted">Maximum 8 characters.</p></div></div>
    <p><button>{"Save changes" if current else "Add Person Type"}</button>{' &nbsp; <a class="button secondary" href="/setup/person-types">Cancel</a>' if current else ''}</p></form></div>'''

    body += '<div class="card"><p class="muted">Drag Person Types into the order you want them shown throughout Setup and Booking Requirements. The order is shared by the Client and a Supervisor working in Support Mode.</p>'
    body += f'<table><thead><tr>{order_heading}<th>Name</th><th>Short name</th><th>Status</th><th>Actions</th></tr></thead><tbody id="setup-sort-person_types">'
    for item in items:
        iid = int(item['id'])
        active = bool(item['active'])
        body += f'<tr data-item-id="{iid}"><td><span class="row-sort-handle" title="Drag to reorder">☰ Drag</span></td><td>{esc(item["name"])}</td><td>{esc(item["short_name"])}</td>'
        body += f'<td>{"Active" if active else "Inactive"}</td><td><a href="/setup/person-types?edit={iid}">Edit</a> &nbsp; '
        body += _confirm_form(context, '/setup/maintenance/catalog/toggle', {'kind': 'person', 'id': iid}, 'Deactivate' if active else 'Reactivate', secondary=True)
        body += ' &nbsp; ' + _confirm_form(context, '/setup/maintenance/catalog/delete', {'kind': 'person', 'id': iid}, 'Delete', secondary=True, confirm='Delete this Person Type? Used records will be protected and cannot be deleted.')
        body += '</td></tr>'
    body += '</tbody></table></div>' + order_script
    return layout('Person Types', body, context)


def register_ordered_person_types_route(app) -> None:
    database = app.state.database

    @app.get('/setup/person-types', response_class=HTMLResponse)
    def person_types_ordered(request: Request, edit: int = 0, message: str = ''):
        context = context_for(database, request)
        return HTMLResponse(_person_types_page(database, context, edit=edit, message=message))
