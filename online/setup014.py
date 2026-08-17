from __future__ import annotations

from .setup014_annual import register_annual_routes
from .setup014_catalogue import register_catalogue_routes
from .setup014_core import copy_previous_year, initialise_setup014

__all__ = ["copy_previous_year", "initialise_setup014", "register_setup014"]


def register_setup014(app) -> None:
    initialise_setup014(app.state.database)
    register_catalogue_routes(app)
    register_annual_routes(app)
