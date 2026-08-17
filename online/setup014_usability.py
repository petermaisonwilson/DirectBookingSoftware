from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, form_data
from .setup014 import (
    _audit,
    _context,
    _layout,
    _one,
    _rows,
    _selected_year,
    _setup_nav,
    _working_company,
    _year_select,
    _years,
    _csrf,
    _valid_money,
)


def apply_setup014_usability(app) -> None:
    """Build 014 usability repair.

    Replace only the Seasonal Pricing GET/POST routes so validation failures
    remain on the normal pricing page instead of escaping as raw JSON errors.
    """
    database = app.state.database

    kept = []
    for route in app.router.routes:
        if getattr(route, "path", None) == "/setup/pricing" and ({"GET", "POST"} & set(getattr(route, "methods", set()))):
            continue
        kept.append(route)
    app.router.routes[:] = kept

    def render_pricing(context, company_id: int, selected: int | None, submitted: dict[str, str] | None = None, error: str = "") -> str:
        years = _years(database, company_id)
        body = f'<h1>Seasonal Element pricing</h1>{_setup_nav()}{_year_select(years,selected,"/setup/pricing")}'
        if error:
            body += f'<div class="error"><strong>Please correct the highlighted cells.</strong><br>{esc(error)}</div>'
        if selected is None:
            return _layout("Seasonal pricing", body, context)

        seasons = _rows(database, "SELECT * FROM setup_seasons WHERE company_id=? AND year=? ORDER BY start_date", (company_id, selected))
        elements = _rows(database, "SELECT * FROM setup_elements WHERE company_id=? AND active=1 ORDER BY element_type,name", (company_id,))
        body += f'<div class="card"><h2>Add season</h2><form method="post" action="/setup/seasons"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="year" value="{selected}"><div class="grid"><div><label>Name</label><input name="name" required></div><div><label>Start</label><input type="date" name="start_date" required></div><div><label>End</label><input type="date" name="end_date" required></div></div><p><button>Add season</button></p></form></div>'
        body += f'<form method="post" action="/setup/pricing"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="year" value="{selected}"><div class="card" style="overflow:auto"><p><strong>Every cell must be completed.</strong> A deliberate <strong>0.00</strong> is valid.</p><table><thead><tr><th>Element</th>' + ''.join(f'<th>{esc(s["name"])}</th>' for s in seasons) + '</tr></thead><tbody>'

        for element in elements:
            body += f'<tr><td>{esc(element["name"])}</td>'
            for season in seasons:
                key = f'r_{element["id"]}_{season["id"]}'
                if submitted is not None:
                    value = submitted.get(key, "")
                else:
                    row = _one(database, "SELECT rate FROM setup_element_rates WHERE company_id=? AND year=? AND element_id=? AND season_id=?", (company_id, selected, element["id"], season["id"]))
                    value = "" if row is None else f'{float(row["rate"]):.2f}'
                invalid = submitted is not None and key in submitted.get("__invalid_keys__", "").split(",")
                style = "min-width:90px;background:#fde8e8;border:2px solid #c94b4b" if invalid else "min-width:90px"
                body += f'<td><input style="{style}" name="{key}" value="{esc(value)}" placeholder="required"></td>'
            body += '</tr>'
        body += '</tbody></table><p><button>Save seasonal prices</button></p></div></form>'
        return _layout("Seasonal pricing", body, context)

    @app.get("/setup/pricing", response_class=HTMLResponse)
    def pricing_page(request: Request, year: str = ""):
        context = _context(database, request)
        company_id = _working_company(context)
        selected = _selected_year(database, company_id, year)
        return render_pricing(context, company_id, selected)

    @app.post("/setup/pricing", response_class=HTMLResponse)
    async def pricing_save(request: Request):
        context = _context(database, request)
        company_id = _working_company(context)
        data = await form_data(request)
        _csrf(context, data)
        year = int(data["year"])
        seasons = _rows(database, "SELECT id FROM setup_seasons WHERE company_id=? AND year=?", (company_id, year))
        elements = _rows(database, "SELECT id FROM setup_elements WHERE company_id=? AND active=1", (company_id,))

        values = []
        invalid_keys: list[str] = []
        for element in elements:
            for season in seasons:
                key = f'r_{element["id"]}_{season["id"]}'
                raw = data.get(key, "").strip()
                if raw == "":
                    invalid_keys.append(key)
                    continue
                try:
                    rate = _valid_money(raw)
                except (TypeError, ValueError):
                    invalid_keys.append(key)
                    continue
                values.append((element["id"], season["id"], rate))

        if invalid_keys:
            submitted = dict(data)
            submitted["__invalid_keys__"] = ",".join(invalid_keys)
            message = "Every seasonal price cell must contain a valid zero or positive price. Zero is valid."
            return HTMLResponse(render_pricing(context, company_id, year, submitted, message), status_code=200)

        with database.connect() as connection:
            for element_id, season_id, rate in values:
                connection.execute(
                    "INSERT OR REPLACE INTO setup_element_rates VALUES (?,?,?,?,?)",
                    (company_id, year, element_id, season_id, rate),
                )
        _audit(database, context, company_id, "SEASONAL_PRICING_SAVED", "pricing_year", year, None, {"cells": len(values)})
        return RedirectResponse(f"/setup/pricing?year={year}", status_code=303)
