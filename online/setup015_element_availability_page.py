from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse

from .app import esc
from .setup015_catalogue import _elements_page
from .setup015_core import context_for, rows, working_company
from .setup015_maintenance import _confirm_form


def register_element_availability_page(app) -> None:
    database = app.state.database

    @app.get('/setup/elements', response_class=HTMLResponse)
    def elements_with_availability(request: Request, edit: int = 0, saved: int = 0, message: str = ''):
        context = context_for(database, request); cid = working_company(context)
        html = _elements_page(database, context, message=message, edit=edit, saved=saved)
        for row in rows(database, 'SELECT * FROM setup_elements WHERE company_id=? ORDER BY element_type,name', (cid,)):
            iid = int(row['id']); link = f'<a href="/setup/elements?edit={iid}">Edit</a>'
            availability = f'<a href="/setup/elements/availability?element_id={iid}">Availability</a>'
            toggle = _confirm_form(context, '/setup/maintenance/catalog/toggle', {'kind': 'element', 'id': iid}, 'Deactivate' if row['active'] else 'Reactivate', secondary=True)
            delete = _confirm_form(context, '/setup/maintenance/catalog/delete', {'kind': 'element', 'id': iid}, 'Delete', secondary=True, confirm='Delete this Element? Used Elements are protected and cannot be deleted.')
            html = html.replace(link, link + ' &nbsp; ' + availability + ' &nbsp; ' + toggle + ' &nbsp; ' + delete, 1)
            if not row['active']:
                html = html.replace(f'<td>{esc(row["name"])}</td>', f'<td>{esc(row["name"])} <span class="muted">(Inactive)</span></td>', 1)
        if edit:
            marker = '<p><button '
            note = f'<div class="card"><h2>Availability</h2><p><strong>Available throughout the operating season</strong> unless a dated closure is added.</p><p><a class="button secondary" href="/setup/elements/availability?element_id={int(edit)}">Manage closed dates</a></p></div>'
            if marker in html and '</main>' in html:
                html = html.replace('</main>', note + '</main>', 1)
        return HTMLResponse(html)
