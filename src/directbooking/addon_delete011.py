from __future__ import annotations

from . import addon_model
from . import addon_setup
from .addon_inheritance import ensure_group_addon_schema
from .database import Database


def apply_addon_delete_safety() -> None:
    if getattr(addon_model, "_build011_delete_safety_applied", False):
        return

    original_delete = addon_model.delete_addon

    def safe_delete_addon(database: Database, addon_id: int) -> None:
        ensure_group_addon_schema(database)
        row = database.connection.execute(
            "SELECT name FROM add_ons WHERE company_id=? AND id=?",
            (database.company_id(), int(addon_id)),
        ).fetchone()
        if row is None:
            raise ValueError("Add-on no longer exists")
        group_refs = database.connection.execute(
            "SELECT COUNT(*) FROM annual_group_addons WHERE company_id=? AND addon_id=?",
            (database.company_id(), int(addon_id)),
        ).fetchone()[0]
        if int(group_refs):
            raise ValueError(
                f"{row['name']} cannot be deleted because annual Element Type Add-on defaults already reference it. "
                "Make it inactive instead so historical setup remains intact."
            )
        original_delete(database, addon_id)

    addon_model.delete_addon = safe_delete_addon
    addon_setup.delete_addon = safe_delete_addon
    addon_model._build011_delete_safety_applied = True
