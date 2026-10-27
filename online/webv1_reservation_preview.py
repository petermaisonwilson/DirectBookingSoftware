"""Read-only preview of the new single-record reservation screens.

Kept separate from the existing operational screens until the full lifecycle,
finance and history are migrated.
"""
import json

from fastapi import Request
from fastapi.responses import HTMLResponse

from .app import esc, layout
from .setup015_core import context_for, working_company
from .webv1_reservation_service import list_reservations, reservation_summary

_STATES = {
    'ENQUIRY_HELD': 'Holding Space',
    'ENQUIRY_RELEASED': 'Released',
    'CONFIRMED': 'Confirmed',
    'CANCELLED': 'Cancelled',
}


def register_reservation_preview_routes(app):
    database = app.state.database

    @app.get('/operations/master-reservations', response_class=HTMLResponse)
    def register(request: Request):
        context = context_for(database, request)
        company_id = working_company(context)
        selected = request.query_params.get('status', '').strip()
        if selected and selected not in _STATES:
            return HTMLResponse(layout('Invalid status', '<div class="error">Invalid reservation status.</div>', context), status_code=400)
        records = list_reservations(database, company_id, state=selected or None)
        body = '<h1>Single-record reservations — preview</h1>'
        body += '<p class="muted">Read-only development view. Existing Enquiry and Booking screens remain in use.</p>'
        body += '<form method="get"><label for="status">Status</label><select name="status" id="status">'
        body += '<option value="">All statuses</option>'
        for code, label in _STATES.items():
            body += f'<option value="{esc(code)}" {"selected" if code == selected else ""}>{esc(label)}</option>'
        body += '</select><button type="submit">Filter</button></form>'
        body += '<table><tr><th>Reference</th><th>Status</th><th>Total</th><th>Created</th></tr>'
        for item in records:
            rid = int(item['id'])
            href = f'/operations/reservations/{rid}'
            body += (f'<tr><td><a href="{href}">{esc(item["reference"])}</a></td>'
                     f'<td>{esc(_STATES.get(item["state"], item["state"]))}</td>'
                     f'<td>{float(item["total_amount"] or 0):.2f} {esc(item["currency"])}</td>'
                     f'<td>{esc(item["created_at"])}</td></tr>')
        body += '</table><p><a href="/operations/master-reservations/new">Create development Enquiry</a></p>'
        return HTMLResponse(layout('Single-record reservations', body, context))

    @app.get('/operations/master-reservations/{reservation_id:int}', response_class=HTMLResponse)
    def detail(reservation_id: int, request: Request):
        context = context_for(database, request)
        company_id = working_company(context)
        result = reservation_summary(database, company_id, reservation_id)
        if result is None:
            return HTMLResponse(layout('Reservation not found',
                                        '<div class="error">Reservation not found for this client.</div>',
                                        context), status_code=404)
        record = result['reservation']
        body = (f'<p><a href="/operations/master-reservations">Back to reservations</a></p>'
                f'<h1>Reservation {esc(record["reference"])}</h1>'
                f'<p><strong>Status:</strong> {esc(_STATES.get(record["state"], record["state"]))}</p>'
                f'<p><strong>Total:</strong> {float(record["total_amount"] or 0):.2f} {esc(record["currency"])}</p>'
                '<h2>Elements</h2><table><tr><th>Element</th><th>Arrival</th><th>Departure</th><th>Total</th></tr>')
        for item in result['elements']:
            body += (f'<tr><td>{esc(item.get("element_name") or item["element_id"])}</td>'
                     f'<td>{esc(item["arrival_date"])}</td><td>{esc(item["departure_date"])}</td>'
                     f'<td>{float(item["total_amount"] or 0):.2f}</td></tr>')
        body += '</table><h2>Price breakdown</h2>'
        for item in result['elements']:
            try:
                snapshot = json.loads(item.get('pricing_snapshot_json') or '{}')
            except (ValueError, TypeError):
                snapshot = {}
            body += f'<h3>{esc(item.get("element_name") or item["element_id"])}</h3>'
            body += '<table><tr><th>Item</th><th>Calculation</th><th>Amount</th></tr>'
            for line in snapshot.get('lines', []):
                if not isinstance(line, dict):
                    continue
                body += (f'<tr><td>{esc(line.get("item", ""))}</td>'
                         f'<td>{esc(line.get("rule", ""))}</td>'
                         f'<td>{float(line.get("amount") or 0):.2f}</td></tr>')
            body += '</table>'
        body += '<h2>Reservation history</h2><table><tr><th>When</th><th>Action</th><th>By</th><th>Reason</th></tr>'
        for event in result['history']:
            body += (f'<tr><td>{esc(event["created_at"])}</td>'
                     f'<td>{esc(event["event_type"])}</td>'
                     f'<td>{esc(event["actor"])}</td>'
                     f'<td>{esc(event["reason"])}</td></tr>')
        body += '</table><p class="muted">Read-only development view; payments are not yet connected.</p>'
        return HTMLResponse(layout('Reservation detail', body, context))
