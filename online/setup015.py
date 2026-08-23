from __future__ import annotations

from .setup015_annual import register_annual_routes
from .setup015_calculator import register_calculator_routes
from .setup015_catalogue import register_catalogue_routes
from .setup015_core import copy_previous_year, initialise_setup015
from .setup015_maintenance import register_setup_maintenance_routes

__all__ = ["copy_previous_year", "initialise_setup015", "register_setup015"]


def register_setup015(app) -> None:
    initialise_setup015(app.state.database)
    # Register maintenance GET routes first so they can enhance the proven Setup pages
    # without replacing the underlying save/calculation engine.
    register_setup_maintenance_routes(app)
    register_catalogue_routes(app)
    register_annual_routes(app)
    register_calculator_routes(app)
