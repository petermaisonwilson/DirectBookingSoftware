from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import Request
from fastapi.responses import HTMLResponse

from .app import esc, form_data, layout
from .setup015_catalogue import error_box, field_style, setup_nav
from .setup015_core import audit, context_for, one, require_csrf, rows, valid_whole, working_company


def _season_for_date(database, company_id: int, year: int, day: date):
    candidates = rows(database, 'SELECT * FROM setup_seasons WHERE company_id=? AND year=? AND start_date<=? AND end_date>=?', (company_id, year, day.isoformat(), day.isoformat()))
    if not candidates: return None
    return min(candidates, key=lambda r: (date.fromisoformat(r['end_date']) - date.fromisoformat(r['start_date'])).days)


def _element_rate_for_date(database, company_id: int, element_id: int, day: date):
    season = _season_for_date(database, company_id, day.year, day)
    if not season: return None, None
    rate = one(database, 'SELECT rate FROM setup_element_rates WHERE company_id=? AND year=? AND element_id=? AND season_id=?', (company_id, day.year, element_id, season['id']))
    return season, (None if rate is None else float(rate['rate']))


def _addon_rule(database, company_id: int, year: int, element, addon_id: int):
    override = one(database, 'SELECT * FROM setup_element_addons WHERE company_id=? AND year=? AND element_id=? AND addon_id=?', (company_id, year, element['id'], addon_id))
    if override:
        if override['state'] == 'N': return {'allowed': False, 'source': 'Element override N', 'min': None, 'max': None, 'rate': None}
        return {'allowed': True, 'source': 'Element override Y', 'min': int(override['min_qty']), 'max': int(override['max_qty']), 'rate': float(override['rate'])}
    type_rule = one(database, 'SELECT * FROM setup_type_addons WHERE company_id=? AND year=? AND element_type=? AND addon_id=?', (company_id, year, element['element_type'], addon_id))
    if not type_rule or not type_rule['allowed']: return {'allowed': False, 'source': 'Element Type default N', 'min': None, 'max': None, 'rate': None}
    return {'allowed': True, 'source': 'Element Type default Y', 'min': int(type_rule['min_qty']), 'max': int(type_rule['max_qty']), 'rate': float(type_rule['rate'])}


def _price_element(method: str, nightly_rates: list[float], people_total: int) -> float:
    if method in {'Per night', 'Per day'}: return sum(nightly_rates)
    if method in {'Per stay', 'Per package'}: return nightly_rates[0]
    if method == 'Per person': return nightly_rates[0] * people_total
    if method == 'Per person per night': return sum(nightly_rates) * people_total
    raise ValueError('Unsupported Element pricing method')


def _price_person(method: str, rate: float, qty: int, nights: int) -> float:
    if method in {'Per night', 'Per day', 'Per person per night'}: return rate * qty * nights
    return rate * qty


def _price_addon(method: str, rate: float, qty: int, nights: int) -> float:
    if method == 'Fixed once': return rate
    if method == 'Per quantity': return rate * qty
    if method in {'Per night', 'Per day'}: return rate * nights
    if method in {'Per quantity per night', 'Per quantity per day'}: return rate * qty * nights
    raise ValueError('Unsupported Add-on pricing method')


def _page(database, context, element_id: int = 0, submitted=None, errors=None, message='', result=None):
    cid = working_company(context); submitted = submitted or {}; errors = errors or set()
    elements = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1 ORDER BY element_type,name', (cid,)); selected = next((e for e in elements if int(e['id']) == int(element_id or 0)), None)
    options = '<option value="">-- choose Element --</option>' + ''.join(f'<option value="{e["id"]}" {"selected" if selected and e["id"] == selected["id"] else ""}>{esc(e["element_type"])} — {esc(e["name"])}</option>' for e in elements)
    body = f'<h1>Price / Rules test</h1>{setup_nav()}{error_box(message)}<div class="card"><p>This is a test calculator, not yet a Booking. It proves that Setup rules drive occupancy, Person charges, Add-ons and price.</p><p><strong>Season rule:</strong> if seasons overlap, the narrowest matching season wins.</p></div><form method="get" action="/setup/price-test" class="card"><label>Element</label><select name="element" onchange="this.form.submit()">{options}</select></form>'
    if not selected:
        return layout('Price / Rules test', body + '<div class="card">Choose an Element to load its rules.</div>', context)

    people = rows(database, 'SELECT * FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name', (cid,)); addons = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND active=1 ORDER BY name', (cid,)); year_rows = rows(database, 'SELECT year FROM setup_years WHERE company_id=? ORDER BY year', (cid,))
    start_value = submitted.get('start_date', ''); end_value = submitted.get('end_date', ''); pricing_year = None
    try:
        if start_value: pricing_year = date.fromisoformat(start_value).year
    except ValueError: pass

    rule_map = {}
    for yr in year_rows:
        y = int(yr['year']); rule_map[str(y)] = {}
        for addon in addons:
            rule = _addon_rule(database, cid, y, selected, addon['id'])
            rule_map[str(y)][str(addon['id'])] = {'allowed': bool(rule['allowed']), 'max': rule['max'] if rule['allowed'] else None}

    body += f'<form method="post" action="/setup/price-test"><input type="hidden" name="csrf" value="{esc(context["csrf_token"])}"><input type="hidden" name="element_id" value="{selected["id"]}"><div class="card"><h2>{esc(selected["name"])} — {esc(selected["element_type"])}</h2><div class="grid"><div><label>Arrival</label><input id="price-test-arrival" style="{field_style("start_date" in errors)}" type="date" name="start_date" value="{esc(start_value)}"></div><div><label>Departure</label><input style="{field_style("end_date" in errors)}" type="date" name="end_date" value="{esc(end_value)}"></div></div></div>'
    body += '<div class="card"><h2>People</h2><div class="grid">'
    for person in people:
        key = f'person_{person["id"]}'; body += f'<div><label>{esc(person["name"])}</label><input style="{field_style(key in errors)}" type="number" min="0" name="{key}" value="{esc(submitted.get(key, "0"))}"></div>'
    body += '</div></div><div class="card"><h2>Add-ons</h2><p class="muted">Arrival date controls which annual Add-on rule applies. Quantity is capped at the configured maximum.</p><table><thead><tr><th>Add-on</th><th>Rule</th><th>Quantity</th></tr></thead><tbody>'
    for addon in addons:
        rule = _addon_rule(database, cid, pricing_year, selected, addon['id']) if pricing_year else None; key = f'addon_{addon["id"]}'; current = submitted.get(key, '0')
        if rule and rule['allowed']:
            detail = f'{rule["source"]}; min {rule["min"]}, max {rule["max"]}, €{rule["rate"]:.2f} — {addon["pricing_method"]}'; input_html = f'<input data-addon-id="{addon["id"]}" style="{field_style(key in errors)}" type="number" min="0" max="{rule["max"]}" name="{key}" value="{esc(current)}">'
        elif rule:
            detail = rule['source']; input_html = f'<input data-addon-id="{addon["id"]}" type="number" min="0" name="{key}" value="0" disabled>'
        else:
            detail = 'Choose an Arrival date to load the rule'; input_html = f'<input data-addon-id="{addon["id"]}" style="{field_style(key in errors)}" type="number" min="0" name="{key}" value="{esc(current)}" disabled>'
        body += f'<tr><td>{esc(addon["name"])}</td><td>{esc(detail)}</td><td>{input_html}</td></tr>'
    body += '</tbody></table><p><button>Calculate test price</button></p></div></form>'
    rules_json = json.dumps(rule_map, separators=(',', ':'))
    body += f'''<script id="addon-rule-data" type="application/json">{rules_json}</script><script>
(function(){{
 const rules=JSON.parse(document.getElementById('addon-rule-data').textContent);
 const arrival=document.getElementById('price-test-arrival');
 function applyLimits(){{
   const year=arrival.value ? arrival.value.slice(0,4) : '';
   document.querySelectorAll('input[data-addon-id]').forEach(function(input){{
     const rule=(rules[year]||{{}})[input.dataset.addonId];
     if(rule && rule.allowed){{
       input.disabled=false; input.min='0'; input.max=String(rule.max);
       const n=Number(input.value||0); if(n>rule.max) input.value=String(rule.max); if(n<0) input.value='0';
     }} else {{ input.value='0'; input.disabled=true; input.removeAttribute('max'); }}
   }});
 }}
 arrival.addEventListener('change',applyLimits);
 document.querySelectorAll('input[data-addon-id]').forEach(function(input){{input.addEventListener('input',function(){{if(this.max!=='' && Number(this.value)>Number(this.max)) this.value=this.max; if(Number(this.value)<0) this.value='0';}});}});
 applyLimits();
}})();
</script>'''

    if result:
        body += '<div class="card"><h2>Price breakdown</h2><table><thead><tr><th>Item</th><th>Rule used</th><th>Amount</th></tr></thead><tbody>'
        for line in result['lines']: body += f'<tr><td>{esc(line["item"])}</td><td>{esc(line["rule"])}</td><td>€{line["amount"]:.2f}</td></tr>'
        body += f'</tbody></table><h2>Total: €{result["total"]:.2f}</h2><p>{result["nights"]} night(s), {result["people"]} person(s).</p></div>'
    return layout('Price / Rules test', body, context)


def register_calculator_routes(app) -> None:
    database = app.state.database

    @app.get('/setup/price-test', response_class=HTMLResponse)
    def price_test(request: Request, element: int = 0):
        context = context_for(database, request); return _page(database, context, element_id=element)

    @app.post('/setup/price-test', response_class=HTMLResponse)
    async def price_test_calculate(request: Request):
        context = context_for(database, request); cid = working_company(context); data = await form_data(request); require_csrf(context, data); errors = set()
        try: element_id = int(data.get('element_id', ''))
        except ValueError: element_id = 0
        element = one(database, 'SELECT * FROM setup_elements WHERE company_id=? AND id=? AND active=1', (cid, element_id))
        if not element: return HTMLResponse(_page(database, context, message='Choose a valid Element.'), 400)
        try: start = date.fromisoformat(data.get('start_date', ''))
        except ValueError: start = None; errors.add('start_date')
        try: end = date.fromisoformat(data.get('end_date', ''))
        except ValueError: end = None; errors.add('end_date')
        if start and end and end <= start: errors.update({'start_date', 'end_date'})
        if start and end and start.year != (end - timedelta(days=1)).year:
            errors.update({'start_date', 'end_date'}); message = 'Build 015 test calculator currently requires the stay to remain within one pricing year.'
        else: message = 'Complete every highlighted field with a valid value.'

        people_rows = rows(database, 'SELECT * FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name', (cid,)); people_counts = {}; people_total = 0
        for person in people_rows:
            key = f'person_{person["id"]}'
            try: qty = valid_whole(data.get(key, '0'))
            except (TypeError, ValueError): qty = 0; errors.add(key)
            people_counts[int(person['id'])] = qty; people_total += qty
        if people_total <= 0: errors.update(f'person_{p["id"]}' for p in people_rows); message = 'Enter at least one person, and correct every highlighted field.'
        if errors: return HTMLResponse(_page(database, context, element_id, data, errors, message), 400)

        year = start.year; nights = (end - start).days; occupancy = one(database, 'SELECT max_total FROM setup_occupancy WHERE company_id=? AND year=? AND element_id=?', (cid, year, element_id))
        if occupancy is None: return HTMLResponse(_page(database, context, element_id, data, set(), 'Occupancy setup is incomplete for this Element and year.'), 400)
        occupancy_errors = set()
        if people_total > int(occupancy['max_total']): occupancy_errors.update(f'person_{p["id"]}' for p in people_rows)
        for person in people_rows:
            limit = one(database, 'SELECT max_count FROM setup_person_limits WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?', (cid, year, element_id, person['id']))
            if limit is None or people_counts[int(person['id'])] > int(limit['max_count']): occupancy_errors.add(f'person_{person["id"]}')
        if occupancy_errors: return HTMLResponse(_page(database, context, element_id, data, occupancy_errors, 'The selected people exceed the configured occupancy rules for this Element.'), 400)

        nightly_rates = []; season_names = []
        for offset in range(nights):
            day = start + timedelta(days=offset); season, rate = _element_rate_for_date(database, cid, element_id, day)
            if season is None or rate is None: return HTMLResponse(_page(database, context, element_id, data, {'start_date', 'end_date'}, f'Pricing setup is incomplete for {day.isoformat()}.'), 400)
            nightly_rates.append(rate); season_names.append(str(season['name']))
        element_amount = _price_element(element['pricing_method'], nightly_rates, people_total); unique_seasons = ', '.join(dict.fromkeys(season_names)); lines = [{'item': element['name'], 'rule': f'{element["pricing_method"]}; season(s): {unique_seasons}', 'amount': element_amount}]; total = element_amount

        person_price_errors = set()
        for person in people_rows:
            qty = people_counts[int(person['id'])]
            price = one(database, 'SELECT rate FROM setup_person_prices WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?', (cid, year, element_id, person['id']))
            if price is None: person_price_errors.add(f'person_{person["id"]}'); continue
            if qty:
                rate = float(price['rate']); amount = _price_person(element['pricing_method'], rate, qty, nights); lines.append({'item': person['name'], 'rule': f'{qty} × €{rate:.2f}; follows Element pricing method {element["pricing_method"]}', 'amount': amount}); total += amount
        if person_price_errors: return HTMLResponse(_page(database, context, element_id, data, person_price_errors, 'Person pricing setup is incomplete for this Element and year.'), 400)

        addons = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND active=1 ORDER BY name', (cid,)); addon_errors = set()
        for addon in addons:
            key = f'addon_{addon["id"]}'
            try: qty = valid_whole(data.get(key, '0'))
            except (TypeError, ValueError): qty = 0; addon_errors.add(key); continue
            if qty == 0: continue
            rule = _addon_rule(database, cid, year, element, addon['id'])
            if not rule['allowed'] or qty < rule['min'] or qty > rule['max']: addon_errors.add(key); continue
            amount = _price_addon(addon['pricing_method'], rule['rate'], qty, nights); lines.append({'item': addon['name'], 'rule': f'{rule["source"]}; qty {qty}; {addon["pricing_method"]} @ €{rule["rate"]:.2f}', 'amount': amount}); total += amount
        if addon_errors: return HTMLResponse(_page(database, context, element_id, data, addon_errors, 'One or more Add-ons are unavailable or outside their configured minimum/maximum quantity.'), 400)

        result = {'lines': lines, 'total': round(total, 2), 'nights': nights, 'people': people_total}; audit(database, context, cid, 'PRICE_TEST_CALCULATED', 'element', element_id, None, {'arrival': start.isoformat(), 'departure': end.isoformat(), 'people': people_total, 'total': result['total']}); return HTMLResponse(_page(database, context, element_id, data, set(), '', result), 200)
