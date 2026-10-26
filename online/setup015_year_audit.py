from __future__ import annotations
from .sql import execute_sql, insert_sql, table_exists

import math
from datetime import date
from urllib.parse import quote_plus

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, form_data, layout
from .setup014_core import shift_date
from .setup015_catalogue import error_box, field_style, setup_nav
from .setup015_core import audit, context_for, require_csrf, rows, working_company, years


def _table_exists(connection, table: str) -> bool:
    return (True if table_exists(connection, table) else None) is not None


def _adjust(rate, percent: float, round_up: bool):
    if rate is None:
        return None
    value = float(rate) * (1.0 + percent / 100.0)
    if value < 0:
        value = 0.0
    return float(math.ceil(value)) if round_up else round(value, 2)


def _clone_year(database, company_id: int, target_year: int, source_year: int | None, *, copy_prices: bool, percent: float = 0.0, round_up: bool = False) -> None:
    if target_year in years(database, company_id):
        raise ValueError('That pricing year already exists.')
    if source_year is not None and source_year not in years(database, company_id):
        raise ValueError('Choose a valid source year.')
    if percent < -100:
        raise ValueError('Price adjustment cannot be less than -100%.')

    with database.connect() as c:
        execute_sql(c, 'INSERT INTO setup_years(company_id,year,copied_from_year) VALUES (:q728_0,:q728_1,:q728_2)', (company_id, target_year, source_year))
        if source_year is None:
            execute_sql(c, 'INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (:q729_0,:q729_1,:q729_2,:q729_3,:q729_4)', (company_id, target_year, f'All Year {target_year}', f'{target_year}-01-01', f'{target_year}-12-31'))
            return

        season_map: dict[int, int] = {}
        for season in execute_sql(c, 'SELECT * FROM setup_seasons WHERE company_id=:q733_0 AND year=:q733_1 ORDER BY id', (company_id, source_year)).fetchall():
            new_id = int(insert_sql(c,
                'INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (:q739_0,:q739_1,:q739_2,:q739_3,:q739_4)',
                (company_id, target_year, season['name'], shift_date(season['start_date'], target_year), shift_date(season['end_date'], target_year)),
            ).lastrowid)
            season_map[int(season['id'])] = new_id

        for table, cols in (
            ('setup_occupancy', ('element_id', 'max_total')),
            ('setup_person_limits', ('element_id', 'person_type_id', 'max_count')),
        ):
            col_sql = ','.join(cols)
            source_rows = execute_sql(c, f'SELECT {col_sql} FROM {table} WHERE company_id=:q754_0 AND year=:q754_1', (company_id, source_year)).fetchall()
            placeholders = ','.join(f':copy_{i}' for i in range(2 + len(cols)))
            for row in source_rows:
                execute_sql(c, f'INSERT INTO {table}(company_id,year,{col_sql}) VALUES ({placeholders})', (company_id, target_year, *[row[col] for col in cols]))

        for row in execute_sql(c, 'SELECT * FROM setup_type_addons WHERE company_id=:q734_0 AND year=:q734_1', (company_id, source_year)).fetchall():
            rate = _adjust(row['rate'], percent, round_up) if copy_prices else None
            execute_sql(c, 'INSERT INTO setup_type_addons(company_id,year,element_type,addon_id,allowed,min_qty,max_qty,rate) VALUES (:q730_0,:q730_1,:q730_2,:q730_3,:q730_4,:q730_5,:q730_6,:q730_7)',
                      (company_id, target_year, row['element_type'], row['addon_id'], row['allowed'], row['min_qty'], row['max_qty'], rate))
        for row in execute_sql(c, 'SELECT * FROM setup_element_addons WHERE company_id=:q735_0 AND year=:q735_1', (company_id, source_year)).fetchall():
            rate = _adjust(row['rate'], percent, round_up) if copy_prices else None
            execute_sql(c, 'INSERT INTO setup_element_addons(company_id,year,element_id,addon_id,state,min_qty,max_qty,rate) VALUES (:q731_0,:q731_1,:q731_2,:q731_3,:q731_4,:q731_5,:q731_6,:q731_7)',
                      (company_id, target_year, row['element_id'], row['addon_id'], row['state'], row['min_qty'], row['max_qty'], rate))

        if copy_prices:
            for row in execute_sql(c, 'SELECT * FROM setup_element_rates WHERE company_id=:q740_0 AND year=:q740_1', (company_id, source_year)).fetchall():
                mapped = season_map.get(int(row['season_id']))
                if mapped is not None:
                    execute_sql(c, 'INSERT INTO setup_element_rates(company_id,year,element_id,season_id,rate) VALUES (:q741_0,:q741_1,:q741_2,:q741_3,:q741_4)',
                              (company_id, target_year, row['element_id'], mapped, _adjust(row['rate'], percent, round_up)))
            for row in execute_sql(c, 'SELECT * FROM setup_person_prices WHERE company_id=:q742_0 AND year=:q742_1', (company_id, source_year)).fetchall():
                execute_sql(c, 'INSERT INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (:q736_0,:q736_1,:q736_2,:q736_3,:q736_4)',
                          (company_id, target_year, row['element_id'], row['person_type_id'], _adjust(row['rate'], percent, round_up)))
            if _table_exists(c, 'setup_addon_person_rates'):
                for row in execute_sql(c, 'SELECT * FROM setup_addon_person_rates WHERE company_id=:q746_0 AND year=:q746_1', (company_id, source_year)).fetchall():
                    execute_sql(c, 'INSERT INTO setup_addon_person_rates(company_id,addon_id,year,person_type_id,rate) VALUES (:q743_0,:q743_1,:q743_2,:q743_3,:q743_4)',
                              (company_id, row['addon_id'], target_year, row['person_type_id'], _adjust(row['rate'], percent, round_up)))


def _audit_items(database, company_id: int, year: int) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    season_rows = rows(database, 'SELECT * FROM setup_seasons WHERE company_id=:q724_0 AND year=:q724_1 ORDER BY start_date,id', (company_id, year))
    elements = rows(database, 'SELECT * FROM setup_elements WHERE company_id=:q725_0 AND active=1 ORDER BY element_type,lower(name)', (company_id,))
    people = rows(database, 'SELECT * FROM setup_person_types WHERE company_id=:q726_0 AND active=1 ORDER BY lower(name)', (company_id,))
    addons = rows(database, 'SELECT * FROM setup_addons WHERE company_id=:q727_0 AND active=1 ORDER BY lower(name)', (company_id,))

    if not season_rows:
        items.append({'category': 'Seasons', 'text': f'{year} has no season dates.', 'href': f'/setup/pricing?year={year}'})

    with database.connect() as c:
        for element in elements:
            eid = int(element['id'])
            for season in season_rows:
                rate = execute_sql(c, 'SELECT rate FROM setup_element_rates WHERE company_id=:q747_0 AND year=:q747_1 AND element_id=:q747_2 AND season_id=:q747_3', (company_id, year, eid, int(season['id']))).fetchone()
                if rate is None:
                    items.append({'category': 'Element pricing', 'text': f"{element['name']} — {season['name']} price missing", 'href': f'/setup/pricing?year={year}'})

            occupancy = execute_sql(c, 'SELECT max_total FROM setup_occupancy WHERE company_id=:q744_0 AND year=:q744_1 AND element_id=:q744_2', (company_id, year, eid)).fetchone()
            if occupancy is None:
                items.append({'category': 'Occupancy', 'text': f"{element['name']} — total occupancy missing", 'href': f'/setup/occupancy?year={year}'})
            for person in people:
                pid = int(person['id'])
                limit = execute_sql(c, 'SELECT max_count FROM setup_person_limits WHERE company_id=:q748_0 AND year=:q748_1 AND element_id=:q748_2 AND person_type_id=:q748_3', (company_id, year, eid, pid)).fetchone()
                if limit is None:
                    items.append({'category': 'Occupancy', 'text': f"{element['name']} — {person['name']} maximum missing", 'href': f'/setup/occupancy?year={year}'})
                price = execute_sql(c, 'SELECT rate FROM setup_person_prices WHERE company_id=:q749_0 AND year=:q749_1 AND element_id=:q749_2 AND person_type_id=:q749_3', (company_id, year, eid, pid)).fetchone()
                if price is None:
                    items.append({'category': 'Person pricing', 'text': f"{element['name']} — {person['name']} price missing", 'href': f'/setup/occupancy?year={year}'})

        for rule in execute_sql(c, 'SELECT ta.*,a.name AS addon_name FROM setup_type_addons ta JOIN setup_addons a ON a.id=ta.addon_id AND a.company_id=ta.company_id WHERE ta.company_id=:q737_0 AND ta.year=:q737_1 AND a.active=1 AND ta.allowed=1', (company_id, year)).fetchall():
            if rule['rate'] is None:
                items.append({'category': 'Add-on pricing', 'text': f"{rule['element_type']} — {rule['addon_name']} price missing", 'href': f'/setup/addon-rules?year={year}'})
        for rule in execute_sql(c, "SELECT ea.*,se.name AS element_name,a.name AS addon_name FROM setup_element_addons ea JOIN setup_elements se ON se.id=ea.element_id AND se.company_id=ea.company_id JOIN setup_addons a ON a.id=ea.addon_id AND a.company_id=ea.company_id WHERE ea.company_id=:q738_0 AND ea.year=:q738_1 AND ea.state='Y' AND se.active=1 AND a.active=1", (company_id, year)).fetchall():
            if rule['rate'] is None:
                items.append({'category': 'Add-on pricing', 'text': f"{rule['element_name']} — {rule['addon_name']} override price missing", 'href': f'/setup/addon-rules?year={year}'})

        if _table_exists(c, 'setup_addon_person_pricing') and _table_exists(c, 'setup_addon_person_rates'):
            person_mode_addons = execute_sql(c, "SELECT a.id,a.name FROM setup_addons a JOIN setup_addon_person_pricing p ON p.company_id=a.company_id AND p.addon_id=a.id WHERE a.company_id=:q745_0 AND a.active=1 AND p.pricing_mode='person_type'", (company_id,)).fetchall()
            for addon in person_mode_addons:
                aid = int(addon['id'])
                used = execute_sql(c, 'SELECT 1 FROM setup_type_addons WHERE company_id=:q750_0 AND year=:q750_1 AND addon_id=:q750_2 AND allowed=1 LIMIT 1', (company_id, year, aid)).fetchone() or execute_sql(c, "SELECT 1 FROM setup_element_addons WHERE company_id=:q751_0 AND year=:q751_1 AND addon_id=:q751_2 AND state='Y' LIMIT 1", (company_id, year, aid)).fetchone()
                if not used:
                    continue
                for person in people:
                    pid = int(person['id'])
                    rate = execute_sql(c, 'SELECT rate FROM setup_addon_person_rates WHERE company_id=:q752_0 AND addon_id=:q752_1 AND year=:q752_2 AND person_type_id=:q752_3', (company_id, aid, year, pid)).fetchone()
                    if rate is None:
                        items.append({'category': 'Add-on Person pricing', 'text': f"{addon['name']} — {person['name']} price missing", 'href': f'/setup/addons/when?year={year}'})
    return items


def _years_page(database, context, submitted=None, errors=None, message='') -> str:
    cid = int(working_company(context)); submitted = submitted or {}; errors = errors or set(); available = years(database, cid)
    next_year = (max(available) + 1) if available else (date.today().year + 1)
    blank_target = submitted.get('blank_year', str(next_year)); copy_target = submitted.get('copy_year', str(next_year))
    source_default = submitted.get('source_year', str(max(available)) if available else '')
    source_options = ''.join(f'<option value="{y}" {"selected" if str(y)==str(source_default) else ""}>{y}</option>' for y in available)
    blank_source_options = ('<option value="">No previous year — first year</option>' if not available else '') + source_options
    round_checked = 'checked' if submitted.get('round_up', '1') == '1' else ''
    percent = submitted.get('percent', '0')
    body = f'<h1>Pricing years</h1>{setup_nav()}{error_box(message)}'
    body += f'''<div class="card"><h2>Create new blank-pricing year</h2><p>The complete operating structure is carried forward; <strong>only prices are left blank</strong>. The Setup Audit then lists every missing price.</p><form method="post" action="/setup/years/new"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><div class="grid"><div><label>New year</label><input style="{field_style('blank_year' in errors)}" type="number" name="year" value="{esc(blank_target)}"></div><div><label>Carry structure from</label><select name="source_year">{blank_source_options}</select></div></div><p><button>Create blank-pricing year</button></p></form></div>'''
    body += f'''<div class="card"><h2>Copy an existing year</h2><p>Copies setup and prices. You may increase or decrease all copied prices before reviewing them.</p><form method="post" action="/setup/years/copy"><input type="hidden" name="csrf" value="{esc(context['csrf_token'])}"><div class="grid"><div><label>Copy from year</label><select name="source_year">{source_options}</select></div><div><label>New year</label><input style="{field_style('copy_year' in errors)}" type="number" name="year" value="{esc(copy_target)}"></div><div><label>Price adjustment %</label><input style="{field_style('percent' in errors)}" name="percent" value="{esc(percent)}" placeholder="e.g. 4.3 or -5"></div></div><p><label style="font-weight:normal"><input style="width:auto" type="checkbox" name="round_up" value="1" {round_checked}> Round adjusted prices up to the nearest whole number</label></p><p><button>Copy year</button></p></form></div>'''
    body += '<div class="card"><h2>Existing years</h2>'
    if available:
        body += '<table><thead><tr><th>Year</th><th>Setup Audit</th></tr></thead><tbody>' + ''.join(f'<tr><td><strong>{y}</strong></td><td><a class="button secondary" href="/setup/audit?year={y}">Run Setup Audit</a></td></tr>' for y in available) + '</tbody></table>'
    else:
        body += '<p>None yet.</p>'
    body += '</div>'
    return layout('Pricing years', body, context)


def _audit_page(database, context, year: int | None) -> str:
    cid = int(working_company(context)); available = years(database, cid)
    selected = year if year in available else (available[-1] if available else None)
    body = f'<h1>Setup Audit</h1>{setup_nav()}'
    if selected is None:
        return layout('Setup Audit', body + '<div class="error">Create a pricing year first.</div>', context)
    options = ''.join(f'<option value="{y}" {"selected" if y==selected else ""}>{y}</option>' for y in available)
    body += f'<div class="card"><form method="get" action="/setup/audit"><label>Audit year</label><select name="year" onchange="this.form.submit()">{options}</select></form></div>'
    items = _audit_items(database, cid, selected)
    if not items:
        body += f'<div class="ok"><h2 style="margin-bottom:0">{selected}: No missing information</h2></div>'
    else:
        body += f'<div class="error"><strong>{len(items)} missing Setup item(s) for {selected}.</strong><br>Click an item to go directly to the Setup page that contains the missing information.</div><div class="card"><h2>Missing information</h2><table><thead><tr><th>Area</th><th>Missing item</th></tr></thead><tbody>'
        for item in items:
            body += f'<tr><td>{esc(item["category"])}</td><td><a href="{esc(item["href"])}">{esc(item["text"])}</a></td></tr>'
        body += '</tbody></table></div>'
    body += f'<p><a class="button secondary" href="/setup/years">← Pricing years</a> <a class="button" href="/setup/audit?year={selected}">Run audit again</a></p>'
    return layout('Setup Audit', body, context)


def register_year_audit_routes(app) -> None:
    database = app.state.database

    @app.get('/setup/years', response_class=HTMLResponse)
    def years_page(request: Request, message: str = ''):
        return HTMLResponse(_years_page(database, context_for(database, request), message=message))

    @app.get('/setup/audit', response_class=HTMLResponse)
    def setup_audit(request: Request, year: int = 0):
        return HTMLResponse(_audit_page(database, context_for(database, request), year or None))

    @app.post('/setup/years/new')
    async def year_new(request: Request):
        context = context_for(database, request); cid = int(working_company(context)); data = await form_data(request); require_csrf(context, data)
        submitted = {'blank_year': data.get('year', ''), 'source_year': data.get('source_year', '')}
        try:
            target = int(data.get('year', '')); source_raw = data.get('source_year', '').strip(); source = int(source_raw) if source_raw else None
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
            target = int(data.get('year', '')); source = int(data.get('source_year', '')); percent = float(data.get('percent', '0').replace(',', '.'))
        except ValueError:
            return HTMLResponse(_years_page(database, context, submitted, {'copy_year', 'percent'}, 'Enter a valid source year, new year and percentage.'), 400)
        round_up = data.get('round_up') == '1'
        try:
            _clone_year(database, cid, target, source, copy_prices=True, percent=percent, round_up=round_up)
        except ValueError as exc:
            return HTMLResponse(_years_page(database, context, submitted, {'copy_year'}, str(exc)), 400)
        audit(database, context, cid, 'PRICING_YEAR_COPIED_ADJUSTED', 'pricing_year', target, {'source': source}, {'year': target, 'source': source, 'percent': percent, 'round_up': round_up})
        return RedirectResponse(f'/setup/audit?year={target}', 303)
