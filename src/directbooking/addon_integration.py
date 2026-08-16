from __future__ import annotations

from PySide6.QtWidgets import QWidget

from . import annual_config as annual
from . import annual_config_repair as repair
from . import setup_page as setup
from .addon_model import ensure_addon_schema
from .addon_setup import AddonsTab, ElementAddonRulesTab
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
            database.connection.execute(
                """
                INSERT INTO annual_element_addons(company_id,year,element_id,addon_id,allowed,min_qty,max_qty,rate)
                SELECT company_id, ?, element_id, addon_id, allowed, min_qty, max_qty, rate
                FROM annual_element_addons
                WHERE company_id=? AND year=?
                """,
                (target_year, database.company_id(), source_year),
            )
            target_rows = database.connection.execute(
                """
                SELECT element_id, addon_id, allowed, min_qty, max_qty, rate
                FROM annual_element_addons
                WHERE company_id=? AND year=?
                ORDER BY element_id, addon_id
                """,
                (database.company_id(), target_year),
            ).fetchall()
            target_rows = [tuple(row) for row in target_rows]
            if target_rows != source_rows:
                raise ValueError("Add-on rules did not copy completely")
            database.connection.commit()
        except Exception as exc:
            database.connection.rollback()
            try:
                original_delete(database, target_year)
                database.connection.execute(
                    "DELETE FROM annual_element_addons WHERE company_id=? AND year=?",
                    (database.company_id(), target_year),
                )
                database.connection.commit()
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
        database.connection.execute(
            "DELETE FROM annual_element_addons WHERE company_id=? AND year=?",
            (database.company_id(), int(year)),
        )
        database.connection.commit()

    annual.copy_previous_year = copy_with_addons
    repair.copy_previous_year_verified = copy_with_addons
    repair.delete_pricing_year = delete_with_addons

    original_setup_init = setup.SetupPage.__init__

    def setup_init_with_addons(self, database: Database):
        original_setup_init(self, database)
        self.addons_tab = AddonsTab(database)
        self.addon_rules_tab = ElementAddonRulesTab(database)
        self.tabs.insertTab(4, self.addons_tab, "Add-ons")
        self.tabs.insertTab(5, self.addon_rules_tab, "Add-on rules")
        self.addons_tab.changed.connect(self.addon_rules_tab.refresh)
        self.data_changed.connect(self.addon_rules_tab.refresh)

    setup.SetupPage.__init__ = setup_init_with_addons

    def rules_show_event(self, event):
        self.refresh_years(self.current_year())
        QWidget.showEvent(self, event)

    ElementAddonRulesTab.showEvent = rules_show_event
    annual._addon_year_integration_applied = True
