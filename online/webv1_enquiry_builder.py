from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, form_data, layout
from .database import iso_now
from .setup015_calculator import _addon_rule, _element_rate_for_date, _price_addon, _price_element, _price_person
from .setup015_core import audit, context_for, one, require_csrf, rows, valid_whole, working_company


def _customer_name(row) -> str:
    name = f"{row['first_name']} {row['last_name']}".strip()
    return name or '(Unnamed customer)'


def _enquiry(database, company_id: int, enquiry_id: int):
    return one(
        database,
        '''SELECT e.*, c.first_name, c.last_name, c.email, c.phone
           FROM enquiries e
           LEFT JOIN customer_records c ON c.id=e.customer_id AND c.company_id=e.company_id
           WHERE e.id=? AND e.company_id=?''',
        (enquiry_id, company_id),
    )


def _builder_page(database, context, enquiry, selected_type: str = '', selected_element_id: int = 0, values=None, errors=None, message='') -> str:
    company_id = working_company(context)
    values = values or {}
    errors = errors or set()
    request_row = one(database, 'SELECT * FROM enquiry_requests WHERE enquiry_id=? AND company_id=?', (enquiry['id'], company_id))

    if not selected_type and request_row:
        selected_type = str(request_row['element_type'] or '')
    if not selected_element_id and request_row and request_row['element_id']:
        selected_element_id = int(request_row['element_id'])

    type_rows = rows(database, 'SELECT * FROM setup_element_types WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (company_id,))
    type_names = [str(r['name']) for r in type_rows]
    if selected_type not in type_names:
        selected_type = ''
        selected_element_id = 0

    element_rows = rows(
        database,
        'SELECT * FROM setup_elements WHERE company_id=? AND active=1 AND element_type=? ORDER BY name COLLATE NOCASE',
        (company_id, selected_type),
    ) if selected_type else []
    selected_element = next((r for r in element_rows if int(r['id']) == int(selected_element_id or 0)), None)
    if selected_element is None:
        selected_element_id = 0

    type_options = '<option value="">-- choose Element Type --</option>' + ''.join(
        f'<option value="{esc(name)}" {"selected" if name == selected_type else ""}>{esc(name)}</option>' for name in type_names
    )
    element_options = '<option value="">-- no specific Element yet --</option>' + ''.join(
        f'<option value="{int(r["id"])}" {"selected" if int(r["id"]) == selected_element_id else ""}>{esc(r["name"])}</option>' for r in element_rows
    )
    error_html = f'<div class="error">{esc(message)}</div>' if message else ''

    body = f'''<h1>Build Enquiry #{int(enquiry['id'])}</h1>
    <p><a href="/operations/enquiries/{int(enquiry['id'])}">← Enquiry details</a></p>
    {error_html}
    <div class="card"><h2>Customer request</h2>
      <p><strong>Customer:</strong> {esc(_customer_name(enquiry))}<br>
      <strong>Arrival:</strong> {esc(enquiry['arrival_date'] or '—')} &nbsp; <strong>Departure:</strong> {esc(enquiry['departure_date'] or '—')}<br>
      <strong>Source:</strong> {esc(enquiry['source'] or '—')}</p>
      <p class="muted">The enquiry remains provisional. Nothing here creates an Offer or reserves availability.</p>
    </div>
    <div class="card"><h2>1. What are they asking for?</h2>
      <form method="get" action="/operations/enquiries/{int(enquiry['id'])}/build">
        <div class="grid"><div><label>Element Type</label><select name="element_type">{type_options}</select></div>
        <div><label>Specific Element (optional)</label><select name="element">{element_options}</select></div></div>
        <p><button type="submit">Load Setup rules</button></p>
      </form>
    </div>'''

    if not selected_type:
        body += '<div class="card"><p>Choose an Element Type to continue.</p></div>'
        return layout(f'Build Enquiry #{int(enquiry["id"])}', body, context)

    if not selected_element:
        body += f'''<div class="card"><h2>Type-only enquiry</h2>
          <p>The customer can ask for <strong>{esc(selected_type)}</strong> without choosing a specific Element yet. This can be saved, but a price cannot be calculated until a specific Element is selected because prices and occupancy rules belong to Elements.</p>
          <form method="post" action="/operations/enquiries/{int(enquiry['id'])}/build">
            <input type="hidden" name="csrf" value="{esc(context['csrf_token'])}">
            <input type="hidden" name="element_type" value="{esc(selected_type)}">
            <input type="hidden" name="element_id" value="">
            <button type="submit">Save requested Element Type</button>
          </form></div>'''
        return layout(f'Build Enquiry #{int(enquiry["id"])}', body, context)

    people = rows(database, 'SELECT * FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (company_id,))
    addons = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (company_id,))
    saved_people = {int(r['person_type_id']): int(r['quantity']) for r in rows(database, 'SELECT * FROM enquiry_people WHERE enquiry_id=? AND company_id=?', (enquiry['id'], company_id))}
    saved_addons = {int(r['addon_id']): int(r['quantity']) for r in rows(database, 'SELECT * FROM enquiry_addons WHERE enquiry_id=? AND company_id=?', (enquiry['id'], company_id))}

    year = None
    if enquiry['arrival_date']:
        try:
            year = date.fromisoformat(enquiry['arrival_date']).year
        except ValueError:
            pass

    body += f'''<form method="post" action="/operations/enquiries/{int(enquiry['id'])}/build">
      <input type="hidden" name="csrf" value="{esc(context['csrf_token'])}">
      <input type="hidden" name="element_type" value="{esc(selected_type)}">
      <input type="hidden" name="element_id" value="{int(selected_element['id'])}">
      <div class="card"><h2>2. People — {esc(selected_element['name'])}</h2>
        <p class="muted">Occupancy and Person limits come directly from Setup for the enquiry year.</p><div class="grid">'''
    for person in people:
        key = f'person_{int(person["id"])}'
        current = values.get(key, str(saved_people.get(int(person['id']), 0)))
        style = 'border:2px solid #c62828;' if key in errors else ''
        rate_text = ''
        if year:
            price = one(database, 'SELECT rate FROM setup_person_prices WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?', (company_id, year, selected_element['id'], person['id']))
            if price is not None:
                rate_text = f' <span class="muted">€{float(price["rate"]):.2f}</span>'
        body += f'<div><label>{esc(person["name"])}{rate_text}</label><input style="{style}" type="number" min="0" name="{key}" value="{esc(current)}"></div>'
    body += '</div></div><div class="card"><h2>3. Add-ons</h2><table><thead><tr><th>Add-on</th><th>Setup rule</th><th>Quantity</th></tr></thead><tbody>'

    for addon in addons:
        key = f'addon_{int(addon["id"])}'
        current = values.get(key, str(saved_addons.get(int(addon['id']), 0)))
        style = 'border:2px solid #c62828;' if key in errors else ''
        rule = None
        if year:
            try:
                rule = _addon_rule(database, company_id, year, selected_element, int(addon['id']))
            except (TypeError, ValueError):
                rule = None
        if rule and rule['allowed']:
            detail = f'{rule["source"]}; min {rule["min"]}, max {rule["max"]}; €{rule["rate"]:.2f} — {addon["pricing_method"]}'
            input_html = f'<input style="{style}" type="number" min="0" max="{rule["max"]}" name="{key}" value="{esc(current)}">'
        elif rule:
            detail = rule['source']
            input_html = f'<input type="number" value="0" disabled>'
        else:
            detail = 'A valid Arrival date and complete annual Setup are required'
            input_html = f'<input style="{style}" type="number" min="0" name="{key}" value="{esc(current)}">'
        body += f'<tr><td>{esc(addon["name"])}</td><td>{esc(detail)}</td><td>{input_html}</td></tr>'

    body += '''</tbody></table></div><div class="card"><h2>4. Calculate and save</h2>
      <p>This calculates a <strong>provisional enquiry price</strong> from the existing Setup rules. It is not yet an Offer and does not freeze a Booking price.</p>
      <button type="submit">Calculate &amp; Save Enquiry</button></div></form>'''
    return layout(f'Build Enquiry #{int(enquiry["id"])}', body, context)


def register_enquiry_builder_routes(app) -> None:
    database = app.state.database

    @app.get('/operations/enquiries/{enquiry_id}/build', response_class=HTMLResponse)
    def enquiry_build(enquiry_id: int, request: Request, element_type: str = '', element: int = 0):
        context = context_for(database, request)
        company_id = working_company(context)
        enquiry = _enquiry(database, company_id, enquiry_id)
        if enquiry is None:
            return HTMLResponse(layout('Enquiry not found', '<div class="error">Enquiry not found.</div>', context), status_code=404)
        return _builder_page(database, context, enquiry, element_type.strip(), int(element or 0))

    @app.post('/operations/enquiries/{enquiry_id}/build', response_class=HTMLResponse)
    async def enquiry_build_save(enquiry_id: int, request: Request):
        context = context_for(database, request)
        company_id = working_company(context)
        enquiry = _enquiry(database, company_id, enquiry_id)
        if enquiry is None:
            return HTMLResponse(layout('Enquiry not found', '<div class="error">Enquiry not found.</div>', context), status_code=404)
        data = await form_data(request)
        require_csrf(context, data)
        selected_type = data.get('element_type', '').strip()
        valid_type = one(database, 'SELECT id FROM setup_element_types WHERE company_id=? AND active=1 AND name=? COLLATE NOCASE', (company_id, selected_type))
        if not selected_type or valid_type is None:
            return HTMLResponse(_builder_page(database, context, enquiry, selected_type, 0, data, set(), 'Choose a valid Element Type.'), status_code=400)

        try:
            element_id = int(data.get('element_id', '') or 0)
        except ValueError:
            element_id = 0

        now = iso_now()
        if element_id == 0:
            with database.connect() as connection:
                connection.execute(
                    '''INSERT INTO enquiry_requests(enquiry_id,company_id,element_type,element_id,provisional_total,pricing_snapshot_json,updated_at)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(enquiry_id) DO UPDATE SET element_type=excluded.element_type,element_id=NULL,provisional_total=NULL,pricing_snapshot_json='{}',updated_at=excluded.updated_at''',
                    (enquiry_id, company_id, selected_type, None, None, '{}', now),
                )
                connection.execute('DELETE FROM enquiry_people WHERE enquiry_id=? AND company_id=?', (enquiry_id, company_id))
                connection.execute('DELETE FROM enquiry_addons WHERE enquiry_id=? AND company_id=?', (enquiry_id, company_id))
                connection.execute('UPDATE enquiries SET updated_at=? WHERE id=? AND company_id=?', (now, enquiry_id, company_id))
            audit(database, context, company_id, 'ENQUIRY_REQUEST_UPDATED', 'enquiry', enquiry_id, after={'element_type': selected_type, 'element_id': None, 'provisional_total': None})
            return RedirectResponse(f'/operations/enquiries/{enquiry_id}?saved=1', status_code=303)

        element = one(database, 'SELECT * FROM setup_elements WHERE company_id=? AND id=? AND active=1 AND element_type=?', (company_id, element_id, selected_type))
        if element is None:
            return HTMLResponse(_builder_page(database, context, enquiry, selected_type, 0, data, set(), 'Choose a valid Element for that Element Type.'), status_code=400)

        errors: set[str] = set()
        try:
            start = date.fromisoformat(enquiry['arrival_date'] or '')
            end = date.fromisoformat(enquiry['departure_date'] or '')
        except ValueError:
            start = end = None
        if not start or not end or end <= start:
            return HTMLResponse(_builder_page(database, context, enquiry, selected_type, element_id, data, set(), 'Enter valid Arrival and Departure dates on the Enquiry before calculating a price.'), status_code=400)
        if start.year != (end - timedelta(days=1)).year:
            return HTMLResponse(_builder_page(database, context, enquiry, selected_type, element_id, data, set(), 'The current proven pricing engine requires the stay to remain within one pricing year.'), status_code=400)

        year = start.year
        nights = (end - start).days
        people = rows(database, 'SELECT * FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (company_id,))
        people_counts: dict[int, int] = {}
        people_total = 0
        for person in people:
            key = f'person_{int(person["id"])}'
            try:
                qty = valid_whole(data.get(key, '0'))
            except (TypeError, ValueError):
                qty = 0
                errors.add(key)
            people_counts[int(person['id'])] = qty
            people_total += qty
        if people_total <= 0:
            errors.update(f'person_{int(p["id"])}' for p in people)
        if errors:
            return HTMLResponse(_builder_page(database, context, enquiry, selected_type, element_id, data, errors, 'Enter at least one person and correct every highlighted value.'), status_code=400)

        occupancy = one(database, 'SELECT max_total FROM setup_occupancy WHERE company_id=? AND year=? AND element_id=?', (company_id, year, element_id))
        if occupancy is None:
            return HTMLResponse(_builder_page(database, context, enquiry, selected_type, element_id, data, set(), 'Occupancy Setup is incomplete for this Element and year.'), status_code=400)
        if people_total > int(occupancy['max_total']):
            errors.update(f'person_{int(p["id"])}' for p in people)
        for person in people:
            limit = one(database, 'SELECT max_count FROM setup_person_limits WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?', (company_id, year, element_id, person['id']))
            if limit is None or people_counts[int(person['id'])] > int(limit['max_count']):
                errors.add(f'person_{int(person["id"])}')
        if errors:
            return HTMLResponse(_builder_page(database, context, enquiry, selected_type, element_id, data, errors, 'The selected people exceed the configured occupancy rules for this Element.'), status_code=400)

        nightly_rates: list[float] = []
        season_names: list[str] = []
        for offset in range(nights):
            day = start + timedelta(days=offset)
            season, rate = _element_rate_for_date(database, company_id, element_id, day)
            if season is None or rate is None:
                return HTMLResponse(_builder_page(database, context, enquiry, selected_type, element_id, data, set(), f'Pricing Setup is incomplete for {day.isoformat()}.'), status_code=400)
            nightly_rates.append(rate)
            season_names.append(str(season['name']))

        element_amount = _price_element(str(element['pricing_method']), nightly_rates, people_total)
        lines = [{'item': str(element['name']), 'rule': f'{element["pricing_method"]}; season(s): {", ".join(dict.fromkeys(season_names))}', 'amount': round(element_amount, 2)}]
        total = element_amount
        people_snapshot = []
        for person in people:
            qty = people_counts[int(person['id'])]
            price = one(database, 'SELECT rate FROM setup_person_prices WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?', (company_id, year, element_id, person['id']))
            if price is None:
                errors.add(f'person_{int(person["id"])}')
                continue
            rate = float(price['rate'])
            amount = _price_person(str(element['pricing_method']), rate, qty, nights) if qty else 0.0
            people_snapshot.append({'person_type_id': int(person['id']), 'name': str(person['name']), 'quantity': qty, 'rate': rate, 'amount': round(amount, 2)})
            if qty:
                lines.append({'item': str(person['name']), 'rule': f'{qty} × €{rate:.2f}; follows Element pricing method {element["pricing_method"]}', 'amount': round(amount, 2)})
                total += amount
        if errors:
            return HTMLResponse(_builder_page(database, context, enquiry, selected_type, element_id, data, errors, 'Person pricing Setup is incomplete for this Element and year.'), status_code=400)

        addons = rows(database, 'SELECT * FROM setup_addons WHERE company_id=? AND active=1 ORDER BY name COLLATE NOCASE', (company_id,))
        addon_counts: dict[int, int] = {}
        addon_snapshot = []
        for addon in addons:
            key = f'addon_{int(addon["id"])}'
            try:
                qty = valid_whole(data.get(key, '0'))
            except (TypeError, ValueError):
                qty = 0
                errors.add(key)
                continue
            addon_counts[int(addon['id'])] = qty
            if qty == 0:
                continue
            try:
                rule = _addon_rule(database, company_id, year, element, int(addon['id']))
            except (TypeError, ValueError):
                errors.add(key)
                continue
            if not rule['allowed'] or qty < int(rule['min']) or qty > int(rule['max']):
                errors.add(key)
                continue
            amount = _price_addon(str(addon['pricing_method']), float(rule['rate']), qty, nights)
            addon_snapshot.append({'addon_id': int(addon['id']), 'name': str(addon['name']), 'quantity': qty, 'pricing_method': str(addon['pricing_method']), 'rate': float(rule['rate']), 'rule_source': str(rule['source']), 'amount': round(amount, 2)})
            lines.append({'item': str(addon['name']), 'rule': f'{rule["source"]}; qty {qty}; {addon["pricing_method"]} @ €{float(rule["rate"]):.2f}', 'amount': round(amount, 2)})
            total += amount
        if errors:
            return HTMLResponse(_builder_page(database, context, enquiry, selected_type, element_id, data, errors, 'One or more Add-ons are unavailable or outside their configured minimum/maximum quantity.'), status_code=400)

        total = round(total, 2)
        snapshot = {
            'element_type': selected_type,
            'element_id': element_id,
            'element_name': str(element['name']),
            'pricing_method': str(element['pricing_method']),
            'arrival_date': start.isoformat(),
            'departure_date': end.isoformat(),
            'nights': nights,
            'people_total': people_total,
            'people': people_snapshot,
            'addons': addon_snapshot,
            'lines': lines,
            'total': total,
        }
        snapshot_json = json.dumps(snapshot, separators=(',', ':'), ensure_ascii=False)
        with database.connect() as connection:
            connection.execute(
                '''INSERT INTO enquiry_requests(enquiry_id,company_id,element_type,element_id,provisional_total,pricing_snapshot_json,updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(enquiry_id) DO UPDATE SET element_type=excluded.element_type,element_id=excluded.element_id,provisional_total=excluded.provisional_total,pricing_snapshot_json=excluded.pricing_snapshot_json,updated_at=excluded.updated_at''',
                (enquiry_id, company_id, selected_type, element_id, total, snapshot_json, now),
            )
            connection.execute('DELETE FROM enquiry_people WHERE enquiry_id=? AND company_id=?', (enquiry_id, company_id))
            connection.execute('DELETE FROM enquiry_addons WHERE enquiry_id=? AND company_id=?', (enquiry_id, company_id))
            for person_id, qty in people_counts.items():
                if qty:
                    connection.execute('INSERT INTO enquiry_people(enquiry_id,company_id,person_type_id,quantity) VALUES (?,?,?,?)', (enquiry_id, company_id, person_id, qty))
            for addon_id, qty in addon_counts.items():
                if qty:
                    connection.execute('INSERT INTO enquiry_addons(enquiry_id,company_id,addon_id,quantity) VALUES (?,?,?,?)', (enquiry_id, company_id, addon_id, qty))
            connection.execute('UPDATE enquiries SET party_size=?,updated_at=? WHERE id=? AND company_id=?', (people_total, now, enquiry_id, company_id))
        audit(database, context, company_id, 'ENQUIRY_PRICED', 'enquiry', enquiry_id, after={'element_type': selected_type, 'element_id': element_id, 'people': people_total, 'total': total})
        return RedirectResponse(f'/operations/enquiries/{enquiry_id}?saved=1', status_code=303)
