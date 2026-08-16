from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .annual_config import copy_previous_year, list_years, migrate_legacy_current_year, validate_year
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


def run_self_test() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    current_year = date.today().year
    next_year = current_year + 1
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "self_test.db"
        database = Database(db_path)
        try:
            database.initialise()
            ensure_record_foundation(database)

            # A brand-new application database contains demo Elements. The packaged self-test
            # needs a controlled fixture so completeness assertions only cover records created here.
            database.connection.execute("DELETE FROM elements")
            database.connection.commit()

            adult_id = database.save_person_type(None, "Adult", "Ad")
            child_id = database.save_person_type(None, "Child", "Ch")
            pitch_id = database.save_element(None, "Pitch 1", "Camping", "Per night", 20.0)
            database.save_element_capacity(pitch_id, 4, {adult_id: 4, child_id: 4})
            save_element_person_rates(database, pitch_id, {adult_id: 5.0, child_id: 3.0})

            migrate_legacy_current_year(database)
            assert current_year in list_years(database)
            assert validate_year(database, current_year) == {"seasons": 0, "rates": 0, "people": 0, "occupancy": 0}

            result = calculate_price(
                database, pitch_id, f"{current_year}-09-01", f"{current_year}-09-08",
                person_counts={adult_id: 2, child_id: 2},
            )
            assert result["annual_mode"] is True
            assert result["element_base_amount"] == 140.0
            assert result["person_amount"] == 112.0
            assert result["base_amount"] == 252.0

            database.save_discount_rule(None, "7 nights 10%", 7, "Percentage", 10, "Element", element_id=pitch_id)
            discounted = calculate_price(
                database, pitch_id, f"{current_year}-09-01", f"{current_year}-09-08",
                person_counts={adult_id: 2, child_id: 2},
            )
            assert discounted["discount_amount"] == 25.2
            assert discounted["final_amount"] == 226.8

            copy_previous_year(database, next_year)
            assert next_year in list_years(database)
            copied = calculate_price(
                database, pitch_id, f"{next_year}-09-01", f"{next_year}-09-08",
                person_counts={adult_id: 2, child_id: 2},
            )
            assert copied["final_amount"] == 226.8

            new_element = database.save_element(None, "New Pitch", "Camping", "Per night", 22.0)
            missing = validate_year(database, next_year)
            assert missing["rates"] > 0 and missing["people"] > 0 and missing["occupancy"] > 0
            try:
                calculate_price(database, new_element, f"{next_year}-09-01", f"{next_year}-09-02", person_counts={adult_id: 1})
                raise AssertionError("Incomplete annual setup was not blocked")
            except ValueError:
                pass

            window = MainWindow(database)
            assert window.windowTitle() == "Direct Booking Software - Build 008"
            assert window.setup_page.tabs.count() == 6
            assert window.setup_page.tabs.tabText(4) == "Person types"
            assert window.setup_page.tabs.tabText(5) == "Annual grids"
            assert window.annual_config_tab.tabs.count() == 3
            dialog = PricingTestDialog(database)
            assert len(dialog.person_controls) == 2
            assert dialog.windowTitle() == "Pricing Test - Build 008"
            dialog.close()
            window.close()

            for table in ("clients", "booking_clients", "booking_party_snapshot", "booking_pricing_snapshots"):
                assert database.connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone() is not None
        finally:
            database.close()

    app.quit()
    print("Direct Booking Software Build 008 self-test: passed")
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
