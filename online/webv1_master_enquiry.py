"""Standalone unified Enquiry creation screen.

Kept at a separate URL until the existing booking and finance pages have
been replaced. Uses the same master record for subsequent lifecycle actions.
"""
import json
from datetime import date
from uuid import uuid4


from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import esc, form_data, layout
from .database import iso_now
from .setup015_core import context_for, require_csrf, working_company
from .webv1_reservation_core import ReservationError
from .webv1_enquiry_builder import _calculate
from .webv1_addon_person import addon_person_mode
from .webv1_addon_when import when_options
from .webv1_reservation_service import create_enquiry


def register_master_enquiry_routes(app):
    database = app.state.database

    @app.get('/operations/master-reservations/new', response_class=HTMLResponse)
    def new(request: Request):
        context = context_for(database, request)
        company_id = working_company(context)
        with database.connect() as c:
            elements = c.execute(
                'SELECT id,name FROM setup_elements WHERE company_id=? AND active=1 ORDER BY name,id',
                (company_id,)).fetchall()
            people = c.execute('SELECT id,name FROM setup_person_types WHERE company_id=? AND active=1 ORDER BY name,id', (company_id,)).fetchall()
            addons = c.execute('SELECT id,name FROM setup_addons WHERE company_id=? AND active=1 ORDER BY name,id', (company_id,)).fetchall()
            customers = c.execute(
                'SELECT id,first_name,last_name FROM customer_records WHERE company_id=? AND active=1 '
                'ORDER BY last_name,first_name,id', (company_id,)).fetchall()
        body = ('<h1>New single-record Enquiry — development</h1>'
                '<p class="muted">This separate screen does not replace the existing booking workflow. '
                'Standard pricing and occupancy checks are connected; payment and full requirements are not yet available.</p>'
                '<form method="post"><label>Customer</label><select name="customer_id" required>')
        for item in customers:
            body += f'<option value="{int(item["id"])}">{esc(str(item["first_name"] or "") + " " + str(item["last_name"] or ""))}</option>'
        body += '</select><label>Elements (Ctrl-click to select more than one)</label><select name="element_ids" multiple size="8" required>'
        for item in elements:
            body += f'<option value="{int(item["id"])}">{esc(item["name"])}</option>'
        body += '</select><p class="muted">Each selected Element can have its own arrival and departure dates below. People and Add-ons are currently shared.</p>'
        for item in elements:
            eid = int(item['id'])
            body += (f'<fieldset><legend>{esc(item["name"])} — optional individual dates</legend>'
                     f'<label>Arrival</label><input type="date" name="arrival_{eid}">'
                     f'<label>Departure</label><input type="date" name="departure_{eid}"></fieldset>')
        body += '<h2>Individual Element occupancy</h2>'
        for item in elements:
            eid = int(item['id'])
            body += f'<fieldset><legend>{esc(item["name"])}</legend>'
            for person in people:
                pid = int(person['id'])
                body += f'<label>{esc(person["name"])}</label><input type="number" min="0" name="element_person_{eid}_{pid}" placeholder="Default">'
            body += '</fieldset>'
        body += '<h2>Default people</h2>'
        for person in people:
            body += f'<label>{esc(person["name"])}</label><input type="number" min="0" step="1" name="person_{int(person["id"])}" value="0">'
        body += '<h2>Add-ons</h2><p class="muted">Whole-stay and selected-day Add-ons. Enter dates as YYYY-MM-DD.</p>'
        for addon in addons:
            aid = int(addon['id'])
            body += f'<h3>{esc(addon["name"])}</h3>'
            options = {str(x['option_code']) for x in when_options(database, company_id, aid)}
            if 'selected_days' in options:
                body += (f'<label>When</label><select name="addon_when_{aid}">'
                         '<option value="every_day">Every day</option>'
                         '<option value="selected_days">Selected days</option></select>')
                if addon_person_mode(database, company_id, aid) == 'person_type':
                    body += (f'<label>Selected-day Person Type quantities</label><textarea name="selected_person_days_{aid}" '
                             'placeholder="YYYY-MM-DD, Person Type ID, quantity (one per line)"></textarea>')
                else:
                    body += (f'<label>Selected-day quantities</label><textarea name="selected_days_{aid}" '
                             'placeholder="YYYY-MM-DD, quantity (one per line)"></textarea>')
            if addon_person_mode(database, company_id, aid) == 'person_type':
                for person in people:
                    body += (f'<label>{esc(person["name"])} quantity</label>'
                             f'<input type="number" min="0" step="1" name="addon_person_{aid}_{int(person["id"])}" value="0">')
            else:
                body += f'<label>Quantity</label><input type="number" min="0" step="1" name="addon_{aid}" value="0">'
        body += '<h2>Individual Element Add-on quantities</h2><p class="muted">Leave blank to use the shared quantity; enter 0 to exclude an Add-on.</p>'
        for item in elements:
            eid = int(item['id'])
            body += f'<fieldset><legend>{esc(item["name"])}</legend>'
            for addon in addons:
                aid = int(addon['id'])
                options = {str(x['option_code']) for x in when_options(database, company_id, aid)}
                if 'selected_days' in options:
                    format_hint = 'YYYY-MM-DD, Person Type ID, quantity' if addon_person_mode(database, company_id, aid) == 'person_type' else 'YYYY-MM-DD, quantity'
                    body += f'<label>{esc(addon["name"])} — individual selected days</label><textarea name="element_selected_days_{eid}_{aid}" placeholder="{format_hint} (blank uses shared days)"></textarea>'
                if addon_person_mode(database, company_id, aid) == 'person_type':
                    for person in people:
                        pid = int(person['id'])
                        body += f'<label>{esc(addon["name"])} — {esc(person["name"])}</label><input type="number" min="0" name="element_addon_person_{eid}_{aid}_{pid}" placeholder="Shared">'
                else:
                    body += f'<label>{esc(addon["name"])}</label><input type="number" min="0" name="element_addon_{eid}_{aid}" placeholder="Shared">'
            body += '</fieldset>'
        body += ('<p class="muted">The full booking workflow remains available for complex Add-on requirements.</p>'
                 '<label>Arrival</label><input name="arrival_date" type="date" required>'
                 '<label>Departure</label><input name="departure_date" type="date" required>'
                 '<label>Notes</label><input name="notes" maxlength="1000">'
                 '<button type="submit">Create development Enquiry</button></form>')
        return HTMLResponse(layout('New single-record Enquiry', body, context))

    @app.post('/operations/master-reservations/new', response_class=HTMLResponse)
    async def create(request: Request):
        context = context_for(database, request)
        company_id = working_company(context)
        data = await form_data(request)
        require_csrf(context, data)
        try:
            customer_id = int(data.get('customer_id', ''))
            selected_ids = [int(item) for item in (await request.form()).getlist('element_ids')]
            if not selected_ids or len(selected_ids) != len(set(selected_ids)):
                raise ValueError('Choose distinct Elements.')
            arrival = date.fromisoformat(data.get('arrival_date', ''))
            departure = date.fromisoformat(data.get('departure_date', ''))
            if arrival >= departure:
                raise ValueError('Departure must be after arrival.')
        except (ValueError, TypeError):
            return HTMLResponse(layout('Invalid Enquiry',
                                        '<div class="error">Choose a valid customer, Element and date range.</div>',
                                        context), status_code=400)
        with database.connect() as c:
            customer = c.execute('SELECT id FROM customer_records WHERE company_id=? AND id=? AND active=1',
                                 (company_id, customer_id)).fetchone()
        if customer is None:
            return HTMLResponse(layout('Invalid customer',
                                        '<div class="error">Customer not found for this client.</div>',
                                        context), status_code=400)
        reference = 'ENQ-' + uuid4().hex[:12].upper()
        with database.connect() as c:
            elements_by_id = {
                int(row['id']): row for row in c.execute(
                    'SELECT id,element_type FROM setup_elements WHERE company_id=? AND active=1',
                    (company_id,)).fetchall()}
        if any(eid not in elements_by_id for eid in selected_ids):
            return HTMLResponse(layout('Invalid Element', '<div class="error">Element unavailable.</div>', context), status_code=400)
        with database.connect() as c:
            people = c.execute('SELECT id,name FROM setup_person_types WHERE company_id=? AND active=1', (company_id,)).fetchall()
        values = dict(data)
        # A standard quantity applies to the whole stay; make the intended mode
        # explicit rather than depending on an Add-on's default selection.
        with database.connect() as c:
            addon_ids = [int(row['id']) for row in c.execute(
                'SELECT id FROM setup_addons WHERE company_id=? AND active=1', (company_id,))]
        for addon_id in addon_ids:
            if values.get(f'addon_when_{addon_id}') == 'selected_days':
                person_mode = addon_person_mode(database, company_id, addon_id) == 'person_type'
                field = f'selected_person_days_{addon_id}' if person_mode else f'selected_days_{addon_id}'
                raw_lines = values.get(field, '').splitlines()
                if not raw_lines or not any(line.strip() for line in raw_lines):
                    return HTMLResponse(layout('Missing Add-on dates',
                        '<div class="error">Enter at least one selected day and quantity.</div>',
                        context), status_code=400)
                for line in raw_lines:
                    if not line.strip():
                        continue
                    parts = [part.strip() for part in line.split(',')]
                    try:
                        if len(parts) != (3 if person_mode else 2):
                            raise ValueError('Invalid entry')
                        day = date.fromisoformat(parts[0])
                        if person_mode:
                            person_id = int(parts[1])
                            with database.connect() as c:
                                valid_person = c.execute(
                                    'SELECT 1 FROM setup_person_types WHERE company_id=? AND id=? AND active=1',
                                    (company_id, person_id)).fetchone()
                            if valid_person is None:
                                raise ValueError('Unknown Person Type')
                        qty = int(parts[2 if person_mode else 1])
                        if day < arrival or day >= departure or qty < 0:
                            raise ValueError('Outside stay')
                    except (ValueError, TypeError):
                        return HTMLResponse(layout('Invalid Add-on day',
                            f'<div class="error">Invalid selected-day entry for Add-on {addon_id}.</div>',
                            context), status_code=400)
                    key = (f'addon_day_person_{addon_id}_{person_id}_{day.isoformat()}'
                           if person_mode else f'addon_day_{addon_id}_{day.isoformat()}')
                    if key in values:
                        return HTMLResponse(layout('Duplicate Add-on day',
                            '<div class="error">Each selected day must appear only once.</div>',
                            context), status_code=400)
                    values[key] = str(qty)
                values[f'addon_selected_{addon_id}'] = '1'
                continue
            if values.get(f'addon_{addon_id}', '0').strip() not in ('', '0') or any(
                key.startswith(f'addon_person_{addon_id}_') and value.strip() not in ('', '0')
                for key, value in values.items()):
                values[f'addon_when_{addon_id}'] = 'every_day'
        allocations = []
        for element_id in selected_ids:
            try:
                element_arrival = date.fromisoformat(data.get(f'arrival_{element_id}') or arrival.isoformat())
                element_departure = date.fromisoformat(data.get(f'departure_{element_id}') or departure.isoformat())
                if element_arrival >= element_departure:
                    raise ValueError('Invalid date range')
            except (ValueError, TypeError):
                return HTMLResponse(layout('Invalid Element dates',
                    f'<div class="error">Invalid dates for Element {element_id}.</div>', context), status_code=400)
            element_values = dict(values)
            element_values['arrival_date'] = element_arrival.isoformat()
            element_values['departure_date'] = element_departure.isoformat()
            for addon_id in addon_ids:
                if element_values.get(f'addon_when_{addon_id}') == 'selected_days':
                    for key, quantity in element_values.items():
                        if key.startswith((f'addon_day_{addon_id}_', f'addon_day_person_{addon_id}_')) and quantity not in ('', '0'):
                            service_date = key[-10:]
                            if service_date < element_arrival.isoformat() or service_date >= element_departure.isoformat():
                                return HTMLResponse(layout('Add-on dates outside Element stay',
                                    f'<div class="error">Selected Add-on dates must fall within every selected Element stay. Element {element_id} is incompatible.</div>',
                                    context), status_code=400)
            for addon_id in addon_ids:
                raw = data.get(f'element_selected_days_{element_id}_{addon_id}', '').strip()
                if not raw:
                    continue
                person_mode = addon_person_mode(database, company_id, addon_id) == 'person_type'
                for key in list(element_values):
                    if key.startswith((f'addon_day_{addon_id}_', f'addon_day_person_{addon_id}_')):
                        element_values.pop(key)
                for line in raw.splitlines():
                    if not line.strip():
                        continue
                    parts = [part.strip() for part in line.split(',')]
                    try:
                        if len(parts) != (3 if person_mode else 2):
                            raise ValueError('Invalid columns')
                        day = date.fromisoformat(parts[0])
                        qty = int(parts[-1])
                        if qty < 0 or not element_arrival <= day < element_departure:
                            raise ValueError('Invalid day or quantity')
                        if person_mode:
                            pid = int(parts[1])
                            if pid not in {int(p['id']) for p in people}:
                                raise ValueError('Invalid Person Type')
                            key = f'addon_day_person_{addon_id}_{pid}_{day.isoformat()}'
                        else:
                            key = f'addon_day_{addon_id}_{day.isoformat()}'
                        if key in element_values:
                            raise ValueError('Duplicate day')
                        element_values[key] = str(qty)
                    except (ValueError, TypeError):
                        return HTMLResponse(layout('Invalid Add-on days', f'<div class="error">Invalid individual Add-on dates for Element {element_id}.</div>', context), status_code=400)
                element_values[f'addon_when_{addon_id}'] = 'selected_days'
                element_values[f'addon_selected_{addon_id}'] = '1'
            for person_id in [int(person['id']) for person in people]:
                override = data.get(f'element_person_{element_id}_{person_id}', '').strip()
                if override != '':
                    element_values[f'person_{person_id}'] = override
            for addon_id in addon_ids:
                override = data.get(f'element_addon_{element_id}_{addon_id}', '').strip()
                if override != '':
                    element_values[f'addon_{addon_id}'] = override
                    if override == '0':
                        element_values.pop(f'addon_selected_{addon_id}', None)
                    else:
                        element_values[f'addon_selected_{addon_id}'] = '1'
                for person in people:
                    pid = int(person['id'])
                    person_override = data.get(f'element_addon_person_{element_id}_{addon_id}_{pid}', '').strip()
                    if person_override != '':
                        element_values[f'addon_person_{addon_id}_{pid}'] = person_override
                        element_values[f'addon_selected_{addon_id}'] = '1'
            element_values['element_id'] = str(element_id)
            element_values['element_type'] = str(elements_by_id[element_id]['element_type'])
            calculation, errors, message = _calculate(database, company_id, element_values)
            if message or calculation is None or errors:
                explanation = message or '; '.join(map(str, errors)) or 'Cannot calculate this Enquiry.'
                return HTMLResponse(layout('Pricing or requirements incomplete',
                                            f'<div class="error">Element {element_id}: {esc(explanation)}</div>',
                                            context), status_code=400)
            snapshot = {key: calculation[key] for key in (
                'element_type', 'element_name', 'year', 'nights', 'people_total',
                'people_counts', 'selected_addons', 'addon_counts', 'addon_days',
                'addon_people', 'addon_person_days', 'addon_when', 'lines', 'base_amount',
                'discount_amount', 'discount_rule', 'total')}
            allocations.append({'element_id': element_id, 'arrival_date': element_arrival.isoformat(),
                                'departure_date': element_departure.isoformat(), 'total_amount': calculation['total'],
                                'pricing_snapshot_json': json.dumps(snapshot, separators=(',', ':'))})
        try:
            rid = create_enquiry(database, company_id, reference, customer_id, allocations,
                                 iso_now(), notes=data.get('notes', '')[:1000])
        except ReservationError as exc:
            return HTMLResponse(layout('Enquiry unavailable',
                                        f'<div class="error">{esc(str(exc))}</div>', context),
                                status_code=409)
        return RedirectResponse(f'/operations/master-reservations/{rid}', status_code=303)
