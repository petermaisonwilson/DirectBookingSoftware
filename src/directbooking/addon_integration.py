from __future__ import annotations

from . import annual_config as annual
from . import annual_config_repair as repair
from .addon_model import copy_addon_year, delete_addon_year, ensure_addon_schema
from .database import Database


def _addon_rows(database: Database, year: int) -> list[tuple]:
    ensure_addon_schema(database)
    rows = database.connection.execute(
        """
        SELECT element_id, addon_id, allowed, min_qty, max_qty, rate
        FROM annual_element_addons
        WHERE company_id=? AND year=?
        ORDER BY element_id, addon_id
        """,
        (database.company_id(), int(year)),
    ).fetchall()
    return [tuple(row) for row in rows]


def apply_addon_year_integration() -> None:
    if getattr(annual, "_addon_year_integration_applied", False):
        return

    original_copy = annual.copy_previous_year
    original_delete = repair.delete_pricing_year

    def copy_with_addons(database: Database, target_year: int):
        target_year = int(target_year)
        source_year = target_year - 1
        source_rows = _addon_rows(database, source_year)
        result = original_copy(database, target_year)
        try:
            database.connection.execute("BEGIN")
            copy_addon_year(database, source_year, target_year)
            target_rows = _addon_rows(database, target_year)
            if target_rows != source_rows:
                raise ValueError("Add-on rules did not copy completely")
            database.connection.commit()
        except Exception as exc:
            database.connection.rollback()
            try:
                original_delete(database, target_year)
            except Exception:
                pass
            if isinstance(exc, ValueError):
                raise ValueError(f"{exc}; the new year has been rolled back.") from exc
            raise ValueError(f"Could not copy Add-on rules into {target_year}; the new year has been rolled back: {exc}") from exc
        if isinstance(result, dict):
            result = dict(result)
            result["addon_rules"] = len(target_rows)
        return result

    def delete_with_addons(database: Database, year: int) -> None:
        original_delete(database, year)
        delete_addon_year(database, year)
        database.connection.commit()

    annual.copy_previous_year = copy_with_addons
    repair.copy_previous_year_verified = copy_with_addons
    repair.delete_pricing_year = delete_with_addons
    annual._addon_year_integration_applied = True
