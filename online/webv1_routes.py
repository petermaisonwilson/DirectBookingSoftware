from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from .app import esc, layout
from .setup015_core import context_for, working_company
from .webv1_core import lifecycle_counts
from .webv1_ordering import sortable_menu_html


def register_web_v1_routes(app) -> None:
    database = app.state.database

    @app.get('/operations', response_class=HTMLResponse)
    def operations_home(request: Request):
        context = context_for(database, request)
        company_id = working_company(context)
        if not company_id:
            raise HTTPException(status_code=403, detail='Select a client in Support Mode first')
        company = database.company(company_id)
        counts = lifecycle_counts(database, company_id)
        cards = [
            ('availability', '<h2>Availability Calendar</h2><p>See free, booked, closed and temporarily held Elements by date.</p><p><a class="button" href="/availability/calendar">Open Availability Calendar</a> <a class="button secondary" href="/company/hold-settings">Hold timing</a></p>'),
            ('customers', f'<h2>Customers</h2><p><strong>{counts["customer_records"]}</strong> customer record(s)</p><p><a class="button" href="/operations/customers">Open Client Register</a></p>'),
            ('enquiries', f'<h2>Enquiries</h2><p><strong>{counts["enquiries"]}</strong> enquiry record(s)</p><p><a class="button" href="/operations/enquiries">Open Enquiry Search</a></p><p><a class="button secondary" href="/operations/customers">Find Customer / New Enquiry</a></p>'),
            ('offers', f'<h2>Offers</h2><p><strong>{counts["offers"]}</strong> offer record(s)</p><p>Draft, sent, accepted, declined and expired offers with frozen price snapshots.</p>'),
            ('bookings', f'<h2>Bookings</h2><p><strong>{counts["bookings"]}</strong> booking record(s)</p><p>Confirmed bookings, payments, workflow status and permanent history.</p><p><a class="button" href="/operations/bookings">Open Bookings</a></p>'),
            ('arrivals', f'<h2>Arrivals / Self Check-in</h2><p><strong>{counts["arrivals"]}</strong> arrival record(s)</p><p>Supports both booked arrivals and walk-in/unbooked arrivals. Self check-in will build on this same Booking record.</p>'),
            ('setup', '<h2>Setup</h2><p>Elements, seasons, Person pricing, Feature / Extra rules, Booking Statuses and closures.</p><p><a class="button" href="/setup">Open Setup</a></p>'),
        ]
        body = f'''<h1>{esc(company['name'])} — Operations</h1>
        <div class="card"><p><strong>Web V1</strong></p><p>Day-to-day availability, enquiries and confirmed bookings now share the same live inventory and status workflow.</p></div>'''
        body += sortable_menu_html(database, context, 'operations', cards)
        return layout('Operations', body, context)

    @app.get('/operations/foundation', response_class=HTMLResponse)
    def operations_foundation(request: Request):
        context = context_for(database, request)
        company_id = working_company(context)
        if not company_id:
            raise HTTPException(status_code=403, detail='Select a client in Support Mode first')
        body = '''<h1>Web V1 booking lifecycle</h1><div class="card"><h2>Permanent record flow</h2><p><strong>Customer → Enquiry → Offer → Booking → Arrival / Self Check-in</strong></p><p>A Booking can contain one or more dated Elements. Each Booking Element stores its own Person and Add-on snapshot so later Setup price changes cannot alter an existing confirmed booking.</p></div><div class="card"><h2>Walk-in / unbooked arrival</h2><p>An Arrival is deliberately allowed to begin without an existing Booking, forming the foundation for evening self check-in.</p></div><div class="card"><h2>Client isolation</h2><p>Every operational record carries a Client ID. Operators see only their own Client. A Supervisor sees a Client's data only while visibly in Support Mode.</p></div>'''
        return layout('Web V1 foundation', body, context)
