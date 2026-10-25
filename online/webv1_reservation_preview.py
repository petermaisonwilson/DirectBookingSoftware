"""Read-only preview of the new single-record reservation screens.

Kept separate from the existing operational screens until the full lifecycle,
finance and history are migrated.
"""
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
        records = list_reservations(database, company_id)
        body = '<h1>Single-record reservations — preview</h1>'
        body += '<p class="muted">Read-only development view. Existing Enquiry and Booking screens remain in use.</p>'
        body += '<table><tr><th>Reference</th><th>Status</th><th>Total</th><th>Created</th></tr>'
        for item in records:
            rid = int(item['id'])
            href = f'/operations/master-reservations/{rid}'
            body += (f'<tr><td><a href="{href}">{esc(item["reference"])}</a></td>'
                     f'<td>{esc(_STATES.get(item["state"], item["state"]))}</td>'
                     f'<td>{float(item["total_amount"] or 0):.2f} {esc(item["currency"])}</td>'
                     f'<td>{esc(item["created_at"])}</td></tr>')
        body += '</table>'
        return HTMLResponse(layout('Single-record reservations', body, context))

    @app.get('/operations/master-reservations/{reservation_id}', response_class=HTMLResponse)
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
                '<h2>Elements</h2><table><tr><th>Element ID</th><th>Arrival</th><th>Departure</th><th>Total</th></tr>')
        for item in result['elements']:
            body += (f'<tr><td>{int(item["element_id"])}</td>'
                     f'<td>{esc(item["arrival_date"])}</td><td>{esc(item["departure_date"])}</td>'
                     f'<td>{float(item["total_amount"] or 0):.2f}</td></tr>')
        body += '</table><p class="muted">Read-only development view; payments and history are not yet connected.</p>'
        return HTMLResponse(layout('Reservation detail', body, context))
