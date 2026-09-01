from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from .app import esc, layout
from .setup015_core import context_for, working_company
from .webv1_core import lifecycle_counts


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
        body = f'''<h1>{esc(company['name'])} — Operations</h1>
        <div class="card"><p><strong>Web V1</strong></p>
        <p>This is the permanent day-to-day booking system. Client Register, Enquiries, Setup maintenance and live Element availability are working; the visual Availability Calendar is now available for day-to-day planning.</p></div>
        <div class="grid">
          <div class="card"><h2>Availability Calendar</h2><p>See free, booked, closed and temporarily held Elements by date. Change Element Type or dates and select an available Element.</p><p><a class="button" href="/availability/calendar">Open Availability Calendar</a></p></div>
          <div class="card"><h2>Customers</h2><p><strong>{counts['customer_records']}</strong> customer record(s)</p><p>Find returning customers or create a new Customer.</p><p><a class="button" href="/operations/customers">Open Client Register</a></p></div>
          <div class="card"><h2>Enquiries</h2><p><strong>{counts['enquiries']}</strong> enquiry record(s)</p><p>Search all enquiries by customer, dates, status or source.</p><p><a class="button" href="/operations/enquiries">Open Enquiry Search</a></p><p><a class="button secondary" href="/operations/customers">Find Customer / New Enquiry</a></p></div>
          <div class="card"><h2>Offers</h2><p><strong>{counts['offers']}</strong> offer record(s)</p><p>Draft, sent, accepted, declined and expired offers with frozen price snapshots.</p></div>
          <div class="card"><h2>Bookings</h2><p><strong>{counts['bookings']}</strong> booking record(s)</p><p>Confirmed bookings with Elements, Persons and Add-ons stored as booking snapshots.</p></div>
          <div class="card"><h2>Arrivals / Self Check-in</h2><p><strong>{counts['arrivals']}</strong> arrival record(s)</p><p>Supports both booked arrivals and walk-in/unbooked arrivals. Self check-in tokens are part of the database foundation.</p></div>
          <div class="card"><h2>Setup</h2><p>The proven Element Types, Elements, seasons, Person pricing, occupancy, Add-on rules and Element closures remain available.</p><p><a class="button" href="/setup">Open Setup</a></p></div>
        </div>'''
        return layout('Operations', body, context)

    @app.get('/operations/foundation', response_class=HTMLResponse)
    def operations_foundation(request: Request):
        context = context_for(database, request)
        company_id = working_company(context)
        if not company_id:
            raise HTTPException(status_code=403, detail='Select a client in Support Mode first')
        body = '''<h1>Web V1 booking lifecycle</h1>
        <div class="card"><h2>Permanent record flow</h2>
        <p><strong>Customer → Enquiry → Offer → Booking → Arrival / Self Check-in</strong></p>
        <p>A Booking can contain one or more dated Elements. Each Booking Element stores its own Person and Add-on snapshot so later Setup price changes cannot alter an existing confirmed booking.</p></div>
        <div class="card"><h2>Walk-in / unbooked arrival</h2>
        <p>An Arrival is deliberately allowed to begin without an existing Booking. That is the foundation for the evening self check-in flow we will build later: identify availability, collect guest details, create the booking and check the guest in without an operator needing to be present.</p></div>
        <div class="card"><h2>Client isolation</h2>
        <p>Every operational record carries a Client ID. Operators see only their own Client. A Supervisor sees a Client's data only while visibly in Support Mode.</p></div>'''
        return layout('Web V1 foundation', body, context)
