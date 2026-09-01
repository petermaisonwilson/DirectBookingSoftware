from __future__ import annotations

from fastapi.responses import Response

from .app import COOKIE_NAME, esc
from .setup015_core import rows


def install_booking_requirements_ui(app) -> None:
    """Setup-page controls for requirement questions.

    Availability suitability now belongs to the calendar renderer itself. Keeping
    that decision in the calendar avoids response rewriting and lets held Elements
    represent the combined state HELD BY YOU + NOW UNSUITABLE correctly.
    """
    database = app.state.database

    @app.middleware('http')
    async def booking_requirements_ui(request, call_next):
        response = await call_next(request)
        if response.status_code >= 400 or 'text/html' not in response.headers.get('content-type', ''):
            return response
        path = request.url.path
        if path not in {'/setup/person-types', '/setup/addons'}:
            return response

        body = b''
        async for chunk in response.body_iterator:
            body += chunk if isinstance(chunk, bytes) else str(chunk).encode('utf-8')
        text = body.decode('utf-8')
        headers = {k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'content-type'}}
        context = database.session_context(request.cookies.get(COOKIE_NAME))
        if not context:
            return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
        cid = context['acting_company_id'] if context['role'] == 'supervisor' else context['company_id']
        if not cid:
            return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')
        cid = int(cid)

        if path == '/setup/person-types':
            controls = '<div class="card"><h2>Age question</h2><p class="muted">Privacy-by-design: ask for age only where it is genuinely needed. Date of birth is not collected.</p><table><thead><tr><th>Person Type</th><th>Ask for age at arrival</th></tr></thead><tbody>'
            for p in rows(database, 'SELECT id,name,ask_age FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name', (cid,)):
                controls += f'<tr><td>{esc(p["name"])}</td><td><form method="post" action="/setup/person-types/age-toggle"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="person_type_id" value="{int(p["id"])}"><button class="secondary">{"✓ Yes" if int(p["ask_age"] or 0) else "No"}</button></form></td></tr>'
            controls += '</tbody></table></div>'
            text = text.replace('<div class="card"><table>', controls + '<div class="card"><table>', 1)
        else:
            controls = '<div class="card"><h2>Ask before Availability</h2><p class="muted">Use this only for requirements that can make an Element unsuitable.</p><table><thead><tr><th>Add-on</th><th>Ask before Availability</th></tr></thead><tbody>'
            for a in rows(database, 'SELECT id,name,ask_before_availability FROM setup_addons WHERE company_id=? AND active=1 ORDER BY name', (cid,)):
                controls += f'<tr><td>{esc(a["name"])}</td><td><form method="post" action="/setup/addons/requirement-toggle"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="addon_id" value="{int(a["id"])}"><button class="secondary">{"✓ Yes" if int(a["ask_before_availability"] or 0) else "No"}</button></form></td></tr>'
            controls += '</tbody></table></div>'
            text = text.replace('<div class="card"><table>', controls + '<div class="card"><table>', 1)

        return Response(content=text, status_code=response.status_code, headers=headers, media_type='text/html')