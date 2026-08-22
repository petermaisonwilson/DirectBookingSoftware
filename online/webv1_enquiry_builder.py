from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, form_data, layout
from .database import iso_now
from .setup015_calculator import _addon_rule, _element_rate_for_date, _price_addon, _price_element, _price_person
from .setup015_core import audit, context_for, one, require_csrf, rows, valid_whole, working_company


def _customer_name(row) -> str:
    name = f"{row['first_name']} {row['last_name']}".strip()
    return name or '(Unnamed customer)'


def _customer(database, company_id: int, customer_id: int):
    return one(database, 'SELECT * FROM customer_records WHERE id=? AND company_id=? AND active=1', (customer_id, company_id))


def _enquiry(database, company_id: int, enquiry_id: int):
    return one(
        database,
        '''SELECT e.*, c.first_name, c.last_name, c.email, c.phone
           FROM enquiries e
           LEFT JOIN customer_records c ON c.id=e.customer_id AND c.company_id=e.company_id
           WHERE e.id=? AND e.company_id=?''',
        (enquiry_id, company_id),
    )


def _int_or_zero(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _setup_payload(database, company_id: int):
    types = rows(database, 'SELECT name FROM setup_element_types WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (company_id,))
    elements = rows(database, 'SELECT * FROM setup_elements WHERE company_id=? AND active=1 ORDER BY element_type,name COLLATE NOCASE', (company_id,))
    people = rows(database, 'SELECT * FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (company_id,))
    addons = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (company_id,))
    years = rows(database, 'SELECT year FROM setup_years WHERE company_id=? ORDER BY year', (company_id,))

    element_json = [{'id': int(e['id']), 'name': str(e['name']), 'type': str(e['element_type'])} for e in elements]
    rule_map: dict[str, dict[str, dict[str, object]]] = {}
    person_rate_map: dict[str, dict[str, dict[str, float]]] = {}
    for yr_row in years:
        year = int(yr_row['year'])
        ykey = str(year)
        rule_map[ykey] = {}
        person_rate_map[ykey] = {}
        for element in elements:
            ekey = str(int(element['id']))
            rule_map[ykey][ekey] = {}
            person_rate_map[ykey][ekey] = {}
            for addon in addons:
                rule = _addon_rule(database, company_id, year, element, int(addon['id']))
                rule_map[ykey][ekey][str(int(addon['id']))] = {
                    'allowed': bool(rule['allowed']),
                    'source': str(rule['source']),
                    'min': rule['min'],
                    'max': rule['max'],
                    'rate': rule['rate'],
                }
            for person in people:
                price = one(database, 'SELECT rate FROM setup_person_prices WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?', (company_id, year, element['id'], person['id']))
                if price is not None:
                    person_rate_map[ykey][ekey][str(int(person['id']))] = float(price['rate'])
    return types, elements, people, addons, element_json, rule_map, person_rate_map


def _saved_values(database, company_id: int, enquiry) -> dict[str, str]:
    values = {
        'arrival_date': str(enquiry['arrival_date'] or ''),
        'departure_date': str(enquiry['departure_date'] or ''),
        'party_size': '' if enquiry['party_size'] is None else str(int(enquiry['party_size'])),
        'source': str(enquiry['source'] or ''),
        'notes': str(enquiry['notes'] or ''),
        'element_type': '',
        'element_id': '',
    }
    request_row = one(database, 'SELECT * FROM enquiry_requests WHERE enquiry_id=? AND company_id=?', (enquiry['id'], company_id))
    if request_row:
        values['element_type'] = str(request_row['element_type'] or '')
        values['element_id'] = '' if request_row['element_id'] is None else str(int(request_row['element_id']))
    for row in rows(database, 'SELECT person_type_id,quantity FROM enquiry_people WHERE enquiry_id=? AND company_id=?', (enquiry['id'], company_id)):
        values[f'person_{int(row["person_type_id"])}'] = str(int(row['quantity']))
    for row in rows(database, 'SELECT addon_id,quantity FROM enquiry_addons WHERE enquiry_id=? AND company_id=?', (enquiry['id'], company_id)):
        values[f'addon_{int(row["addon_id"])}'] = str(int(row['quantity']))
    return values


def _validate_dates(values: dict[str, str]) -> tuple[date | None, date | None, str]:
    arrival = values.get('arrival_date', '').strip()
    departure = values.get('departure_date', '').strip()
    if bool(arrival) != bool(departure):
        return None, None, 'Enter both Arrival and Departure dates, or leave both blank.'
    if not arrival:
        return None, None, ''
    try:
        start = date.fromisoformat(arrival)
        end = date.fromisoformat(departure)
    except ValueError:
        return None, None, 'Enter valid Arrival and Departure dates.'
    if end <= start:
        return None, None, 'Departure date must be after Arrival date.'
    return start, end, ''


def _calculate(database, company_id: int, values: dict[str, str]):
    selected_type = values.get('element_type', '').strip()
    element_id = _int_or_zero(values.get('element_id'))
    element = one(database, 'SELECT * FROM setup_elements WHERE company_id=? AND id=? AND active=1 AND element_type=?', (company_id, element_id, selected_type))
    if element is None:
        return None, set(), 'Choose a specific Element before calculating a provisional price.'

    start, end, date_error = _validate_dates(values)
    if date_error:
        return None, {'arrival_date', 'departure_date'}, date_error
    if start is None or end is None:
        return None, {'arrival_date', 'departure_date'}, 'Arrival and Departure dates are required to calculate a provisional price.'
    if start.year != (end - timedelta(days=1)).year:
        return None, {'arrival_date', 'departure_date'}, 'The current proven pricing engine requires the stay to remain within one pricing year.'

    year = start.year
    nights = (end - start).days
    people = rows(database, 'SELECT * FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (company_id,))
    people_counts: dict[int, int] = {}
    people_total = 0
    errors: set[str] = set()
    for person in people:
        key = f'person_{int(person["id"])}'
        try:
            qty = valid_whole(values.get(key, '0'))
        except (TypeError, ValueError):
            qty = 0
            errors.add(key)
        people_counts[int(person['id'])] = qty
        people_total += qty
    if people_total <= 0:
        errors.update(f'person_{int(p["id"])}' for p in people)
        return None, errors, 'Enter at least one person.'
    if errors:
        return None, errors, 'Correct every highlighted Person quantity.'

    occupancy = one(database, 'SELECT max_total FROM setup_occupancy WHERE company_id=? AND year=? AND element_id=?', (company_id, year, element_id))
    if occupancy is None:
        return None, set(), 'Occupancy Setup is incomplete for this Element and year.'
    if people_total > int(occupancy['max_total']):
        errors.update(f'person_{int(p["id"])}' for p in people)
    for person in people:
        limit = one(database, 'SELECT max_count FROM setup_person_limits WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?', (company_id, year, element_id, person['id']))
        if limit is None or people_counts[int(person['id'])] > int(limit['max_count']):
            errors.add(f'person_{int(person["id"])}')
    if errors:
        return None, errors, 'The selected people exceed the configured occupancy rules for this Element.'

    nightly_rates: list[float] = []
    season_names: list[str] = []
    for offset in range(nights):
        day = start + timedelta(days=offset)
        season, rate = _element_rate_for_date(database, company_id, element_id, day)
        if season is None or rate is None:
            return None, {'arrival_date', 'departure_date'}, f'Pricing Setup is incomplete for {day.isoformat()}.'
        nightly_rates.append(rate)
        season_names.append(str(season['name']))

    element_amount = _price_element(element['pricing_method'], nightly_rates, people_total)
    unique_seasons = ', '.join(dict.fromkeys(season_names))
    lines = [{'item': str(element['name']), 'rule': f'{element["pricing_method"]}; season(s): {unique_seasons}', 'amount': element_amount}]
    total = element_amount

    for person in people:
        qty = people_counts[int(person['id'])]
        price = one(database, 'SELECT rate FROM setup_person_prices WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?', (company_id, year, element_id, person['id']))
        if price is None:
            return None, {f'person_{int(person["id"])}'}, 'Person pricing Setup is incomplete for this Element and year.'
        if qty:
            rate = float(price['rate'])
            amount = _price_person(element['pricing_method'], rate, qty, nights)
            lines.append({'item': str(person['name']), 'rule': f'{qty} × €{rate:.2f}; follows Element pricing method {element["pricing_method"]}', 'amount': amount})
            total += amount

    addon_counts: dict[int, int] = {}
    addons = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (company_id,))
    addon_errors: set[str] = set()
    for addon in addons:
        key = f'addon_{int(addon["id"])}'
        try:
            qty = valid_whole(values.get(key, '0'))
        except (TypeError, ValueError):
            qty = 0
            addon_errors.add(key)
        addon_counts[int(addon['id'])] = qty
        if qty == 0:
            continue
        rule = _addon_rule(database, company_id, year, element, int(addon['id']))
        if not rule['allowed'] or qty < int(rule['min']) or qty > int(rule['max']):
            addon_errors.add(key)
            continue
        amount = _price_addon(addon['pricing_method'], float(rule['rate']), qty, nights)
        lines.append({'item': str(addon['name']), 'rule': f'{rule["source"]}; qty {qty}; {addon["pricing_method"]} @ €{float(rule["rate"]):.2f}', 'amount': amount})
        total += amount
    if addon_errors:
        return None, addon_errors, 'One or more Add-ons are unavailable or outside their configured minimum/maximum quantity.'

    return {
        'element_id': element_id,
        'element_type': selected_type,
        'element_name': str(element['name']),
        'year': year,
        'nights': nights,
        'people_total': people_total,
        'people_counts': people_counts,
        'addon_counts': addon_counts,
        'lines': lines,
        'total': round(total, 2),
    }, set(), ''


def _form_page(database, context, customer, values: dict[str, str], *, enquiry_id: int | None = None, errors=None, message='', result=None):
    company_id = working_company(context)
    errors = errors or set()
    types, elements, people, addons, element_json, rule_map, person_rate_map = _setup_payload(database, company_id)
    type_names = [str(r['name']) for r in types]
    selected_type = values.get('element_type', '').strip()
    selected_element_id = _int_or_zero(values.get('element_id'))
    if selected_type not in type_names:
        selected_type = ''
        selected_element_id = 0

    type_options = '<option value="">-- not decided yet --</option>' + ''.join(
        f'<option value="{esc(name)}" {"selected" if name == selected_type else ""}>{esc(name)}</option>' for name in type_names
    )
    element_options = '<option value="">-- no specific Element yet --</option>' + ''.join(
        f'<option value="{int(e["id"])}" data-type="{esc(e["element_type"])}" {"selected" if int(e["id"]) == selected_element_id else ""}>{esc(e["name"])}</option>' for e in elements
    )
    error_html = f'<div class="error">{esc(message)}</div>' if message else ''
    title = f'Edit Enquiry #{enquiry_id}' if enquiry_id else 'New Enquiry'
    back = f'/operations/enquiries/{enquiry_id}' if enquiry_id else f'/operations/customers/{int(customer["id"])}'
    action = f'/operations/enquiries/{enquiry_id}/edit' if enquiry_id else f'/operations/customers/{int(customer["id"])}/enquiries/new'

    def style(key: str) -> str:
        return 'border:2px solid #c62828;' if key in errors else ''

    body = f'''<h1>{esc(title)}</h1><p><a href="{back}">← Back</a></p>{error_html}
    <form method="post" action="{action}" id="integrated-enquiry-form">
      <input type="hidden" name="csrf" value="{esc(context['csrf_token'])}">
      <div class="card"><h2>Customer &amp; stay</h2><p><strong>Customer:</strong> {esc(_customer_name(customer))}</p>
        <div class="grid">
          <div><label>Arrival date</label><input id="arrival_date" style="{style('arrival_date')}" type="date" name="arrival_date" value="{esc(values.get('arrival_date',''))}"></div>
          <div><label>Departure date</label><input style="{style('departure_date')}" type="date" name="departure_date" value="{esc(values.get('departure_date',''))}"></div>
          <div><label>Party size (if breakdown not known yet)</label><input style="{style('party_size')}" type="number" min="1" name="party_size" value="{esc(values.get('party_size',''))}"></div>
          <div><label>Source</label><input name="source" placeholder="Phone, website, walk-in..." value="{esc(values.get('source',''))}"></div>
        </div>
      </div>
      <div class="card"><h2>Element</h2><p class="muted">Choose the Element Type and, if known, the exact Element. You do not need to leave this screen to load Setup.</p>
        <div class="grid">
          <div><label>Element Type</label><select id="element_type" name="element_type">{type_options}</select></div>
          <div><label>Specific Element (optional)</label><select id="element_id" name="element_id">{element_options}</select></div>
        </div>
      </div>
      <div class="card" id="people-card"><h2>People</h2><p class="muted">Person prices and occupancy rules come directly from Setup once a specific Element and pricing year are known.</p><div class="grid">'''
    for person in people:
        key = f'person_{int(person["id"])}'
        body += f'''<div><label>{esc(person['name'])} <span class="muted" id="person-rate-{int(person['id'])}"></span></label>
          <input class="person-input" data-person-id="{int(person['id'])}" style="{style(key)}" type="number" min="0" name="{key}" value="{esc(values.get(key,'0'))}"></div>'''
    body += '</div></div><div class="card" id="addons-card"><h2>Add-ons</h2><table><thead><tr><th>Add-on</th><th>Setup rule</th><th>Quantity</th></tr></thead><tbody>'
    for addon in addons:
        key = f'addon_{int(addon["id"])}'
        body += f'''<tr><td>{esc(addon['name'])}</td><td id="addon-rule-{int(addon['id'])}" class="muted">Choose dates and an Element.</td>
          <td><input class="addon-input" data-addon-id="{int(addon['id'])}" style="{style(key)}" type="number" min="0" name="{key}" value="{esc(values.get(key,'0'))}"></td></tr>'''
    body += f'''</tbody></table></div>
      <div class="card"><h2>Notes</h2><textarea name="notes" rows="5" style="width:100%;padding:9px;border:1px solid #aeb8c4;border-radius:6px">{esc(values.get('notes',''))}</textarea></div>
      <div class="card"><h2>Provisional price</h2><p>Calculate from the existing Setup rules before saving, or save an unpriced/type-only enquiry if the customer has not decided enough details yet.</p>
        <p><button type="submit" name="action" value="calculate">Calculate provisional price</button> <button type="submit" name="action" value="save">Save Enquiry</button></p>
      </div>'''
    if result:
        line_rows = ''.join(f'<tr><td>{esc(line["item"])}</td><td>{esc(line["rule"])}</td><td>€{float(line["amount"]):.2f}</td></tr>' for line in result['lines'])
        body += f'''<div class="card"><h2>Calculated provisional total: €{float(result['total']):.2f}</h2>
          <p>{int(result['nights'])} night(s), {int(result['people_total'])} person(s). This is still an Enquiry price, not an Offer.</p>
          <table><thead><tr><th>Item</th><th>Rule used</th><th>Amount</th></tr></thead><tbody>{line_rows}</tbody></table>
          <p><button type="submit" name="action" value="save">Save Enquiry with this price</button></p></div>'''
    body += '</form>'

    elements_js = json.dumps(element_json, separators=(',', ':')).replace('</', '<\\/')
    rules_js = json.dumps(rule_map, separators=(',', ':')).replace('</', '<\\/')
    person_rates_js = json.dumps(person_rate_map, separators=(',', ':')).replace('</', '<\\/')
    body += f'''<script>
(function(){{
 const elements={elements_js}; const rules={rules_js}; const personRates={person_rates_js};
 const typeSel=document.getElementById('element_type'); const elementSel=document.getElementById('element_id'); const arrival=document.getElementById('arrival_date');
 const originalElement=String({json.dumps(str(selected_element_id) if selected_element_id else '')});
 function rebuildElements(){{
   const wanted=typeSel.value; const old=elementSel.value || originalElement; elementSel.innerHTML='<option value="">-- no specific Element yet --</option>';
   elements.filter(e=>e.type===wanted).forEach(function(e){{const o=document.createElement('option');o.value=String(e.id);o.textContent=e.name;if(String(e.id)===old)o.selected=true;elementSel.appendChild(o);}});
   applyRules();
 }}
 function applyRules(){{
   const year=arrival.value ? arrival.value.slice(0,4) : ''; const eid=elementSel.value; const configured=Boolean(year && eid);
   document.querySelectorAll('.person-input').forEach(function(input){{input.disabled=!eid; const pid=input.dataset.personId; const rate=((personRates[year]||{{}})[eid]||{{}})[pid]; document.getElementById('person-rate-'+pid).textContent=(rate===undefined?'':'€'+Number(rate).toFixed(2));}});
   document.querySelectorAll('.addon-input').forEach(function(input){{const aid=input.dataset.addonId; const rule=(((rules[year]||{{}})[eid]||{{}})[aid]); const text=document.getElementById('addon-rule-'+aid);
     if(configured && rule && rule.allowed){{input.disabled=false;input.max=String(rule.max);text.textContent=rule.source+'; min '+rule.min+', max '+rule.max+'; €'+Number(rule.rate).toFixed(2);if(Number(input.value)>Number(rule.max))input.value=String(rule.max);}}
     else {{input.disabled=true;input.removeAttribute('max');if(configured && rule){{input.value='0';text.textContent=rule.source;}}else{{text.textContent='Choose dates and a specific Element.';}}}}
   }});
 }}
 typeSel.addEventListener('change',function(){{elementSel.value='';rebuildElements();}}); elementSel.addEventListener('change',applyRules); arrival.addEventListener('change',applyRules);
 rebuildElements();
}})();
</script>'''
    return layout(title, body, context)


def _basic_values(data: dict[str, str]) -> dict[str, str]:
    return {key: data.get(key, '').strip() for key in ('arrival_date','departure_date','party_size','source','notes','element_type','element_id')}


def _save(database, context, company_id: int, customer_id: int, values: dict[str, str], calculation, enquiry_id: int | None = None) -> int:
    now = iso_now()
    selected_type = values.get('element_type', '').strip()
    element_id = _int_or_zero(values.get('element_id')) or None
    if calculation:
        party_size = int(calculation['people_total'])
        provisional_total = float(calculation['total'])
        snapshot_json = json.dumps({
            'element_type': calculation['element_type'], 'element_id': calculation['element_id'], 'element_name': calculation['element_name'],
            'year': calculation['year'], 'nights': calculation['nights'], 'people_total': calculation['people_total'],
            'lines': calculation['lines'], 'total': calculation['total'],
        }, separators=(',', ':'))
    else:
        party_size = _int_or_zero(values.get('party_size')) or None
        provisional_total = None
        snapshot_json = '{}'

    with database.connect() as connection:
        if enquiry_id is None:
            enquiry_id = int(connection.execute(
                '''INSERT INTO enquiries(company_id,customer_id,status,source,arrival_date,departure_date,party_size,notes,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)''',
                (company_id, customer_id, 'new', values.get('source',''), values.get('arrival_date') or None, values.get('departure_date') or None, party_size, values.get('notes',''), now, now),
            ).lastrowid)
        else:
            connection.execute(
                '''UPDATE enquiries SET source=?,arrival_date=?,departure_date=?,party_size=?,notes=?,updated_at=? WHERE id=? AND company_id=?''',
                (values.get('source',''), values.get('arrival_date') or None, values.get('departure_date') or None, party_size, values.get('notes',''), now, enquiry_id, company_id),
            )
        connection.execute('DELETE FROM enquiry_people WHERE enquiry_id=? AND company_id=?', (enquiry_id, company_id))
        connection.execute('DELETE FROM enquiry_addons WHERE enquiry_id=? AND company_id=?', (enquiry_id, company_id))
        if selected_type:
            connection.execute(
                '''INSERT INTO enquiry_requests(enquiry_id,company_id,element_type,element_id,provisional_total,pricing_snapshot_json,updated_at)
                   VALUES (?,?,?,?,?,?,?) ON CONFLICT(enquiry_id) DO UPDATE SET element_type=excluded.element_type,element_id=excluded.element_id,provisional_total=excluded.provisional_total,pricing_snapshot_json=excluded.pricing_snapshot_json,updated_at=excluded.updated_at''',
                (enquiry_id, company_id, selected_type, element_id, provisional_total, snapshot_json, now),
            )
        else:
            connection.execute('DELETE FROM enquiry_requests WHERE enquiry_id=? AND company_id=?', (enquiry_id, company_id))
        if calculation:
            for person_id, qty in calculation['people_counts'].items():
                if qty:
                    connection.execute('INSERT INTO enquiry_people(enquiry_id,company_id,person_type_id,quantity) VALUES (?,?,?,?)', (enquiry_id, company_id, person_id, qty))
            for addon_id, qty in calculation['addon_counts'].items():
                if qty:
                    connection.execute('INSERT INTO enquiry_addons(enquiry_id,company_id,addon_id,quantity) VALUES (?,?,?,?)', (enquiry_id, company_id, addon_id, qty))
    action = 'ENQUIRY_CREATED' if enquiry_id and one(database, 'SELECT created_at,updated_at FROM enquiries WHERE id=? AND company_id=?', (enquiry_id, company_id))['created_at'] == now else 'ENQUIRY_UPDATED'
    audit(database, context, company_id, action, 'enquiry', enquiry_id, after={'customer_id': customer_id, 'element_type': selected_type, 'element_id': element_id, 'provisional_total': provisional_total})
    return int(enquiry_id)


def register_enquiry_builder_routes(app) -> None:
    database = app.state.database

    @app.get('/operations/customers/{customer_id}/enquiries/new', response_class=HTMLResponse)
    def integrated_enquiry_new(customer_id: int, request: Request):
        context = context_for(database, request)
        company_id = working_company(context)
        customer = _customer(database, company_id, customer_id)
        if customer is None:
            return HTMLResponse(layout('Customer not found', '<div class="error">Customer not found.</div>', context), status_code=404)
        return _form_page(database, context, customer, {})

    @app.post('/operations/customers/{customer_id}/enquiries/new', response_class=HTMLResponse)
    async def integrated_enquiry_create(customer_id: int, request: Request):
        context = context_for(database, request)
        company_id = working_company(context)
        customer = _customer(database, company_id, customer_id)
        if customer is None:
            return HTMLResponse(layout('Customer not found', '<div class="error">Customer not found.</div>', context), status_code=404)
        data = await form_data(request)
        require_csrf(context, data)
        values = dict(data)
        basic = _basic_values(data)
        start, end, date_error = _validate_dates(basic)
        if date_error:
            return HTMLResponse(_form_page(database, context, customer, values, errors={'arrival_date','departure_date'}, message=date_error), status_code=400)
        if basic['party_size']:
            try:
                if int(basic['party_size']) < 1:
                    raise ValueError
            except ValueError:
                return HTMLResponse(_form_page(database, context, customer, values, errors={'party_size'}, message='Party size must be a whole number of at least 1.'), status_code=400)
        if basic['element_type']:
            valid_type = one(database, 'SELECT id FROM setup_element_types WHERE company_id=? AND active=1 AND name=? COLLATE NOCASE', (company_id, basic['element_type']))
            if valid_type is None:
                return HTMLResponse(_form_page(database, context, customer, values, message='Choose a valid Element Type.'), status_code=400)
        calculation = None
        action = data.get('action', 'save')
        if action == 'calculate' or _int_or_zero(basic['element_id']):
            calculation, calc_errors, calc_message = _calculate(database, company_id, values)
            if calc_message:
                status = 400 if action == 'save' or _int_or_zero(basic['element_id']) else 200
                return HTMLResponse(_form_page(database, context, customer, values, errors=calc_errors, message=calc_message), status_code=status)
        if action == 'calculate':
            return HTMLResponse(_form_page(database, context, customer, values, result=calculation), status_code=200)
        enquiry_id = _save(database, context, company_id, customer_id, basic | values, calculation)
        return RedirectResponse(f'/operations/enquiries/{enquiry_id}?saved=1', status_code=303)

    @app.get('/operations/enquiries/{enquiry_id}/edit', response_class=HTMLResponse)
    def integrated_enquiry_edit(enquiry_id: int, request: Request):
        context = context_for(database, request)
        company_id = working_company(context)
        enquiry = _enquiry(database, company_id, enquiry_id)
        if enquiry is None:
            return HTMLResponse(layout('Enquiry not found', '<div class="error">Enquiry not found.</div>', context), status_code=404)
        customer = _customer(database, company_id, int(enquiry['customer_id']))
        values = _saved_values(database, company_id, enquiry)
        return _form_page(database, context, customer, values, enquiry_id=enquiry_id)

    @app.post('/operations/enquiries/{enquiry_id}/edit', response_class=HTMLResponse)
    async def integrated_enquiry_update(enquiry_id: int, request: Request):
        context = context_for(database, request)
        company_id = working_company(context)
        enquiry = _enquiry(database, company_id, enquiry_id)
        if enquiry is None:
            return HTMLResponse(layout('Enquiry not found', '<div class="error">Enquiry not found.</div>', context), status_code=404)
        customer = _customer(database, company_id, int(enquiry['customer_id']))
        data = await form_data(request)
        require_csrf(context, data)
        values = dict(data)
        basic = _basic_values(data)
        start, end, date_error = _validate_dates(basic)
        if date_error:
            return HTMLResponse(_form_page(database, context, customer, values, enquiry_id=enquiry_id, errors={'arrival_date','departure_date'}, message=date_error), status_code=400)
        calculation = None
        action = data.get('action', 'save')
        if action == 'calculate' or _int_or_zero(basic['element_id']):
            calculation, calc_errors, calc_message = _calculate(database, company_id, values)
            if calc_message:
                return HTMLResponse(_form_page(database, context, customer, values, enquiry_id=enquiry_id, errors=calc_errors, message=calc_message), status_code=400)
        if action == 'calculate':
            return HTMLResponse(_form_page(database, context, customer, values, enquiry_id=enquiry_id, result=calculation), status_code=200)
        _save(database, context, company_id, int(enquiry['customer_id']), basic | values, calculation, enquiry_id=enquiry_id)
        return RedirectResponse(f'/operations/enquiries/{enquiry_id}?saved=1', status_code=303)

    @app.get('/operations/enquiries/{enquiry_id}/build', response_class=HTMLResponse)
    def old_builder_redirect(enquiry_id: int, request: Request, element_type: str = '', element: str = ''):
        context = context_for(database, request)
        company_id = working_company(context)
        if _enquiry(database, company_id, enquiry_id) is None:
            return HTMLResponse(layout('Enquiry not found', '<div class="error">Enquiry not found.</div>', context), status_code=404)
        return RedirectResponse(f'/operations/enquiries/{enquiry_id}/edit', status_code=303)
