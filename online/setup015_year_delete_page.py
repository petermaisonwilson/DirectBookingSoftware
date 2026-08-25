from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse

from .app import esc
from .setup015_core import context_for, working_company, years
from .setup015_year_audit import _years_page


def _delete_form(context, year: int) -> str:
    return (
        f'<form method="post" action="/setup/maintenance/years/delete" style="display:inline" '
        f'onsubmit="return confirm(\'Delete {year} and all of its year-specific Setup data?\')">'
        f'<input type="hidden" name="csrf" value="{esc(context["csrf_token"])}">'
        f'<input type="hidden" name="year" value="{year}">'
        '<button class="secondary">Delete year</button></form>'
    )


def register_year_delete_page(app) -> None:
    """Expose the already-proven protected year deletion on the canonical Years page."""
    database = app.state.database

    @app.get('/setup/years', response_class=HTMLResponse)
    def years_with_delete(request: Request, message: str = ''):
        context = context_for(database, request)
        cid = int(working_company(context))
        html = _years_page(database, context, message=message)

        old_header = '<table><thead><tr><th>Year</th><th>Setup Audit</th></tr></thead><tbody>'
        new_header = '<table><thead><tr><th>Year</th><th>Setup Audit</th><th>Maintenance</th></tr></thead><tbody>'
        html = html.replace(old_header, new_header, 1)

        for year in years(database, cid):
            old_row = (
                f'<tr><td><strong>{year}</strong></td>'
                f'<td><a class="button secondary" href="/setup/audit?year={year}">Run Setup Audit</a></td></tr>'
            )
            new_row = (
                f'<tr><td><strong>{year}</strong></td>'
                f'<td><a class="button secondary" href="/setup/audit?year={year}">Run Setup Audit</a></td>'
                f'<td>{_delete_form(context, year)}</td></tr>'
            )
            html = html.replace(old_row, new_row, 1)

        return HTMLResponse(html)
