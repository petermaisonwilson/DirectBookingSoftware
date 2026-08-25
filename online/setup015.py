from __future__ import annotations

from .setup015_annual import register_annual_routes
from .setup015_calculator import register_calculator_routes
from .setup015_catalogue import register_catalogue_routes
from .setup015_core import copy_previous_year, initialise_setup015
from .setup015_element_availability_page import register_element_availability_page
from .setup015_maintenance import register_setup_maintenance_routes
from .setup015_year_audit import register_year_audit_routes

__all__ = ["copy_previous_year", "initialise_setup015", "register_setup015"]


def register_setup015(app) -> None:
    initialise_setup015(app.state.database)
    # Register the enhanced Year / Setup Audit workflow before the legacy Year
    # routes so blank-year and adjusted copy-year behaviour has one safe source.
    register_year_audit_routes(app)
    # Register the enhanced Elements page first so Availability appears alongside
    # Edit / Deactivate / Delete while the proven Setup save engine remains intact.
    register_element_availability_page(app)
    register_setup_maintenance_routes(app)
    register_catalogue_routes(app)
    register_annual_routes(app)
    register_calculator_routes(app)
