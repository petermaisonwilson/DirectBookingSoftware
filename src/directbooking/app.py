from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .addon_inheritance import resolve_addon_rule, save_group_addon_rule, validate_group_addon_year
from .addon_model import addon_amount, save_addon, save_addon_rule
from .annual_config import copy_previous_year, list_years, migrate_legacy_current_year, validate_year
from .annual_config_repair import delete_pricing_year
from .database import Database
from .main_window import MainWindow
from .person_pricing import save_element_person_rates
from .pricing import calculate_price
from .pricing_test_dialog import PricingTestDialog
from .record_foundation import ensure_record_foundation


def application_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    else:
        base = Path.home() / ".local" / "share"
    return base / "DirectBookingSoftware"


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


def run_self_test() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    current_year = date.today().year
    next_year = current_year + 1
    delete_test_year = current_year + 2
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "self_test.db"
        database = Database(db_path)
        try:
            database.initialise()
            ensure_record_foundation(database)
            database.connection.execute("DELETE FROM elements")
            database.connection.commit()

            adult_id = database.save_person_type(None, "Adult", "Ad")
            child_id = database.save_person_type(None, "Child", "Ch")
            pitch1_id = database.save_element(None, "Pitch 1", "Camping", "Per night", 20.0)
            pitch7_id = database.save_element(None, "Pitch 7", "Camping", "Per night", 22.0)
            gite_id = database.save_element(None, "Gite", "Gites", "Per night", 80.0)
            for element_id, total in ((pitch1_id, 6), (pitch7_id, 4), (gite_id, 4)):
                database.save_element_capacity(element_id, total, {adult_id: total, child_id: total})
                save_element_person_rates(database, element_id, {adult_id: 0.0, child_id: 0.0})

            migrate_legacy_current_year(database)
            assert validate_year(database, current_year) == {"seasons": 0, "rates": 0, "people": 0, "occupancy": 0}

            dog_id = save_addon(database, None, "Dog", "Per quantity per night")
            ehu_id = save_addon(database, None, "Electric Hook-up", "Per night")
            save_group_addon_rule(database, current_year, "Camping", dog_id, True, 0, 2, 3.0)
            save_group_addon_rule(database, current_year, "Camping", ehu_id, True, 0, 1, 4.0)
            save_group_addon_rule(database, current_year, "Gites", dog_id, False)
            save_group_addon_rule(database, current_year, "Gites", ehu_id, False)
            save_addon_rule(database, current_year, pitch7_id, dog_id, False)
            database.connection.commit()

            assert validate_group_addon_year(database, current_year) == {"unreviewed": 0, "incomplete": 0}
            inherited = resolve_addon_rule(database, current_year, pitch1_id, dog_id)
            assert inherited["allowed"] is True and inherited["max_qty"] == 2 and inherited["source"] == "Element Type: Camping"
            exception = resolve_addon_rule(database, current_year, pitch7_id, dog_id)
            assert exception["allowed"] is False and exception["source"] == "Element override"
            gite_dog = resolve_addon_rule(database, current_year, gite_id, dog_id)
            assert gite_dog["allowed"] is False and gite_dog["source"] == "Element Type: Gites"
            assert addon_amount("Per quantity per night", 3.0, 2, 7, 8) == 42.0
            assert addon_amount("Fixed once", 10.0, 1, 7, 8) == 10.0

            result = calculate_price(
                database, pitch1_id, f"{current_year}-09-01", f"{current_year}-09-08",
                person_counts={adult_id: 2, child_id: 2},
            )
            assert result["annual_mode"] is True
            assert result["element_base_amount"] == 140.0

            source_rates = _annual_rates(database, current_year)
            source_overrides = _override_rows(database, current_year)
            source_groups = _group_rows(database, current_year)
            copy_previous_year(database, next_year)
            assert next_year in list_years(database)
            assert _annual_rates(database, next_year) == source_rates
            assert _override_rows(database, next_year) == source_overrides
            assert _group_rows(database, next_year) == source_groups
            copied_inherited = resolve_addon_rule(database, next_year, pitch1_id, dog_id)
            copied_exception = resolve_addon_rule(database, next_year, pitch7_id, dog_id)
            assert copied_inherited["allowed"] is True and copied_inherited["source"] == "Element Type: Camping"
            assert copied_exception["allowed"] is False and copied_exception["source"] == "Element override"

            copy_previous_year(database, delete_test_year)
            assert _group_rows(database, delete_test_year) == source_groups
            delete_pricing_year(database, delete_test_year)
            assert delete_test_year not in list_years(database)
            assert _override_rows(database, delete_test_year) == []
            assert _group_rows(database, delete_test_year) == []

            window = MainWindow(database)
            assert window.windowTitle() == "Direct Booking Software - Build 012"
            assert window.setup_page.tabs.count() == 8
            assert window.setup_page.tabs.tabText(4) == "Add-ons"
            assert window.setup_page.tabs.tabText(5) == "Add-on rules"
            rules = window.setup_page.addon_rules_tab
            assert rules.pages.count() == 2
            assert rules.pages.tabText(0) == "Element Type defaults"
            assert rules.pages.tabText(1) == "Element overrides"
            assert rules.type_table.horizontalHeaderItem(3).text() == "Y / N"
            assert rules.override_table.horizontalHeaderItem(4).text() == "I / Y / N"
            type_control = rules.type_table.cellWidget(0, 3)
            assert type_control is not None and hasattr(type_control, "_availability_check")
            override_control = rules.override_table.cellWidget(0, 4)
            assert override_control is not None and set(override_control._override_buttons) == {"I", "Y", "N"}
            dialog = PricingTestDialog(database)
            assert len(dialog.person_controls) == 2
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

    app.quit()
    print("Direct Booking Software Build 012 self-test: passed")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return run_self_test()

    app = QApplication(sys.argv)
    app.setApplicationName("Direct Booking Software")
    app.setOrganizationName("Direct Booking Software")

    database = Database(application_data_dir() / "direct_booking.db")
    database.initialise()
    ensure_record_foundation(database)

    window = MainWindow(database)
    window.show()

    exit_code = app.exec()
    database.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
