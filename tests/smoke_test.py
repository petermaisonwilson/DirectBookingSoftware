from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from directbooking.addon_inheritance import (
    resolve_addon_rule,
    save_group_addon_rule,
    validate_group_addon_year,
)
from directbooking.addon_model import addon_amount, list_addons, save_addon, save_addon_rule
from directbooking.addon_rules011 import ZERO_BG
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


def _override_rows(database: Database, year: int) -> list[tuple]:
    rows = database.connection.execute(
        "SELECT element_id, addon_id, allowed, min_qty, max_qty, rate FROM annual_element_addons WHERE company_id=? AND year=? ORDER BY element_id, addon_id",
        (database.company_id(), year),
    ).fetchall()
    return [tuple(row) for row in rows]


def _group_rows(database: Database, year: int) -> list[tuple]:
    rows = database.connection.execute(
        "SELECT group_name, addon_id, allowed, min_qty, max_qty, rate FROM annual_group_addons WHERE company_id=? AND year=? ORDER BY group_name COLLATE NOCASE, addon_id",
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
            pitch1 = database.save_element(None, "Pitch 1", "Camping", "Per night", 20.0)
            pitch7 = database.save_element(None, "Pitch 7", "Camping", "Per night", 25.0)
            gite = database.save_element(None, "Gite", "Gites", "Per night", 80.0)
            peg = database.save_element(None, "Fishing Peg 1", "Fishing", "Per day", 15.0)
            for element_id, total in ((pitch1, 6), (pitch7, 4), (gite, 4), (peg, 2)):
                database.save_element_capacity(element_id, total, {adult: total, child: total})
                save_element_person_rates(database, element_id, {adult: 0.0, child: 0.0})

            migrate_legacy_current_year(database)
            assert current_year in list_years(database)
            assert validate_year(database, current_year) == {"seasons": 0, "rates": 0, "people": 0, "occupancy": 0}

            dog = save_addon(database, None, "Dog", "Per quantity per night")
            ehu = save_addon(database, None, "Electric Hook-up", "Per night")
            landing_net = save_addon(database, None, "Landing Net hire", "Fixed once")
            assert len(list_addons(database, False)) == 3

            for group_name in ("Camping", "Gites", "Fishing"):
                for addon_id in (dog, ehu, landing_net):
                    save_group_addon_rule(database, current_year, group_name, addon_id, False)
            save_group_addon_rule(database, current_year, "Camping", dog, True, 0, 2, 3.0)
            save_group_addon_rule(database, current_year, "Camping", ehu, True, 0, 1, 4.0)
            save_group_addon_rule(database, current_year, "Fishing", landing_net, True, 0, 2, 0.0)
            save_addon_rule(database, current_year, pitch7, dog, False)
            database.connection.commit()
            assert validate_group_addon_year(database, current_year) == {"unreviewed": 0, "incomplete": 0}

            pitch1_dog = resolve_addon_rule(database, current_year, pitch1, dog)
            assert pitch1_dog["allowed"] is True and pitch1_dog["max_qty"] == 2 and pitch1_dog["rate"] == 3.0
            assert pitch1_dog["source"] == "Element Type: Camping"
            pitch7_dog = resolve_addon_rule(database, current_year, pitch7, dog)
            assert pitch7_dog["allowed"] is False and pitch7_dog["source"] == "Element override"
            gite_dog = resolve_addon_rule(database, current_year, gite, dog)
            assert gite_dog["allowed"] is False and gite_dog["source"] == "Element Type: Gites"
            peg_net = resolve_addon_rule(database, current_year, peg, landing_net)
            assert peg_net["allowed"] is True and float(peg_net["rate"]) == 0.0

            assert addon_amount("Per quantity per night", 3.0, 2, 7, 8) == 42.0
            assert addon_amount("Per night", 4.0, 1, 7, 8) == 28.0
            assert addon_amount("Fixed once", 10.0, 1, 3, 4) == 10.0

            pitch_result = calculate_price(
                database, pitch1, f"{current_year}-09-01", f"{current_year}-09-08",
                person_counts={adult: 2, child: 2},
            )
            assert pitch_result["annual_mode"] is True
            assert pitch_result["element_base_amount"] == 140.0

            source_rates = _annual_rates(database, current_year)
            source_groups = _group_rows(database, current_year)
            source_overrides = _override_rows(database, current_year)
            copy_previous_year(database, next_year)
            assert _annual_rates(database, next_year) == source_rates
            assert _group_rows(database, next_year) == source_groups
            assert _override_rows(database, next_year) == source_overrides
            assert resolve_addon_rule(database, next_year, pitch1, dog)["source"] == "Element Type: Camping"
            assert resolve_addon_rule(database, next_year, pitch7, dog)["source"] == "Element override"

            copy_previous_year(database, delete_test_year)
            assert _group_rows(database, delete_test_year) == source_groups
            assert _override_rows(database, delete_test_year) == source_overrides
            delete_pricing_year(database, delete_test_year)
            assert delete_test_year not in list_years(database)
            assert _group_rows(database, delete_test_year) == []
            assert _override_rows(database, delete_test_year) == []

            # A new Add-on has no stored Type default until Save, but Build 012 presents it
            # as a simple unticked N rather than an editable/blank Yes-No cell.
            save_addon(database, None, "Extra Car", "Fixed once")
            assert validate_group_addon_year(database, next_year)["unreviewed"] == 3

            new_pitch = database.save_element(None, "Pitch 9", "Camping", "Per night", 25.0)
            inherited_new_pitch = resolve_addon_rule(database, next_year, new_pitch, dog)
            assert inherited_new_pitch["allowed"] is True and inherited_new_pitch["source"] == "Element Type: Camping"
            assert validate_year(database, next_year)["rates"] > 0

            window = MainWindow(database)
            assert window.windowTitle() == "Direct Booking Software - Build 012"
            assert window.nav.count() == 6
            assert window.setup_page.tabs.count() == 8
            assert window.setup_page.tabs.tabText(4) == "Add-ons"
            assert window.setup_page.tabs.tabText(5) == "Add-on rules"
            assert window.setup_page.tabs.tabText(6) == "Person types"
            assert window.setup_page.tabs.tabText(7) == "Annual grids"

            rules_tab = window.setup_page.addon_rules_tab
            assert rules_tab.pages.count() == 2
            assert rules_tab.pages.tabText(0) == "Element Type defaults"
            assert rules_tab.pages.tabText(1) == "Element overrides"
            assert rules_tab.type_table.horizontalHeaderItem(3).text() == "Y / N"
            assert rules_tab.override_table.horizontalHeaderItem(4).text() == "I / Y / N"
            rules_tab.refresh_years(next_year)

            extra_row = next(
                r for r in range(rules_tab.type_table.rowCount())
                if rules_tab.type_table.item(r, 0).text() == "Camping"
                and rules_tab.type_table.item(r, 1).text() == "Extra Car"
            )
            type_wrapper = rules_tab.type_table.cellWidget(extra_row, 3)
            check = type_wrapper._availability_check
            assert check.isChecked() is False
            check.setChecked(True)
            rules_tab.type_table.item(extra_row, 4).setText("0")
            rules_tab.type_table.item(extra_row, 5).setText("1")
            zero_price = rules_tab.type_table.item(extra_row, 6)
            zero_price.setText("0.00")
            rules_tab._style_type_rows()
            assert zero_price.background().color() == ZERO_BG
            assert rules_tab._scan_type_defaults() == []

            pitch1_dog_row = next(
                r for r in range(rules_tab.override_table.rowCount())
                if rules_tab.override_table.item(r, 0).text() == "Pitch 1"
                and rules_tab.override_table.item(r, 2).text() == "Dog"
            )
            pitch7_dog_row = next(
                r for r in range(rules_tab.override_table.rowCount())
                if rules_tab.override_table.item(r, 0).text() == "Pitch 7"
                and rules_tab.override_table.item(r, 2).text() == "Dog"
            )
            pitch1_buttons = rules_tab.override_table.cellWidget(pitch1_dog_row, 4)._override_buttons
            pitch7_buttons = rules_tab.override_table.cellWidget(pitch7_dog_row, 4)._override_buttons
            assert pitch1_buttons["I"].isChecked()
            assert pitch7_buttons["N"].isChecked()
            pitch1_buttons["Y"].setChecked(True)
            rules_tab.override_table.item(pitch1_dog_row, 5).setText("0")
            rules_tab.override_table.item(pitch1_dog_row, 6).setText("3")
            rules_tab.override_table.item(pitch1_dog_row, 7).setText("5.00")
            assert rules_tab._scan_overrides() == []
            pitch1_buttons["I"].setChecked(True)
            assert pitch1_buttons["I"].isChecked()

            # Existing Build 009 blank-vs-zero annual-grid safety remains intact.
            annual_tab = window.annual_config_tab
            annual_tab.refresh_years(next_year)
            new_pitch_row = next(
                row for row in range(annual_tab.people_table.rowCount())
                if annual_tab.people_table.item(row, 0).text() == "Pitch 9"
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
            assert dialog.windowTitle() == "Pricing Test - Build 012"
            dialog.close()
            window.close()

            for table in (
                "clients", "booking_clients", "booking_party_snapshot", "booking_pricing_snapshots",
                "add_ons", "annual_element_addons", "annual_group_addons",
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
            assert _group_rows(reopened, next_year) == source_groups
            assert _override_rows(reopened, next_year) == source_overrides
        finally:
            reopened.close()

    app.quit()
    print("Build 012 smoke test: passed")


if __name__ == "__main__":
    main()
