from __future__ import annotations

import json

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from .app import COOKIE_NAME, form_data
from .setup015 import register_setup015
from .setup015_core import require_csrf
from .webv1_addon_person import initialise_addon_person
from .webv1_addon_popup import initialise_addon_popup, register_addon_popup_routes
from .webv1_addon_when import initialise_addon_when, register_addon_when_routes
from .webv1_availability import initialise_availability, register_availability_routes
from .webv1_basket import register_basket_routes
from .webv1_booking_progress import register_booking_progress_routes
from .webv1_booking_requirements import (
    _snapshot_hold_requirements,
    initialise_booking_requirements,
    register_booking_requirement_routes,
)
from .webv1_booking_requirements_core import register_booking_requirements_core
from .webv1_booking_requirements_refinements import (
    install_booking_requirements_refinements,
    register_booking_requirements_refinement_routes,
)
from .webv1_booking_requirements_ui import install_booking_requirements_ui
from .webv1_booking_requirements_v3 import (
    install_booking_requirements_v3_form,
    register_booking_requirements_v3,
)
from .webv1_booking_status import initialise_booking_statuses, register_booking_status_routes
from .webv1_bookings import initialise_booking_workflow, register_booking_routes
from .webv1_calendar_edit_semantics import install_calendar_edit_semantics
from .webv1_calendar_refresh import install_calendar_expiry_refresh
from .webv1_duration_display import install_duration_display
from .webv1_edit_action_box import install_edit_action_box
from .webv1_feature_booking_ui import install_feature_booking_ui
from .webv1_feature_wording import install_feature_wording
from .webv1_features_extras_v2 import initialise_features_extras, register_features_extras_routes
from .webv1_hold_settings import (
    create_or_replace_hold as create_timed_hold,
    initialise_hold_settings,
    install_hold_timing,
    register_hold_settings_routes,
)
from .webv1_ordering import initialise_ordering, register_ordering_routes
from .webv1_pricing_usability import (
    initialise_pricing_usability,
    install_pricing_calculation_transparency,
    register_pricing_usability_routes,
)
from . import webv1_availability, webv1_calendar_v2
from .webv1_calendar_v5 import register_calendar_v5_routes
from .webv1_core import initialise_web_v1
from .webv1_customers import register_customer_routes
from .webv1_enquiries import register_enquiry_routes
from .webv1_enquiry_builder import register_enquiry_builder_routes
from .webv1_routes import register_web_v1_routes
from .webv1_status_availability import install_status_aware_availability
from .webv1_user_display import install_user_display_rules

__all__ = ['initialise_web_v1', 'register_web_v1']


def register_web_v1(app) -> None:
    """Register the proven Setup engine first, then the permanent Web V1 lifecycle."""
    register_setup015(app)
    initialise_web_v1(app.state.database)
    initialise_ordering(app.state.database)
    initialise_addon_when(app.state.database)
    initialise_addon_person(app.state.database)
    initialise_availability(app.state.database)
    initialise_booking_statuses(app.state.database)
    initialise_booking_workflow(app.state.database)
    initialise_hold_settings(app.state.database)
    initialise_pricing_usability(app.state.database)
    initialise_addon_popup(app.state.database)
    initialise_booking_requirements(app.state.database)
    initialise_features_extras(app.state.database)
    install_status_aware_availability()
    install_hold_timing()
    install_pricing_calculation_transparency()
    register_pricing_usability_routes(app)
    webv1_calendar_v2.json = json
    register_web_v1_routes(app)
    register_ordering_routes(app)
    register_customer_routes(app)
    register_enquiry_routes(app)
    register_enquiry_builder_routes(app)
    register_booking_routes(app)
    register_addon_when_routes(app)
    register_booking_status_routes(app)
    register_hold_settings_routes(app)
    register_availability_routes(app)
    register_basket_routes(app)
    register_booking_progress_routes(app)
    register_addon_popup_routes(app)
    register_booking_requirement_routes(app)
    register_booking_requirements_refinement_routes(app)
    register_booking_requirements_v3(app)
    register_booking_requirements_core(app)
    register_features_extras_routes(app)
    register_calendar_v5_routes(app)
    webv1_calendar_v2.register_calendar_v2_routes(app)
    install_calendar_edit_semantics(app)
    install_calendar_expiry_refresh(app)
    install_user_display_rules(app)
    install_edit_action_box(app)
    install_duration_display(app)
    install_booking_requirements_ui(app)
    install_booking_requirements_refinements(app)
    install_booking_requirements_v3_form(app)
    install_feature_booking_ui(app)
    install_feature_wording(app)

    app.router.routes[:] = [
        route for route in app.router.routes
        if getattr(route, 'path', None) not in {'/availability/calendar', '/availability/hold'}
    ]

    @app.post('/availability/hold')
    async def availability_hold_with_client_timing(request: Request):
        context, cid = webv1_availability._session_company(app.state.database, request)
        data = await form_data(request)
        require_csrf(context, data)
        token = request.cookies.get(COOKIE_NAME, '')
        try:
            element_id = int(data.get('element_id', ''))
            hold = create_timed_hold(
                app.state.database,
                context,
                cid,
                token,
                element_id,
                data.get('arrival_date', ''),
                data.get('departure_date', ''),
            )
            with app.state.database.connect() as c:
                working = c.execute('SELECT lead_name FROM booking_requirement_sessions WHERE company_id=? AND session_token=?', (cid, token)).fetchone()
                c.execute('UPDATE element_holds SET lead_name=? WHERE id=? AND company_id=? AND session_token=?', (str(working['lead_name'] or '') if working else '', int(hold['id']), cid, token))
            _snapshot_hold_requirements(app.state.database, cid, token, int(hold['id']))
        except (TypeError, ValueError) as exc:
            return JSONResponse({'ok': False, 'error': str(exc)}, status_code=409)
        return JSONResponse({'ok': True, 'hold': hold})

    @app.get('/availability/calendar')
    def availability_calendar_compat(request: Request):
        token = request.cookies.get(COOKIE_NAME, '')
        context = app.state.database.session_context(token) if token else None
        company_id = None
        if context:
            company_id = context['acting_company_id'] if context['role'] == 'supervisor' else context['company_id']
        if company_id and token:
            cid = int(company_id)
            with app.state.database.connect() as c:
                hold_ids = [int(r['id']) for r in c.execute('SELECT id FROM element_holds WHERE session_token=? AND company_id=?', (token, cid)).fetchall()]
                for hold_id in hold_ids:
                    c.execute('DELETE FROM hold_requirement_people WHERE hold_id=?', (hold_id,))
                    c.execute('DELETE FROM hold_requirement_addons WHERE hold_id=?', (hold_id,))
                c.execute('DELETE FROM element_holds WHERE session_token=? AND company_id=?', (token, cid))
                c.execute('DELETE FROM booking_requirement_people WHERE session_token=? AND company_id=?', (token, cid))
                c.execute('DELETE FROM booking_requirement_addons WHERE session_token=? AND company_id=?', (token, cid))
                c.execute('''INSERT INTO booking_requirement_sessions(session_token,company_id,ready,arrival_date,departure_date,lead_name,updated_at)
                             VALUES (?,?,0,'','','',CURRENT_TIMESTAMP)
                             ON CONFLICT(session_token,company_id) DO UPDATE SET ready=0,arrival_date='',departure_date='',lead_name='',updated_at=CURRENT_TIMESTAMP''', (token, cid))
        return RedirectResponse('/availability/start', 303)
