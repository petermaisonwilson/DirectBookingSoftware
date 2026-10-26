from __future__ import annotations
from .sql import execute_sql

from urllib.parse import quote_plus

from fastapi import Request
from fastapi.responses import RedirectResponse, Response

from .app import COOKIE_NAME, esc, form_data
from .setup015_core import audit, context_for, require_csrf, rows, working_company
from .webv1_rule_resolver import resolve_element_item_rule


def initialise_addon_popup(database) -> None:
    """Store per-Element popup visibility. Missing rows deliberately mean Show = Yes."""
    with database.connect() as c:
        execute_sql(c, '''CREATE TABLE IF NOT EXISTS setup_element_popup_items (
            company_id INTEGER NOT NULL,
            element_id INTEGER NOT NULL,
            addon_id INTEGER NOT NULL,
            show_popup INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY(company_id, element_id, addon_id)
        )''')


def popup_addons_for_element(database, company_id: int, year: int, element) -> list[dict[str, object]]:
    """Return only Feature / Extra items this Element can actually provide.

    The canonical resolved rule already applies Element Type default followed by
    any Individual Element override. An unticked Type default therefore stays
    hidden unless this Element overrides it to Yes; likewise an Element override
    to No hides an otherwise allowed Type default.
    """
    result: list[dict[str, object]] = []
    element_id = int(element['id'])
    for addon in rows(database, 'SELECT a.*, COALESCE(p.show_popup,1) AS show_popup\n        FROM setup_addons a\n        LEFT JOIN setup_element_popup_items p\n          ON p.company_id=a.company_id AND p.element_id=:q648_0 AND p.addon_id=a.id\n        WHERE a.company_id=:q648_1 AND a.active=1\n        ORDER BY lower(a.name)', (element_id, company_id)):
        if not int(addon['show_popup']):
            continue
        rule = resolve_element_item_rule(database, company_id, year, element, int(addon['id']))
        if not bool(rule['allowed']):
            continue
        result.append({'id': int(addon['id']), 'name': str(addon['name']), 'available': True})
    return result



def popup_addons_for_elements(database, company_id: int, year: int, elements) -> dict[int, list[dict[str, object]]]:
    """Bulk equivalent of popup_addons_for_element, preserving override precedence."""
    element_ids = [int(element['id']) for element in elements]
    result: dict[int, list[dict[str, object]]] = {eid: [] for eid in element_ids}
    if not element_ids:
        return result
    marks = ','.join(f':element_{i}' for i in range(len(element_ids)))
    with database.connect() as c:
        addons = execute_sql(c,
            'SELECT id,name FROM setup_addons WHERE company_id=:q650_0 AND active=1 ORDER BY lower(name)',
            (company_id,),
        ).fetchall()
        hidden = {
            (int(row['element_id']), int(row['addon_id']))
            for row in execute_sql(c,
                f'SELECT element_id,addon_id FROM setup_element_popup_items WHERE company_id=:q658_0 AND element_id IN ({marks}) AND show_popup=0',
                (company_id, *element_ids),
            ).fetchall()
        }
        overrides = {
            (int(row['element_id']), int(row['addon_id'])): str(row['state'] or 'I').upper()
            for row in execute_sql(c,
                f'SELECT element_id,addon_id,state FROM setup_element_addons WHERE company_id=:q659_0 AND year=:q659_1 AND element_id IN ({marks})',
                (company_id, year, *element_ids),
            ).fetchall()
        }
        type_rules = {
            (str(row['element_type']), int(row['addon_id'])): bool(int(row['allowed'] or 0))
            for row in execute_sql(c,
                'SELECT element_type,addon_id,allowed FROM setup_type_addons WHERE company_id=:q656_0 AND year=:q656_1',
                (company_id, year),
            ).fetchall()
        }
    for element in elements:
        eid = int(element['id'])
        element_type = str(element['element_type'])
        for addon in addons:
            aid = int(addon['id'])
            if (eid, aid) in hidden:
                continue
            state = overrides.get((eid, aid), 'I')
            allowed = state == 'Y' or (state != 'N' and type_rules.get((element_type, aid), False))
            if allowed:
                result[eid].append({'id': aid, 'name': str(addon['name']), 'available': True})
    return result


def _remove_element_list(text: str) -> str:
    marker = '<div class="card"><table><thead><tr><th>Name</th><th>Element Type</th>'
    start = text.find(marker)
    if start < 0:
        return text
    end = text.find('</div>', start)
    if end < 0:
        return text
    return text[:start] + text[end + len('</div>'):]


def register_addon_popup_routes(app) -> None:
    database = app.state.database

    @app.post('/setup/elements/popup')
    async def element_popup_toggle(request: Request):
        context = context_for(database, request)
        company_id = int(working_company(context))
        data = await form_data(request); require_csrf(context, data)
        raw_element = data.get('element_id',''); raw_addon = data.get('addon_id','')
        if not raw_element.isdigit() or not raw_addon.isdigit():
            return RedirectResponse('/setup/elements?message=' + quote_plus('Invalid popup item.'), 303)
        element_id, addon_id = int(raw_element), int(raw_addon)
        with database.connect() as c:
            element = execute_sql(c, 'SELECT id FROM setup_elements WHERE id=:q652_0 AND company_id=:q652_1', (element_id, company_id)).fetchone()
            addon = execute_sql(c, 'SELECT id FROM setup_addons WHERE id=:q653_0 AND company_id=:q653_1 AND active=1', (addon_id, company_id)).fetchone()
            if element is None or addon is None:
                return RedirectResponse('/setup/elements?message=' + quote_plus('Element or popup item was not found.'), 303)
            existing = execute_sql(c, 'SELECT show_popup FROM setup_element_popup_items WHERE company_id=:q654_0 AND element_id=:q654_1 AND addon_id=:q654_2', (company_id, element_id, addon_id)).fetchone()
            old_value = int(existing['show_popup']) if existing else 1
            new_value = 0 if old_value else 1
            execute_sql(c, 'INSERT INTO setup_element_popup_items(company_id,element_id,addon_id,show_popup) VALUES (:q649_0,:q649_1,:q649_2,:q649_3)\n                         ON CONFLICT(company_id,element_id,addon_id) DO UPDATE SET show_popup=excluded.show_popup',
                      (company_id, element_id, addon_id, new_value))
        audit(database, context, company_id, 'ELEMENT_POPUP_CHANGED', 'element', element_id,
              {'addon_id': addon_id, 'show_popup': old_value}, {'addon_id': addon_id, 'show_popup': new_value})
        return RedirectResponse(f'/setup/elements?edit={element_id}', 303)

    @app.middleware('http')
    async def element_popup_html(request, call_next):
        response = await call_next(request)
        if request.url.path != '/setup/elements' or request.method != 'GET' or response.status_code != 200 or 'text/html' not in response.headers.get('content-type',''):
            return response
        body = b''
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else str(chunk).encode('utf-8')
        text = body.decode('utf-8')
        context = database.session_context(request.cookies.get(COOKIE_NAME))
        raw_edit = request.query_params.get('edit','')
        if context and raw_edit.isdigit():
            company_id = context['acting_company_id'] if context['role']=='supervisor' else context['company_id']
            if company_id:
                company_id, element_id = int(company_id), int(raw_edit)
                element = rows(database, 'SELECT * FROM setup_elements WHERE company_id=:q651_0 AND id=:q651_1', (company_id, element_id))
                if element:
                    controls = '<div class="card element-popup-settings"><h2>Calendar popup information</h2><p class="muted">Choose which bookable Feature / Extra items may be shown in this Element\'s Availability Calendar popup. Items that resolve to No in Feature / Extra Rules are automatically omitted from the popup, even if Show is Yes here.</p><table><thead><tr><th>Item</th><th>Show when bookable</th></tr></thead><tbody>'
                    settings = {int(r['addon_id']): int(r['show_popup']) for r in rows(database, 'SELECT addon_id,show_popup FROM setup_element_popup_items WHERE company_id=:q657_0 AND element_id=:q657_1', (company_id, element_id))}
                    for addon in rows(database, 'SELECT id,name FROM setup_addons WHERE company_id=:q655_0 AND active=1 ORDER BY lower(name)', (company_id,)):
                        aid = int(addon['id']); shown = bool(settings.get(aid, 1))
                        controls += f'<tr><td>{esc(addon["name"])}</td><td><form method="post" action="/setup/elements/popup" style="display:inline"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="element_id" value="{element_id}"><input type="hidden" name="addon_id" value="{aid}"><button class="secondary">{"✓ Yes" if shown else "No"}</button></form></td></tr>'
                    controls += '</tbody></table></div>'
                    text = _remove_element_list(text)
                    editor_end = text.find('</form></div>')
                    if editor_end >= 0:
                        insert_at = editor_end + len('</form></div>')
                        text = text[:insert_at] + controls + text[insert_at:]
                    else:
                        text = text.replace('</main>', controls + '</main>', 1)
        headers = {k:v for k,v in response.headers.items() if k.lower() not in {'content-length','content-type'}}
        return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
