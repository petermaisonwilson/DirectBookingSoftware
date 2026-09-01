from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import form_data
from .setup015_core import audit, context_for, require_csrf, working_company, years
from .setup015_year_audit import _clone_year, _years_page


def _fallback_source(database, company_id: int, target: int) -> int | None:
    candidates = [y for y in years(database, company_id) if y != target]
    return max(candidates) if candidates else None


def register_year_action_routes(app) -> None:
    database = app.state.database

    @app.post('/setup/years/new')
    async def year_new(request: Request):
        context = context_for(database, request); cid = int(working_company(context)); data = await form_data(request); require_csrf(context, data)
        submitted = {'blank_year': data.get('year', ''), 'source_year': data.get('source_year', '')}
        try:
            target = int(data.get('year', ''))
            raw_source = data.get('source_year', '').strip()
            source = int(raw_source) if raw_source else _fallback_source(database, cid, target)
        except ValueError:
            return HTMLResponse(_years_page(database, context, submitted, {'blank_year'}, 'Enter a valid new year and source year.'), 400)
        try:
            _clone_year(database, cid, target, source, copy_prices=False)
        except ValueError as exc:
            return HTMLResponse(_years_page(database, context, submitted, {'blank_year'}, str(exc)), 400)
        audit(database, context, cid, 'PRICING_YEAR_CREATED_BLANK', 'pricing_year', target, None, {'year': target, 'structure_from': source, 'prices_copied': False})
        return RedirectResponse(f'/setup/audit?year={target}', 303)

    @app.post('/setup/years/copy')
    async def year_copy(request: Request):
        context = context_for(database, request); cid = int(working_company(context)); data = await form_data(request); require_csrf(context, data)
        submitted = {'copy_year': data.get('year', ''), 'source_year': data.get('source_year', ''), 'percent': data.get('percent', '0'), 'round_up': '1' if data.get('round_up') == '1' else '0'}
        try:
            target = int(data.get('year', ''))
            raw_source = data.get('source_year', '').strip()
            source = int(raw_source) if raw_source else _fallback_source(database, cid, target)
            if source is None:
                raise ValueError
            percent = float(data.get('percent', '0').replace(',', '.'))
        except ValueError:
            return HTMLResponse(_years_page(database, context, submitted, {'copy_year', 'percent'}, 'Enter a valid source year, new year and percentage.'), 400)
        round_up = data.get('round_up') == '1'
        try:
            _clone_year(database, cid, target, source, copy_prices=True, percent=percent, round_up=round_up)
        except ValueError as exc:
            return HTMLResponse(_years_page(database, context, submitted, {'copy_year'}, str(exc)), 400)
        audit(database, context, cid, 'PRICING_YEAR_COPIED_ADJUSTED', 'pricing_year', target, {'source': source}, {'year': target, 'source': source, 'percent': percent, 'round_up': round_up})
        return RedirectResponse(f'/setup/audit?year={target}', 303)
