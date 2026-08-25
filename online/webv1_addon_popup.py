from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import Request
from fastapi.responses import RedirectResponse, Response

from .app import COOKIE_NAME, esc, form_data
from .setup015_calculator import _addon_rule
from .setup015_core import audit, context_for, require_csrf, rows, working_company


def initialise_addon_popup(database) -> None:
    """Add the customer/staff popup flag without disturbing existing Add-on data."""
    with database.connect() as c:
        columns = {str(r['name']) for r in c.execute('PRAGMA table_info(setup_addons)').fetchall()}
        if 'popup' not in columns:
            c.execute('ALTER TABLE setup_addons ADD COLUMN popup INTEGER NOT NULL DEFAULT 0')


def popup_addons_for_element(database, company_id: int, year: int, element) -> list[dict[str, object]]:
    """Resolve popup flags through the same Type-default/Element-override rules used everywhere else."""
    result: list[dict[str, object]] = []
    for addon in rows(
        database,
        'SELECT * FROM setup_addons WHERE company_id=? AND active=1 AND popup=1 ORDER BY name COLLATE NOCASE',
        (company_id,),
    ):
        rule = _addon_rule(database, company_id, year, element, int(addon['id']))
        result.append({
            'id': int(addon['id']),
            'name': str(addon['name']),
            'available': bool(rule['allowed']),
        })
    return result


def register_addon_popup_routes(app) -> None:
    database = app.state.database

    @app.post('/setup/addons/popup')
    async def addon_popup_toggle(request: Request):
        context = context_for(database, request)
        company_id = int(working_company(context))
        data = await form_data(request)
        require_csrf(context, data)
        raw_id = data.get('id', '')
        if not raw_id.isdigit():
            return RedirectResponse('/setup/addons?message=' + quote_plus('Invalid Add-on.'), 303)
        addon_id = int(raw_id)
        with database.connect() as c:
            before = c.execute('SELECT * FROM setup_addons WHERE id=? AND company_id=?', (addon_id, company_id)).fetchone()
            if before is None:
                return RedirectResponse('/setup/addons?message=' + quote_plus('Add-on was not found.'), 303)
            new_value = 0 if int(before['popup'] or 0) else 1
            c.execute('UPDATE setup_addons SET popup=? WHERE id=? AND company_id=?', (new_value, addon_id, company_id))
        audit(
            database, context, company_id, 'ADDON_POPUP_CHANGED', 'addon', addon_id,
            dict(before), {'popup': new_value},
        )
        return RedirectResponse('/setup/addons', 303)

    @app.middleware('http')
    async def addon_popup_html(request, call_next):
        response = await call_next(request)
        if request.url.path != '/setup/addons' or request.method != 'GET' or response.status_code != 200 or 'text/html' not in response.headers.get('content-type', ''):
            return response
        body = b''
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else str(chunk).encode('utf-8')
        text = body.decode('utf-8')
        context = database.session_context(request.cookies.get(COOKIE_NAME))
        if context:
            company_id = context['acting_company_id'] if context['role'] == 'supervisor' else context['company_id']
            if company_id:
                company_id = int(company_id)
                text = text.replace('<th>Pricing method</th><th>Status</th><th>Actions</th>', '<th>Pricing method</th><th>Status</th><th>Popup</th><th>Actions</th>', 1)
                for addon in rows(database, 'SELECT * FROM setup_addons WHERE company_id=? ORDER BY active DESC,name COLLATE NOCASE', (company_id,)):
                    name = esc(addon['name'])
                    method = esc(addon['pricing_method'])
                    status = 'Active' if addon['active'] else 'Inactive'
                    addon_id = int(addon['id'])
                    popup = bool(addon['popup'])
                    old = f'<tr><td>{name}</td><td>{method}</td><td>{status}</td><td>'
                    control = (
                        f'<td><form method="post" action="/setup/addons/popup" style="display:inline">'
                        f'<input type="hidden" name="csrf" value="{esc(context["csrf_token"])}">'
                        f'<input type="hidden" name="id" value="{addon_id}">'
                        f'<button class="secondary" title="Show this Add-on in calendar availability popups">{"✓ Yes" if popup else "No"}</button>'
                        f'</form></td>'
                    )
                    text = text.replace(old, f'<tr><td>{name}</td><td>{method}</td><td>{status}</td>{control}<td>', 1)
                note = '<p class="muted"><strong>Popup:</strong> choose which Add-ons appear in the Availability Calendar mini information popup. The tick/cross shown for each Element is resolved automatically from its Element Type default and any Individual Element override.</p>'
                marker = '<div class="card"><table>'
                text = text.replace(marker, note + marker, 1)
        headers = {k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'content-type'}}
        return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
