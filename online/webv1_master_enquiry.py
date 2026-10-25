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
        body += '</select><label>Element</label><select name="element_id" required>'
        for item in elements:
            body += f'<option value="{int(item["id"])}">{esc(item["name"])}</option>'
        body += '</select><h2>People</h2>'
        for person in people:
            body += f'<label>{esc(person["name"])}</label><input type="number" min="0" step="1" name="person_{int(person["id"])}" value="0">'
        body += '<h2>Add-ons</h2><p class="muted">Whole-stay quantities and Person Type Add-ons. Selected-day options remain in the full workflow.</p>'
        for addon in addons:
            aid = int(addon['id'])
            body += f'<h3>{esc(addon["name"])}</h3>'
            if addon_person_mode(database, company_id, aid) == 'person_type':
                for person in people:
                    body += (f'<label>{esc(person["name"])} quantity</label>'
                             f'<input type="number" min="0" step="1" name="addon_person_{aid}_{int(person["id"])}" value="0">')
            else:
                body += f'<label>Quantity</label><input type="number" min="0" step="1" name="addon_{aid}" value="0">'
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
            element_id = int(data.get('element_id', ''))
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
            element = c.execute('SELECT element_type FROM setup_elements WHERE company_id=? AND id=? AND active=1',
                                (company_id, element_id)).fetchone()
        if element is None:
            return HTMLResponse(layout('Invalid Element', '<div class="error">Element unavailable.</div>', context), status_code=400)
        values = dict(data)
        # A standard quantity applies to the whole stay; make the intended mode
        # explicit rather than depending on an Add-on's default selection.
        with database.connect() as c:
            addon_ids = [int(row['id']) for row in c.execute(
                'SELECT id FROM setup_addons WHERE company_id=? AND active=1', (company_id,))]
        for addon_id in addon_ids:
            if values.get(f'addon_{addon_id}', '0').strip() not in ('', '0') or any(
                key.startswith(f'addon_person_{addon_id}_') and value.strip() not in ('', '0')
                for key, value in values.items()):
                values[f'addon_when_{addon_id}'] = 'every_day'
        values['element_type'] = str(element['element_type'])
        calculation, errors, message = _calculate(database, company_id, values)
        if message or calculation is None:
            return HTMLResponse(layout('Pricing or requirements incomplete',
                                        f'<div class="error">{esc(message or "Cannot calculate this Enquiry.")}</div>',
                                        context), status_code=400)
        snapshot = {key: calculation[key] for key in (
            'element_type', 'element_name', 'year', 'nights', 'people_total',
            'people_counts', 'selected_addons', 'addon_counts', 'addon_days',
            'addon_people', 'addon_person_days', 'lines', 'base_amount',
            'discount_amount', 'discount_rule', 'total')}
        allocation = {'element_id': element_id, 'arrival_date': arrival.isoformat(),
                      'departure_date': departure.isoformat(), 'total_amount': calculation['total'],
                      'pricing_snapshot_json': json.dumps(snapshot, separators=(',', ':'))}
        try:
            rid = create_enquiry(database, company_id, reference, customer_id, [allocation],
                                 iso_now(), notes=data.get('notes', '')[:1000])
        except ReservationError as exc:
            return HTMLResponse(layout('Enquiry unavailable',
                                        f'<div class="error">{esc(str(exc))}</div>', context),
                                status_code=409)
        return RedirectResponse(f'/operations/master-reservations/{rid}', status_code=303)
