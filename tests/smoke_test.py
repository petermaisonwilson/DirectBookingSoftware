from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

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

            # The application deliberately seeds three demo Elements on a brand-new database.
            # This smoke test needs a controlled legacy fixture, so remove only those isolated
            # test Elements before creating the records whose migration/completeness we assert.
            database.connection.execute("DELETE FROM elements")
            database.connection.commit()

            adult = database.save_person_type(None, "Adult", "Ad")
            child = database.save_person_type(None, "Child", "Ch")
            pitch = database.save_element(None, "Pitch 1", "Camping", "Per night", 20.0)
            database.save_element_capacity(pitch, 4, {adult: 4, child: 4})
            save_element_person_rates(database, pitch, {adult: 5.0, child: 3.0})

            migrate_legacy_current_year(database)
            assert current_year in list_years(database)
            assert validate_year(database, current_year) == {"seasons": 0, "rates": 0, "people": 0, "occupancy": 0}

            pitch_result = calculate_price(
                database, pitch, f"{current_year}-09-01", f"{current_year}-09-08",
                person_counts={adult: 2, child: 2},
            )
            assert pitch_result["annual_mode"] is True
            assert pitch_result["element_base_amount"] == 140.0
            assert pitch_result["person_amount"] == 112.0
            assert pitch_result["base_amount"] == 252.0
            assert pitch_result["people_summary"] == "2 Ad, 2 Ch"

            database.save_discount_rule(None, "7 nights 10%", 7, "Percentage", 10, "Element", element_id=pitch)
            discounted = calculate_price(
                database, pitch, f"{current_year}-09-01", f"{current_year}-09-08",
                person_counts={adult: 2, child: 2},
            )
            assert discounted["discount_amount"] == 25.2
            assert discounted["final_amount"] == 226.8

            source_rates = _annual_rates(database, current_year)
            copy_previous_year(database, next_year)
            assert _annual_rates(database, next_year) == source_rates
            copied = calculate_price(
                database, pitch, f"{next_year}-09-01", f"{next_year}-09-08",
                person_counts={adult: 2, child: 2},
            )
            assert copied["final_amount"] == 226.8

            # A further copied year can be deleted cleanly without disturbing its source.
            copy_previous_year(database, delete_test_year)
            assert delete_test_year in list_years(database)
            assert _annual_rates(database, delete_test_year) == source_rates
            delete_pricing_year(database, delete_test_year)
            assert delete_test_year not in list_years(database)
            assert _annual_rates(database, delete_test_year) == []
            assert next_year in list_years(database)
            assert _annual_rates(database, next_year) == source_rates

            new_pitch = database.save_element(None, "Pitch added later", "Camping", "Per night", 25.0)
            missing = validate_year(database, next_year)
            assert missing["rates"] > 0
            assert missing["people"] > 0
            assert missing["occupancy"] > 0
            try:
                calculate_price(database, new_pitch, f"{next_year}-09-01", f"{next_year}-09-02", person_counts={adult: 1})
                raise AssertionError("Missing annual configuration was not blocked")
            except ValueError:
                pass

            window = MainWindow(database)
            assert window.windowTitle() == "Direct Booking Software - Build 009"
            assert window.nav.count() == 6
            assert window.setup_page.tabs.count() == 6
            assert window.setup_page.tabs.tabText(4) == "Person types"
            assert window.setup_page.tabs.tabText(5) == "Annual grids"
            assert window.annual_config_tab.tabs.count() == 3
            assert window.annual_config_tab.year_combo.count() >= 2
            assert hasattr(window.annual_config_tab, "delete_year_button")
            assert window.annual_config_tab.delete_year_button.text() == "Delete year"

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
            after_zero = missing_counts_from_tables(annual_tab)
            assert after_zero["people"] == before["people"] - 1
            assert test_item.background().color() == ZERO_COLOUR

            test_item.setText("")
            apply_cell_highlights(annual_tab)
            after_blank = missing_counts_from_tables(annual_tab)
            assert after_blank["people"] == before["people"]
            assert test_item.background().color() == MISSING_COLOUR
            with patch("directbooking.annual_grid_safety.QMessageBox.warning") as warning:
                annual_tab.save_all()
                assert warning.called
            assert validate_year(database, next_year)["people"] > 0

            dialog = PricingTestDialog(database)
            assert dialog.element.count() >= 2
            assert len(dialog.person_controls) == 2
            assert dialog.windowTitle() == "Pricing Test - Build 009"
            dialog.close()
            window.close()

            for table in ("clients", "booking_clients", "booking_party_snapshot", "booking_pricing_snapshots"):
                assert database.connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone() is not None
        finally:
            database.close()

        reopened = Database(db_path)
        try:
            reopened.initialise()
            ensure_record_foundation(reopened)
            reopened_result = calculate_price(
                reopened, pitch, f"{next_year}-09-01", f"{next_year}-09-08",
                person_counts={adult: 2, child: 2},
            )
            assert reopened_result["final_amount"] == 226.8
        finally:
            reopened.close()

    app.quit()
    print("Build 009 smoke test: passed")


if __name__ == "__main__":
    main()
