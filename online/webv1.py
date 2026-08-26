from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import RedirectResponse

from .setup015 import register_setup015
from .webv1_addon_person import initialise_addon_person
from .webv1_addon_popup import initialise_addon_popup, register_addon_popup_routes
from .webv1_addon_when import initialise_addon_when, register_addon_when_routes
from .webv1_availability import initialise_availability, register_availability_routes
from .webv1_basket import register_basket_routes
from .webv1_booking_status import initialise_booking_statuses, register_booking_status_routes
from .webv1_bookings import initialise_booking_workflow, register_booking_routes
from .webv1_calendar_edit_semantics import install_calendar_edit_semantics
from .webv1_calendar_refresh import install_calendar_expiry_refresh
from .webv1_hold_settings import initialise_hold_settings, install_hold_timing, register_hold_settings_routes
from .webv1_pricing_usability import (
    initialise_pricing_usability,
    install_pricing_calculation_transparency,
    register_pricing_usability_routes,
)
from . import webv1_calendar_v2
from .webv1_calendar_v5 import register_calendar_v5_routes
from .webv1_core import initialise_web_v1
from .webv1_customers import register_customer_routes
from .webv1_enquiries import register_enquiry_routes
from .webv1_enquiry_builder import register_enquiry_builder_routes
from .webv1_routes import register_web_v1_routes
from .webv1_status_availability import install_status_aware_availability

__all__ = ['initialise_web_v1', 'register_web_v1']


def register_web_v1(app) -> None:
    """Register the proven Setup engine first, then the permanent Web V1 lifecycle."""
    register_setup015(app)
    initialise_web_v1(app.state.database)
    initialise_addon_when(app.state.database)
    initialise_addon_person(app.state.database)
    initialise_availability(app.state.database)
    initialise_booking_statuses(app.state.database)
    initialise_booking_workflow(app.state.database)
    initialise_hold_settings(app.state.database)
    initialise_pricing_usability(app.state.database)
    initialise_addon_popup(app.state.database)
    install_status_aware_availability()
    install_hold_timing()
    install_pricing_calculation_transparency()
    register_pricing_usability_routes(app)
    webv1_calendar_v2.json = json
    register_web_v1_routes(app)
    register_customer_routes(app)
    register_enquiry_routes(app)
    register_enquiry_builder_routes(app)
    register_booking_routes(app)
    register_addon_when_routes(app)
    register_booking_status_routes(app)
    register_hold_settings_routes(app)
    register_availability_routes(app)
    register_basket_routes(app)
    register_addon_popup_routes(app)
    register_calendar_v5_routes(app)
    webv1_calendar_v2.register_calendar_v2_routes(app)
    install_calendar_edit_semantics(app)
    install_calendar_expiry_refresh(app)

    @app.get('/availability/calendar')
    def availability_calendar_compat(request: Request):
        target = '/availability/calendar-v2'
        if request.url.query:
            target += '?' + request.url.query
        return RedirectResponse(target, 303)