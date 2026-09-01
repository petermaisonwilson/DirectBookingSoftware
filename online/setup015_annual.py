from __future__ import annotations

from datetime import date

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, form_data, layout
from .setup015_catalogue import setup_nav
from .setup015_core import audit, context_for, one, require_csrf, rows, selected_year, valid_money, valid_whole, working_company, years


def _year_select(available: list[int], selected: int | None, path: str) -> str:
    if not available:
        return '<div class="error">Create a pricing year first.</div>'
    options = ''.join(f'<option value="{y}" {"selected" if y == selected else ""}>{y}</option>' for y in available)
    return f'<form method="get" action="{path}" class="card"><label>Pricing year</label><select name="year" onchange="this.form.submit()">{options}</select></form>'


def _error_html(message: str) -> str:
    return f'<div class="error"><strong>Please correct the highlighted fields.</strong><br>{esc(message)}</div>' if message else ''


def _style(name: str, errors: set[str], width: str = '90px') -> str:
    base = f'min-width:{width}'
    return base + ';border:2px solid #b42318;background:#fff1f0' if name in errors else base


def _pricing_page(database, context, year: int | None, submitted: dict[str, str] | None = None, errors: set[str] | None = None, message: str = '') -> str:
    cid = working_company(context); available = years(database, cid); selected = selected_year(database, cid, year); errors = errors or set(); submitted = submitted or {}
    body = f'<h1>Seasonal Element pricing</h1>{setup_nav()}{_error_html(message)}{_year_select(available, selected, "/setup/pricing")}'
    if selected is None: return layout('Seasonal pricing', body, context)
    seasons = rows(database, 'SELECT * FROM setup_seasons WHERE company_id=? AND year=? ORDER BY start_date', (cid, selected)); elements = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1 ORDER BY element_type,name', (cid,))
    season_name = submitted.get('season_name', '') if submitted else ''; season_start = submitted.get('season_start', '') if submitted else ''; season_end = submitted.get('season_end', '') if submitted else ''
    body += f'''<div class="card"><h2>Add season</h2><form method="post" action="/setup/seasons"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="year" value="{selected}"><div class="grid"><div><label>Name</label><input style="{_style("season_name", errors)}" name="name" value="{esc(season_name)}"></div><div><label>Start</label><input style="{_style("season_start", errors)}" type="date" name="start_date" value="{esc(season_start)}"></div><div><label>End</label><input style="{_style("season_end", errors)}" type="date" name="end_date" value="{esc(season_end)}"></div></div><p><button>Add season</button></p></form></div>'''
    body += f'<form method="post" action="/setup/pricing"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="year" value="{selected}"><div class="card" style="overflow:auto"><p><strong>Every visible price cell must contain a value. 0 or 0.00 is valid.</strong></p><table><thead><tr><th>Element</th>' + ''.join(f'<th>{esc(s["name"])}</th>' for s in seasons) + '</tr></thead><tbody>'
    for element in elements:
        body += f'<tr><td>{esc(element["name"])}</td>'
        for season in seasons:
            key = f'r_{element["id"]}_{season["id"]}'
            if submitted and key in submitted: value = submitted.get(key, '')
            else:
                rate_row = one(database, 'SELECT rate FROM setup_element_rates WHERE company_id=? AND year=? AND element_id=? AND season_id=?', (cid, selected, element['id'], season['id'])); value = '' if rate_row is None else f'{float(rate_row["rate"]):.2f}'
            body += f'<td><input style="{_style(key, errors)}" name="{key}" value="{esc(value)}" placeholder="required"></td>'
        body += '</tr>'
    body += '</tbody></table><p><button>Save seasonal prices</button></p></div></form>'
    return layout('Seasonal pricing', body, context)


def _occupancy_page(database, context, year: int | None, submitted: dict[str, str] | None = None, errors: set[str] | None = None, message: str = '') -> str:
    cid = working_company(context); available = years(database, cid); selected = selected_year(database, cid, year); submitted = submitted or {}; errors = errors or set()
    body = f'<h1>Occupancy & Person pricing</h1>{setup_nav()}{_error_html(message)}{_year_select(available, selected, "/setup/occupancy")}'
    if selected is None: return layout('Occupancy', body, context)
    elements = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1 ORDER BY element_type,name', (cid,)); people = rows(database, 'SELECT * FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name', (cid,))
    body += f'<form method="post" action="/setup/occupancy"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="year" value="{selected}"><div class="card" style="overflow:auto"><p><strong>No blanks:</strong> every Min, Max and € box must contain a value. 0 is valid. Use Min 1 where an Element must contain at least one of that Person Type.</p><table><thead><tr><th rowspan="2">Element</th><th rowspan="2">Total max</th>' + ''.join(f'<th colspan="3" style="text-align:center">{esc(p["name"])}</th>' for p in people) + '</tr><tr>' + ''.join('<th style="text-align:center">Min</th><th style="text-align:center">Max</th><th style="text-align:center">€</th>' for _ in people) + '</tr></thead><tbody>'
    for element in elements:
        total_key = f't_{element["id"]}'
        if submitted: total_value = submitted.get(total_key, '')
        else:
            total = one(database, 'SELECT max_total FROM setup_occupancy WHERE company_id=? AND year=? AND element_id=?', (cid, selected, element['id'])); total_value = '' if total is None else str(total['max_total'])
        body += f'<tr><td>{esc(element["name"])}</td><td><input style="{_style(total_key, errors, "64px")}" name="{total_key}" value="{esc(total_value)}"></td>'
        for person in people:
            min_key = f'pmin_{element["id"]}_{person["id"]}'; max_key = f'p_{element["id"]}_{person["id"]}'; rate_key = f'pr_{element["id"]}_{person["id"]}'
            if submitted:
                min_value = submitted.get(min_key, ''); max_value = submitted.get(max_key, ''); rate_value = submitted.get(rate_key, '')
            else:
                limit = one(database, 'SELECT min_count,max_count FROM setup_person_limits WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?', (cid, selected, element['id'], person['id'])); price = one(database, 'SELECT rate FROM setup_person_prices WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?', (cid, selected, element['id'], person['id']))
                min_value = '' if limit is None else str(limit['min_count']); max_value = '' if limit is None else str(limit['max_count']); rate_value = '' if price is None else f'{float(price["rate"]):.2f}'
            body += f'<td><input style="{_style(min_key, errors, "58px")}" name="{min_key}" value="{esc(min_value)}"></td><td><input style="{_style(max_key, errors, "58px")}" name="{max_key}" value="{esc(max_value)}"></td><td><input style="{_style(rate_key, errors, "70px")}" name="{rate_key}" value="{esc(rate_value)}"></td>'
        body += '</tr>'
    body += '</tbody></table><p><button>Save occupancy & Person prices</button></p></div></form>'
    return layout('Occupancy', body, context)


def _addon_page(database, context, year: int | None, submitted: dict[str, str] | None = None, errors: set[str] | None = None, message: str = '') -> str:
    cid = working_company(context); available = years(database, cid); selected = selected_year(database, cid, year); submitted = submitted or {}; errors = errors or set()
    body = f'<h1>Add-on rules</h1>{setup_nav()}{_error_html(message)}{_year_select(available, selected, "/setup/addon-rules")}'
    if selected is None: return layout('Add-on rules', body, context)
    addons = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND active=1 ORDER BY name', (cid,)); elements = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1 ORDER BY element_type,name', (cid,)); types = sorted({str(e['element_type']) for e in elements}, key=str.casefold)
    body += f'<form method="post" action="/setup/addon-rules"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="year" value="{selected}"><div class="card"><h2>Element Type defaults</h2><p>Tick = Y / available. Unticked = N / unavailable.</p><table><thead><tr><th>Element Type</th><th>Add-on</th><th>Y</th><th>Min</th><th>Max</th><th>Price €</th></tr></thead><tbody>'
    for typ in types:
        for addon in addons:
            base = one(database, 'SELECT * FROM setup_type_addons WHERE company_id=? AND year=? AND element_type=? AND addon_id=?', (cid, selected, typ, addon['id'])); check_key = f'ty_{addon["id"]}_{typ}'; yes = (check_key in submitted) if submitted else bool(base and base['allowed']); chk = 'checked' if yes else ''; vals = []
            for prefix, col in (('tymin', 'min_qty'), ('tymax', 'max_qty'), ('tyrate', 'rate')):
                key = f'{prefix}_{addon["id"]}_{typ}'
                if submitted: value = submitted.get(key, '')
                else: value = '' if not base or base[col] is None else (f'{float(base[col]):.2f}' if col == 'rate' else str(base[col]))
                vals.append((key, value))
            body += f'<tr><td>{esc(typ)}</td><td>{esc(addon["name"])}</td><td><input style="width:auto" type="checkbox" name="{esc(check_key)}" {chk}></td>' + ''.join(f'<td><input style="{_style(k, errors)}" name="{esc(k)}" value="{esc(v)}"></td>' for k, v in vals) + '</tr>'
    body += '</tbody></table></div><div class="card"><h2>Individual Element overrides</h2><p><strong>I = Inherit Element Type rule &nbsp; Y = Yes &nbsp; N = No.</strong></p><table><thead><tr><th>Element</th><th>Add-on</th><th>I / Y / N</th><th>Min</th><th>Max</th><th>Price €</th></tr></thead><tbody>'
    for element in elements:
        for addon in addons:
            base = one(database, 'SELECT * FROM setup_element_addons WHERE company_id=? AND year=? AND element_id=? AND addon_id=?', (cid, selected, element['id'], addon['id'])); radio_key = f'ov_{element["id"]}_{addon["id"]}'; state = submitted.get(radio_key, 'I') if submitted else ('I' if not base else base['state']); radios = ' '.join(f'<label style="display:inline;font-weight:normal"><input style="width:auto" type="radio" name="{radio_key}" value="{s}" {"checked" if state == s else ""}> {s}</label>' for s in ('I', 'Y', 'N')); vals = []
            for prefix, col in (('ovmin', 'min_qty'), ('ovmax', 'max_qty'), ('ovrate', 'rate')):
                key = f'{prefix}_{element["id"]}_{addon["id"]}'
                if submitted: value = submitted.get(key, '')
                else: value = '' if not base or base[col] is None else (f'{float(base[col]):.2f}' if col == 'rate' else str(base[col]))
                vals.append((key, value))
            body += f'<tr><td>{esc(element["name"])}</td><td>{esc(addon["name"])}</td><td>{radios}</td>' + ''.join(f'<td><input style="{_style(k, errors)}" name="{k}" value="{esc(v)}"></td>' for k, v in vals) + '</tr>'
    body += '</tbody></table><p><button>Save Add-on rules</button></p></div></form>'
    return layout('Add-on rules', body, context)


def register_annual_routes(app) -> None:
    database = app.state.database

    @app.get('/setup/pricing', response_class=HTMLResponse)
    def pricing_page(request: Request, year: str = ''):
        context = context_for(database, request); return _pricing_page(database, context, selected_year(database, working_company(context), year))

    @app.post('/setup/seasons')
    async def season_save(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data)
        try: year = int(data.get('year', ''))
        except ValueError: return HTMLResponse(_pricing_page(database, context, None, message='Select a valid pricing year.'), 400)
        name = data.get('name', '').strip(); start_raw = data.get('start_date', ''); end_raw = data.get('end_date', ''); errors = set()
        if not name: errors.add('season_name')
        try: start = date.fromisoformat(start_raw)
        except ValueError: start = None; errors.add('season_start')
        try: end = date.fromisoformat(end_raw)
        except ValueError: end = None; errors.add('season_end')
        if start and end and end < start: errors.update({'season_start', 'season_end'})
        if errors:
            submitted = {'season_name': name, 'season_start': start_raw, 'season_end': end_raw}; return HTMLResponse(_pricing_page(database, context, year, submitted, errors, 'Complete the highlighted season fields. The end date cannot be before the start date.'), 400)
        with database.connect() as c:
            try: sid = c.execute('INSERT INTO setup_seasons(company_id,year,name,start_date,end_date) VALUES (?,?,?,?,?)', (cid, year, name, start_raw, end_raw)).lastrowid
            except Exception:
                submitted = {'season_name': name, 'season_start': start_raw, 'season_end': end_raw}; return HTMLResponse(_pricing_page(database, context, year, submitted, {'season_name'}, 'That season name already exists for this year.'), 400)
        audit(database, context, cid, 'SEASON_ADDED', 'season', sid, None, {'year': year, 'name': name}); return RedirectResponse(f'/setup/pricing?year={year}', 303)

    @app.post('/setup/pricing')
    async def pricing_save(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data); year = int(data['year']); seasons = rows(database, 'SELECT id FROM setup_seasons WHERE company_id=? AND year=?', (cid, year)); elements = rows(database, 'SELECT id FROM setup_elements WHERE company_id=? AND active=1', (cid,)); values = []; errors = set()
        for element in elements:
            for season in seasons:
                key = f'r_{element["id"]}_{season["id"]}'; raw = data.get(key, '').strip()
                if raw == '': errors.add(key); continue
                try: rate = valid_money(raw)
                except (TypeError, ValueError): errors.add(key); continue
                values.append((element['id'], season['id'], rate))
        if errors: return HTMLResponse(_pricing_page(database, context, year, data, errors, 'Every seasonal price cell must be completed with a valid zero or positive price. Zero is valid.'), 400)
        with database.connect() as c:
            for eid, sid, rate in values: c.execute('INSERT OR REPLACE INTO setup_element_rates VALUES (?,?,?,?,?)', (cid, year, eid, sid, rate))
        audit(database, context, cid, 'SEASONAL_PRICING_SAVED', 'pricing_year', year, None, {'cells': len(values)}); return RedirectResponse(f'/setup/pricing?year={year}', 303)

    @app.get('/setup/occupancy', response_class=HTMLResponse)
    def occupancy_page(request: Request, year: str = ''):
        context = context_for(database, request); return _occupancy_page(database, context, selected_year(database, working_company(context), year))

    @app.post('/setup/occupancy')
    async def occupancy_save(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data); year = int(data['year']); elements = rows(database, 'SELECT id FROM setup_elements WHERE company_id=? AND active=1', (cid,)); people = rows(database, 'SELECT id FROM setup_person_types WHERE company_id=? AND active=1', (cid,)); totals = []; limits = []; prices = []; errors = set()
        for element in elements:
            total_key = f't_{element["id"]}'
            try: totals.append((element['id'], valid_whole(data.get(total_key, ''))))
            except (TypeError, ValueError): errors.add(total_key)
            for person in people:
                min_key = f'pmin_{element["id"]}_{person["id"]}'; max_key = f'p_{element["id"]}_{person["id"]}'; rate_key = f'pr_{element["id"]}_{person["id"]}'
                try: minimum = valid_whole(data.get(min_key, '')); maximum = valid_whole(data.get(max_key, ''))
                except (TypeError, ValueError): errors.update({min_key, max_key}); continue
                if minimum > maximum: errors.update({min_key, max_key}); continue
                limits.append((element['id'], person['id'], minimum, maximum))
                try: prices.append((element['id'], person['id'], valid_money(data.get(rate_key, ''))))
                except (TypeError, ValueError): errors.add(rate_key)
        if errors: return HTMLResponse(_occupancy_page(database, context, year, data, errors, 'Every Total Max, Person Min, Person Max and Person € box must contain a valid zero or positive value. Minimum cannot be greater than maximum.'), 400)
        with database.connect() as c:
            for eid, total in totals: c.execute('INSERT OR REPLACE INTO setup_occupancy VALUES (?,?,?,?)', (cid, year, eid, total))
            for eid, pid, minimum, maximum in limits: c.execute('INSERT OR REPLACE INTO setup_person_limits(company_id,year,element_id,person_type_id,max_count,min_count) VALUES (?,?,?,?,?,?)', (cid, year, eid, pid, maximum, minimum))
            for eid, pid, rate in prices: c.execute('INSERT OR REPLACE INTO setup_person_prices(company_id,year,element_id,person_type_id,rate) VALUES (?,?,?,?,?)', (cid, year, eid, pid, rate))
        audit(database, context, cid, 'OCCUPANCY_SAVED', 'pricing_year', year, None, {'elements': len(elements), 'person_prices': len(prices)}); return RedirectResponse(f'/setup/occupancy?year={year}', 303)

    @app.get('/setup/addon-rules', response_class=HTMLResponse)
    def addon_rules_page(request: Request, year: str = ''):
        context = context_for(database, request); return _addon_page(database, context, selected_year(database, working_company(context), year))

    @app.post('/setup/addon-rules')
    async def addon_rules_save(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data); year = int(data['year']); addons = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND active=1', (cid,)); elements = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1', (cid,)); types = sorted({str(e['element_type']) for e in elements}, key=str.casefold); type_values = []; override_values = []; errors = set()
        for typ in types:
            for addon in addons:
                allowed = 1 if f'ty_{addon["id"]}_{typ}' in data else 0
                if allowed:
                    keys = (f'tymin_{addon["id"]}_{typ}', f'tymax_{addon["id"]}_{typ}', f'tyrate_{addon["id"]}_{typ}')
                    try: mn = valid_whole(data.get(keys[0], '')); mx = valid_whole(data.get(keys[1], '')); rate = valid_money(data.get(keys[2], ''))
                    except (TypeError, ValueError): errors.update(keys); continue
                    if mx < mn: errors.update(keys[:2]); continue
                else: mn = mx = rate = None
                type_values.append((typ, addon['id'], allowed, mn, mx, rate))
        for element in elements:
            for addon in addons:
                state = data.get(f'ov_{element["id"]}_{addon["id"]}', 'I')
                if state == 'I': override_values.append((element['id'], addon['id'], state, None, None, None)); continue
                if state == 'Y':
                    keys = (f'ovmin_{element["id"]}_{addon["id"]}', f'ovmax_{element["id"]}_{addon["id"]}', f'ovrate_{element["id"]}_{addon["id"]}')
                    try: mn = valid_whole(data.get(keys[0], '')); mx = valid_whole(data.get(keys[1], '')); rate = valid_money(data.get(keys[2], ''))
                    except (TypeError, ValueError): errors.update(keys); continue
                    if mx < mn: errors.update(keys[:2]); continue
                else: mn = mx = rate = None
                override_values.append((element['id'], addon['id'], state, mn, mx, rate))
        if errors: return HTMLResponse(_addon_page(database, context, year, data, errors, 'Complete all values required by Y rules. Minimum and maximum must be whole numbers, prices must be zero or positive, and maximum cannot be less than minimum.'), 400)
        with database.connect() as c:
            for typ, aid, allowed, mn, mx, rate in type_values: c.execute('INSERT OR REPLACE INTO setup_type_addons VALUES (?,?,?,?,?,?,?,?)', (cid, year, typ, aid, allowed, mn, mx, rate))
            for eid, aid, state, mn, mx, rate in override_values:
                if state == 'I': c.execute('DELETE FROM setup_element_addons WHERE company_id=? AND year=? AND element_id=? AND addon_id=?', (cid, year, eid, aid))
                else: c.execute('INSERT OR REPLACE INTO setup_element_addons VALUES (?,?,?,?,?,?,?,?)', (cid, year, eid, aid, state, mn, mx, rate))
        audit(database, context, cid, 'ADDON_RULES_SAVED', 'pricing_year', year, None, {'element_types': len(types), 'elements': len(elements), 'addons': len(addons)}); return RedirectResponse(f'/setup/addon-rules?year={year}', 303)