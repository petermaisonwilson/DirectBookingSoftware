from __future__ import annotations

import json

from fastapi.responses import RedirectResponse

from .setup015 import register_setup015
from .webv1_addon_person import initialise_addon_person
from .webv1_addon_when import initialise_addon_when, register_addon_when_routes
from .webv1_availability import initialise_availability, register_availability_routes
from .webv1_booking_status import initialise_booking_statuses, register_booking_status_routes
from . import webv1_calendar_v2
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
    install_status_aware_availability()
    # Calendar v2 uses json.dumps while rendering safe JavaScript literals.
    webv1_calendar_v2.json = json
    register_web_v1_routes(app)
    register_customer_routes(app)
    register_enquiry_routes(app)
    register_enquiry_builder_routes(app)
    register_addon_when_routes(app)
    register_booking_status_routes(app)
    register_availability_routes(app)
    webv1_calendar_v2.register_calendar_v2_routes(app)

    @app.get('/availability/calendar')
    def availability_calendar_compat():
        return RedirectResponse('/availability/calendar-v2', 303)
