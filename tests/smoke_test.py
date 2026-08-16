from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from directbooking.annual_config import copy_previous_year, list_years, migrate_legacy_current_year, validate_year
from directbooking.database import Database
from directbooking.main_window import MainWindow
from directbooking.person_pricing import save_element_person_rates
from directbooking.pricing import calculate_price
from directbooking.pricing_test_dialog import PricingTestDialog
from directbooking.record_foundation import ensure_record_foundation


def main() -> None:
    app = QApplication.instance() or QApplication([])
    current_year = date.today().year
    next_year = current_year + 1
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "smoke.db"
        database = Database(db_path)
        database.initialise()
        ensure_record_foundation(database)

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

        copy_previous_year(database, next_year)
        copied = calculate_price(
            database, pitch, f"{next_year}-09-01", f"{next_year}-09-08",
            person_counts={adult: 2, child: 2},
        )
        assert copied["final_amount"] == 226.8

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
        assert window.windowTitle() == "Direct Booking Software - Build 008"
        assert window.nav.count() == 6
        assert window.setup_page.tabs.count() == 6
        assert window.setup_page.tabs.tabText(4) == "Person types"
        assert window.setup_page.tabs.tabText(5) == "Annual grids"
        assert window.annual_config_tab.tabs.count() == 3
        assert window.annual_config_tab.year_combo.count() >= 2

        dialog = PricingTestDialog(database)
        assert dialog.element.count() >= 2
        assert len(dialog.person_controls) == 2
        assert dialog.windowTitle() == "Pricing Test - Build 008"
        dialog.close()
        window.close()

        for table in ("clients", "booking_clients", "booking_party_snapshot", "booking_pricing_snapshots"):
            assert database.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone() is not None
        database.close()

        reopened = Database(db_path)
        reopened.initialise()
        ensure_record_foundation(reopened)
        reopened_result = calculate_price(
            reopened, pitch, f"{next_year}-09-01", f"{next_year}-09-08",
            person_counts={adult: 2, child: 2},
        )
        assert reopened_result["final_amount"] == 226.8
        reopened.close()

    app.quit()
    print("Build 008 smoke test: passed")


if __name__ == "__main__":
    main()
