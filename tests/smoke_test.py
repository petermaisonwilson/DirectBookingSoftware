from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from directbooking.addon_model import (
    addon_amount,
    get_addon_rule,
    list_addons,
    save_addon,
    save_addon_rule,
    validate_addon_year,
)
from directbooking.addon_setup import MISSING_BG, ZERO_BG
from directbooking.annual_config import copy_previous_year, list_years, migrate_legacy_current_year, validate_year
from directbooking.annual_config_repair import delete_pricing_year
from directbooking.annual_grid_safety import (
    MISSING_COLOUR,
    ZERO_COLOUR,
    apply_cell_highlights,
    missing_counts_from_tables,
)
from directbooking.database import Database
from directbooking.main_window import MainWindow
from directbooking.person_pricing import save_element_person_rates
from directbooking.pricing import calculate_price
from directbooking.pricing_test_dialog import PricingTestDialog
from directbooking.record_foundation import ensure_record_foundation


def _annual_rates(database: Database, year: int) -> list[tuple[int, float]]:
    rows = database.connection.execute(
        "SELECT element_id, rate FROM annual_element_rates WHERE company_id=? AND year=? ORDER BY element_id, rate",
        (database.company_id(), year),
    ).fetchall()
    return [(int(row["element_id"]), float(row["rate"])) for row in rows]


def _addon_rows(database: Database, year: int) -> list[tuple]:
    rows = database.connection.execute(
        """
        SELECT element_id, addon_id, allowed, min_qty, max_qty, rate
        FROM annual_element_addons
        WHERE company_id=? AND year=? ORDER BY element_id, addon_id
        """,
        (database.company_id(), year),
    ).fetchall()
    return [tuple(row) for row in rows]


def main() -> None:
    app = QApplication.instance() or QApplication([])
    current_year = date.today().year
    next_year = current_year + 1
    delete_test_year = current_year + 2
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "smoke.db"
        database = Database(db_path)
        try:
            database.initialise()
            ensure_record_foundation(database)
            database.connection.execute("DELETE FROM elements")
            database.connection.commit()

            adult = database.save_person_type(None, "Adult", "Ad")
            child = database.save_person_type(None, "Child", "Ch")
            pitch = database.save_element(None, "Pitch 1", "Accommodation", "Per night", 20.0)
            gite = database.save_element(None, "Gite", "Accommodation", "Per night", 80.0)
            peg = database.save_element(None, "Fishing Peg 1", "Fishing", "Per day", 15.0)
            for element_id, total in ((pitch, 6), (gite, 4), (peg, 2)):
                database.save_element_capacity(element_id, total, {adult: total, child: total})
                save_element_person_rates(database, element_id, {adult: 0.0, child: 0.0})

            migrate_legacy_current_year(database)
            assert current_year in list_years(database)
            assert validate_year(database, current_year) == {"seasons": 0, "rates": 0, "people": 0, "occupancy": 0}

            dog = save_addon(database, None, "Dog", "Per quantity per night")
            ehu = save_addon(database, None, "Electric Hook-up", "Per night")
            landing_net = save_addon(database, None, "Landing Net hire", "Fixed once")
            assert len(list_addons(database, False)) == 3

            # Every Element/Add-on pair is explicitly reviewed for the year.
            save_addon_rule(database, current_year, pitch, dog, True, 0, 2, 3.0)
            save_addon_rule(database, current_year, pitch, ehu, True, 0, 1, 4.0)
            save_addon_rule(database, current_year, pitch, landing_net, False)
            save_addon_rule(database, current_year, gite, dog, False)
            save_addon_rule(database, current_year, gite, ehu, False)
            save_addon_rule(database, current_year, gite, landing_net, False)
            save_addon_rule(database, current_year, peg, dog, False)
            save_addon_rule(database, current_year, peg, ehu, False)
            save_addon_rule(database, current_year, peg, landing_net, True, 0, 2, 0.0)
            database.connection.commit()
            assert validate_addon_year(database, current_year) == {"unreviewed": 0, "incomplete": 0}
            assert bool(get_addon_rule(database, current_year, gite, dog)["allowed"]) is False
            assert float(get_addon_rule(database, current_year, peg, landing_net)["rate"]) == 0.0

            # Add-ons inherit the parent Element duration but use their own pricing method.
            assert addon_amount("Per quantity per night", 3.0, 2, 7, 8) == 42.0
            assert addon_amount("Per night", 4.0, 1, 7, 8) == 28.0
            assert addon_amount("Fixed once", 10.0, 1, 3, 4) == 10.0
            assert addon_amount("Per quantity", 5.0, 2, 7, 8) == 10.0

            pitch_result = calculate_price(
                database, pitch, f"{current_year}-09-01", f"{current_year}-09-08",
                person_counts={adult: 2, child: 2},
            )
            assert pitch_result["annual_mode"] is True
            assert pitch_result["element_base_amount"] == 140.0

            source_rates = _annual_rates(database, current_year)
            source_addons = _addon_rows(database, current_year)
            copy_previous_year(database, next_year)
            assert _annual_rates(database, next_year) == source_rates
            assert _addon_rows(database, next_year) == source_addons
            assert validate_addon_year(database, next_year) == {"unreviewed": 0, "incomplete": 0}

            copy_previous_year(database, delete_test_year)
            assert _addon_rows(database, delete_test_year) == source_addons
            delete_pricing_year(database, delete_test_year)
            assert delete_test_year not in list_years(database)
            assert _addon_rows(database, delete_test_year) == []
            assert _addon_rows(database, next_year) == source_addons

            # A new Element and a new Add-on after the copy must create unreviewed combinations.
            new_pitch = database.save_element(None, "Pitch added later", "Accommodation", "Per night", 25.0)
            new_extra = save_addon(database, None, "Extra Car", "Fixed once")
            assert validate_year(database, next_year)["rates"] > 0
            addon_missing = validate_addon_year(database, next_year)
            assert addon_missing["unreviewed"] > 0

            window = MainWindow(database)
            assert window.windowTitle() == "Direct Booking Software - Build 010"
            assert window.nav.count() == 6
            assert window.setup_page.tabs.count() == 8
            assert window.setup_page.tabs.tabText(4) == "Add-ons"
            assert window.setup_page.tabs.tabText(5) == "Add-on rules"
            assert window.setup_page.tabs.tabText(6) == "Person types"
            assert window.setup_page.tabs.tabText(7) == "Annual grids"
            assert window.setup_page.addons_tab.table.rowCount() == 4

            rules_tab = window.setup_page.addon_rules_tab
            rules_tab.refresh_years(next_year)
            target_row = next(
                r for r in range(rules_tab.table.rowCount())
                if rules_tab.table.item(r, 0).text() == "Pitch added later"
                and rules_tab.table.item(r, 1).text() == "Extra Car"
            )
            available_item = rules_tab.table.item(target_row, 3)
            assert available_item.text() == ""
            rules_tab._style_rows()
            assert available_item.background().color() == MISSING_BG
            with patch("directbooking.addon_setup.QMessageBox.warning") as warning:
                rules_tab.save_all()
                assert warning.called
            assert get_addon_rule(database, next_year, new_pitch, new_extra) is None

            # Explicit Yes + zero price is valid and gently highlighted rather than warned as missing.
            available_item.setText("Yes")
            rules_tab.table.item(target_row, 4).setText("0")
            rules_tab.table.item(target_row, 5).setText("1")
            price_item = rules_tab.table.item(target_row, 6)
            price_item.setText("0.00")
            rules_tab._style_rows()
            assert price_item.background().color() == ZERO_BG

            # Build 009 blank-vs-zero annual-grid behaviour remains intact.
            annual_tab = window.annual_config_tab
            annual_tab.refresh_years(next_year)
            new_pitch_row = next(
                row for row in range(annual_tab.people_table.rowCount())
                if annual_tab.people_table.item(row, 0).text() == "Pitch added later"
            )
            adult_column = next(
                column for column in range(2, annual_tab.people_table.columnCount())
                if annual_tab.people_table.horizontalHeaderItem(column).text() == "Adult"
            )
            test_item = annual_tab.people_table.item(new_pitch_row, adult_column)
            assert test_item.text() == ""
            apply_cell_highlights(annual_tab)
            assert test_item.background().color() == MISSING_COLOUR
            before = missing_counts_from_tables(annual_tab)
            test_item.setText("0.00")
            apply_cell_highlights(annual_tab)
            assert missing_counts_from_tables(annual_tab)["people"] == before["people"] - 1
            assert test_item.background().color() == ZERO_COLOUR

            dialog = PricingTestDialog(database)
            assert dialog.windowTitle() == "Pricing Test - Build 010"
            dialog.close()
            window.close()

            for table in (
                "clients", "booking_clients", "booking_party_snapshot", "booking_pricing_snapshots",
                "add_ons", "annual_element_addons",
            ):
                assert database.connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone() is not None
        finally:
            database.close()

        reopened = Database(db_path)
        try:
            reopened.initialise()
            ensure_record_foundation(reopened)
            assert _addon_rows(reopened, next_year) == source_addons
        finally:
            reopened.close()

    app.quit()
    print("Build 010 smoke test: passed")


if __name__ == "__main__":
    main()
