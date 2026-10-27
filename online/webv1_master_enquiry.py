"""Compatibility entry for the retired standalone development creation page.

All new operational reservations start through the complete requirements/basket
workflow. Existing standalone records remain readable; missing business details
are never inferred in order to manufacture a confirmation or payment account.
"""
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .app import layout
from .setup015_core import context_for, require_csrf
from .app import form_data


def register_master_enquiry_routes(app):
    database = app.state.database

    @app.get('/operations/master-reservations/new')
    def new(request: Request):
        context_for(database, request)
        return RedirectResponse('/availability/start', status_code=303)

    @app.post('/operations/master-reservations/new', response_class=HTMLResponse)
    async def create(request: Request):
        context = context_for(database, request)
        data = await form_data(request)
        require_csrf(context, data)
        return HTMLResponse(layout('Continue with Booking Requirements',
            '<h1>Continue with Booking Requirements</h1>'
            '<p>The standalone development form has been retired. No reservation was created.</p>'
            '<p><a class="button" href="/availability/start">Start the complete booking workflow</a></p>',
            context), status_code=409)
