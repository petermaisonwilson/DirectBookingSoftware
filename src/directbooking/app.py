from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .addon_model import addon_amount, save_addon, save_addon_rule, validate_addon_year
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


def _addon_rows(database: Database, year: int) -> list[tuple]:
    rows = database.connection.execute(
        "SELECT element_id, addon_id, allowed, min_qty, max_qty, rate FROM annual_element_addons WHERE company_id=? AND year=? ORDER BY element_id, addon_id",
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
            pitch_id = database.save_element(None, "Pitch 1", "Accommodation", "Per night", 20.0)
            gite_id = database.save_element(None, "Gite", "Accommodation", "Per night", 80.0)
            for element_id, total in ((pitch_id, 6), (gite_id, 4)):
                database.save_element_capacity(element_id, total, {adult_id: total, child_id: total})
                save_element_person_rates(database, element_id, {adult_id: 0.0, child_id: 0.0})

            migrate_legacy_current_year(database)
            assert validate_year(database, current_year) == {"seasons": 0, "rates": 0, "people": 0, "occupancy": 0}

            dog_id = save_addon(database, None, "Dog", "Per quantity per night")
            ehu_id = save_addon(database, None, "Electric Hook-up", "Per night")
            save_addon_rule(database, current_year, pitch_id, dog_id, True, 0, 2, 3.0)
            save_addon_rule(database, current_year, pitch_id, ehu_id, True, 0, 1, 4.0)
            save_addon_rule(database, current_year, gite_id, dog_id, False)
            save_addon_rule(database, current_year, gite_id, ehu_id, False)
            database.connection.commit()
            assert validate_addon_year(database, current_year) == {"unreviewed": 0, "incomplete": 0}
            assert addon_amount("Per quantity per night", 3.0, 2, 7, 8) == 42.0
            assert addon_amount("Fixed once", 10.0, 1, 7, 8) == 10.0

            result = calculate_price(
                database, pitch_id, f"{current_year}-09-01", f"{current_year}-09-08",
                person_counts={adult_id: 2, child_id: 2},
            )
            assert result["annual_mode"] is True
            assert result["element_base_amount"] == 140.0

            source_rates = _annual_rates(database, current_year)
            source_addons = _addon_rows(database, current_year)
            copy_previous_year(database, next_year)
            assert next_year in list_years(database)
            assert _annual_rates(database, next_year) == source_rates
            assert _addon_rows(database, next_year) == source_addons

            copy_previous_year(database, delete_test_year)
            assert _addon_rows(database, delete_test_year) == source_addons
            delete_pricing_year(database, delete_test_year)
            assert delete_test_year not in list_years(database)
            assert _addon_rows(database, delete_test_year) == []

            new_element = database.save_element(None, "New Pitch", "Accommodation", "Per night", 22.0)
            save_addon(database, None, "Extra Car", "Fixed once")
            assert validate_year(database, next_year)["rates"] > 0
            assert validate_addon_year(database, next_year)["unreviewed"] > 0
            try:
                calculate_price(database, new_element, f"{next_year}-09-01", f"{next_year}-09-02", person_counts={adult_id: 1})
                raise AssertionError("Incomplete annual setup was not blocked")
            except ValueError:
                pass

            window = MainWindow(database)
            assert window.windowTitle() == "Direct Booking Software - Build 010"
            assert window.setup_page.tabs.count() == 8
            assert window.setup_page.tabs.tabText(4) == "Add-ons"
            assert window.setup_page.tabs.tabText(5) == "Add-on rules"
            assert window.setup_page.tabs.tabText(6) == "Person types"
            assert window.setup_page.tabs.tabText(7) == "Annual grids"
            dialog = PricingTestDialog(database)
            assert len(dialog.person_controls) == 2
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

    app.quit()
    print("Direct Booking Software Build 010 self-test: passed")
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
