from __future__ import annotations

from .setup015 import register_setup015
from .webv1_core import initialise_web_v1
from .webv1_customers import register_customer_routes
from .webv1_enquiries import register_enquiry_routes
from .webv1_enquiry_builder import register_enquiry_builder_routes
from .webv1_routes import register_web_v1_routes

__all__ = ['initialise_web_v1', 'register_web_v1']


def register_web_v1(app) -> None:
    """Register the proven Setup engine first, then the permanent Web V1 lifecycle."""
    register_setup015(app)
    initialise_web_v1(app.state.database)
    register_web_v1_routes(app)
    register_customer_routes(app)
    register_enquiry_routes(app)
    register_enquiry_builder_routes(app)
