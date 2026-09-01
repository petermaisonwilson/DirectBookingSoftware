from __future__ import annotations

from .setup015_annual import register_annual_routes
from .setup015_calculator import register_calculator_routes
from .setup015_catalogue import register_catalogue_routes
from .setup015_core import copy_previous_year, initialise_setup015
from .setup015_element_availability_page import register_element_availability_page
from .setup015_elements_no_base_price import register_elements_no_base_price
from .setup015_maintenance import register_setup_maintenance_routes
from .setup015_year_actions import register_year_action_routes
from .setup015_year_audit import register_year_audit_routes
from .setup015_year_delete_page import register_year_delete_page

__all__ = ["copy_previous_year", "initialise_setup015", "register_setup015"]


def register_setup015(app) -> None:
    initialise_setup015(app.state.database)
    # Canonical year actions preserve the old "copy latest previous year" call
    # while also supporting explicit source-year selection and price adjustment.
    register_year_action_routes(app)
    # Show protected Delete Year controls on the same canonical Years screen.
    # Register before the audit page so this GET route owns /setup/years.
    register_year_delete_page(app)
    register_year_audit_routes(app)
    # Element creation/editing no longer contains a Base Price. Actual prices are
    # explicitly maintained in Seasonal Pricing; the legacy DB column remains
    # only for backwards compatibility and is not used by the booking engine.
    register_elements_no_base_price(app)
    # Legacy/enhanced routes remain registered underneath for compatibility.
    register_element_availability_page(app)
    register_setup_maintenance_routes(app)
    register_catalogue_routes(app)
    register_annual_routes(app)
    register_calculator_routes(app)
