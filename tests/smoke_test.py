from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from directbooking.database import Database
from directbooking.main_window import MainWindow
from directbooking.person_pricing import get_element_person_rates, save_element_person_rates
from directbooking.pricing import calculate_price
from directbooking.pricing_test_dialog import PricingTestDialog


def main() -> None:
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "smoke.db"
        database = Database(db_path)
        database.initialise()

        adult = database.save_person_type(None, "Adult", "Ad")
        child = database.save_person_type(None, "Child", "Ch")

        pitch = database.save_element(None, "Pitch 1", "Camping", "Per night", 20.0)
        database.save_element_capacity(pitch, 4, {adult: 4, child: 4})
        save_element_person_rates(database, pitch, {adult: 5.0, child: 3.0})
        pitch_result = calculate_price(
            database, pitch, "2026-09-01", "2026-09-08",
            person_counts={adult: 2, child: 2},
        )
        assert pitch_result["element_base_amount"] == 140.0
        assert pitch_result["person_amount"] == 112.0
        assert pitch_result["base_amount"] == 252.0
        assert pitch_result["people_summary"] == "2 Ad, 2 Ch"
        assert len(pitch_result["person_breakdown"]) == 2

        database.save_discount_rule(None, "7 nights 10%", 7, "Percentage", 10, "Element", element_id=pitch)
        discounted = calculate_price(
            database, pitch, "2026-09-01", "2026-09-08",
            person_counts={adult: 2, child: 2},
        )
        assert discounted["base_amount"] == 252.0
        assert discounted["discount_amount"] == 25.2
        assert discounted["final_amount"] == 226.8

        # No person rates on a fixed-price element means no supplement.
        gite = database.save_element(None, "Gite", "Accommodation", "Per night", 65.0)
        database.save_element_capacity(gite, 4, {adult: 4, child: 4})
        gite_result = calculate_price(
            database, gite, "2026-09-01", "2026-09-04",
            person_counts={adult: 2, child: 2},
        )
        assert gite_result["element_base_amount"] == 195.0
        assert gite_result["person_amount"] == 0.0
        assert gite_result["base_amount"] == 195.0

        # Per-person pricing keeps Build 006 semantics: values are actual person rates.
        bunk = database.save_element(None, "Bunk", "Rooms", "Per person per night", 20.0)
        database.save_element_capacity(bunk, 4, {adult: 2, child: 3})
        save_element_person_rates(database, bunk, {adult: 20.0, child: 10.0})
        bunk_result = calculate_price(
            database, bunk, "2026-09-01", "2026-09-04",
            person_counts={adult: 2, child: 2},
        )
        assert bunk_result["element_base_amount"] == 0.0
        assert bunk_result["person_amount"] == 180.0
        assert bunk_result["base_amount"] == 180.0

        blocked = False
        try:
            calculate_price(database, pitch, "2026-09-01", "2026-09-04", person_counts={adult: 3, child: 2})
        except ValueError as exc:
            blocked = "Total persons" in str(exc)
        assert blocked

        window = MainWindow(database)
        assert window.windowTitle() == "Direct Booking Software - Build 007"
        assert window.nav.count() == 6
        assert window.setup_page.tabs.count() == 7
        assert window.setup_page.tabs.tabText(6) == "Person pricing"

        dialog = PricingTestDialog(database)
        assert dialog.element.count() >= 3
        assert len(dialog.person_controls) == 2
        assert dialog.windowTitle() == "Pricing Test - Build 007"
        dialog.close()
        window.close()
        database.close()

        reopened = Database(db_path)
        reopened.initialise()
        assert get_element_person_rates(reopened, pitch)[adult] == 5.0
        reopened_result = calculate_price(
            reopened, pitch, "2026-09-01", "2026-09-08",
            person_counts={adult: 2, child: 2},
        )
        assert reopened_result["final_amount"] == 226.8
        reopened.close()

    app.quit()
    print("Build 007 smoke test: passed")


if __name__ == "__main__":
    main()
