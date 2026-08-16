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
        infant = database.save_person_type(None, "Infant", "Inf")
        database.set_person_type_active(infant, False)

        pitch = database.save_element(None, "Pitch", "Camping", "Per night", 40.0)
        database.save_element_capacity(pitch, 4, {adult: 4, child: 4})
        pitch_result = calculate_price(
            database, pitch, "2026-09-01", "2026-09-04",
            person_counts={adult: 2, child: 2},
        )
        assert pitch_result["base_amount"] == 120.0
        assert pitch_result["guests"] == 4
        assert pitch_result["people_summary"] == "2 Ad, 2 Ch"

        bunk = database.save_element(None, "Bunk", "Rooms", "Per person per night", 20.0)
        database.save_element_capacity(bunk, 4, {adult: 2, child: 3})
        save_element_person_rates(database, bunk, {adult: 20.0, child: 10.0})
        rates = get_element_person_rates(database, bunk)
        assert rates[adult] == 20.0 and rates[child] == 10.0

        bunk_result = calculate_price(
            database, bunk, "2026-09-01", "2026-09-04",
            person_counts={adult: 2, child: 2},
        )
        assert bunk_result["base_amount"] == 180.0
        assert len(bunk_result["person_breakdown"]) == 2

        meal = database.save_element(None, "Meal", "Food", "Per person", 15.0)
        database.save_element_capacity(meal, 6, {adult: 6, child: 6})
        save_element_person_rates(database, meal, {child: 8.0})
        meal_result = calculate_price(
            database, meal, "2026-09-01", "2026-09-01",
            person_counts={adult: 2, child: 2},
        )
        assert meal_result["base_amount"] == 46.0  # adults fall back to €15 base; children €8

        blocked = False
        try:
            calculate_price(database, bunk, "2026-09-01", "2026-09-04", person_counts={adult: 3, child: 1})
        except ValueError as exc:
            blocked = "Adult" in str(exc)
        assert blocked

        blocked_total = False
        try:
            calculate_price(database, bunk, "2026-09-01", "2026-09-04", person_counts={adult: 2, child: 3})
        except ValueError as exc:
            blocked_total = "Total persons" in str(exc)
        assert blocked_total

        database.save_discount_rule(None, "3 nights 10%", 3, "Percentage", 10, "Element", element_id=bunk)
        discounted = calculate_price(
            database, bunk, "2026-09-01", "2026-09-04",
            person_counts={adult: 2, child: 2},
        )
        assert discounted["base_amount"] == 180.0
        assert discounted["discount_amount"] == 18.0
        assert discounted["final_amount"] == 162.0

        window = MainWindow(database)
        assert window.windowTitle() == "Direct Booking Software - Build 006"
        assert window.nav.count() == 6
        assert window.setup_page.tabs.count() == 7
        assert window.setup_page.tabs.tabText(4) == "Person types"
        assert window.setup_page.tabs.tabText(5) == "Occupancy"
        assert window.setup_page.tabs.tabText(6) == "Person pricing"
        assert window.person_pricing_tab.element.count() >= 3

        dialog = PricingTestDialog(database)
        assert dialog.element.count() >= 3
        assert len(dialog.person_controls) == 2
        dialog.close()
        window.close()
        database.close()

        reopened = Database(db_path)
        reopened.initialise()
        assert get_element_person_rates(reopened, bunk)[child] == 10.0
        reopened_result = calculate_price(
            reopened, bunk, "2026-09-01", "2026-09-04",
            person_counts={adult: 2, child: 2},
        )
        assert reopened_result["final_amount"] == 162.0
        reopened.close()

    app.quit()
    print("Build 006 smoke test: passed")


if __name__ == "__main__":
    main()
