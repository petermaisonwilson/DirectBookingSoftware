from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from directbooking.database import Database
from directbooking.main_window import MainWindow
from directbooking.pricing import calculate_price
from directbooking.pricing_test_dialog import PricingTestDialog


def main() -> None:
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "smoke.db"
        database = Database(db_path)
        database.initialise()

        per_night = database.save_element(None, "Pitch 11", "Camping", "Per night", 40.0)
        per_day = database.save_element(None, "Day Pitch", "Camping", "Per day", 20.0)
        per_stay = database.save_element(None, "Cleaning", "Extras", "Per stay", 75.0)
        per_person = database.save_element(None, "Meal", "Food", "Per person", 15.0)
        per_person_night = database.save_element(None, "Bunk", "Rooms", "Per person per night", 12.0)
        per_package = database.save_element(None, "Welcome Pack", "Extras", "Per package", 30.0)

        assert calculate_price(database, per_night, "2026-09-01", "2026-09-04", 1)["base_amount"] == 120.0
        day_result = calculate_price(database, per_day, "2026-09-01", "2026-09-04", 1)
        assert day_result["nights"] == 3 and day_result["days"] == 4 and day_result["base_amount"] == 80.0
        assert calculate_price(database, per_stay, "2026-09-01", "2026-09-04", 4)["base_amount"] == 75.0
        assert calculate_price(database, per_person, "2026-09-01", "2026-09-04", 4)["base_amount"] == 60.0
        assert calculate_price(database, per_person_night, "2026-09-01", "2026-09-04", 4)["base_amount"] == 144.0
        assert calculate_price(database, per_package, "2026-09-01", "2026-09-04", 4)["base_amount"] == 30.0

        database.save_discount_rule(None, "7 nights 10%", 7, "Percentage", 10, "Element", element_id=per_night)
        database.save_discount_rule(None, "7 nights one free", 7, "Free nights", 1, "Element", element_id=per_night)
        discounted = calculate_price(database, per_night, "2026-09-01", "2026-09-08", 1)
        assert discounted["base_amount"] == 280.0
        assert discounted["discount_amount"] == 40.0
        assert discounted["final_amount"] == 240.0
        assert discounted["discount_rule_name"] == "7 nights one free"

        database.save_discount_rule(None, "14 nights 20%", 14, "Percentage", 20, "Group", group_name="Camping")
        long_stay = calculate_price(database, per_night, "2026-09-01", "2026-09-15", 1)
        assert long_stay["base_amount"] == 560.0
        assert long_stay["discount_amount"] == 112.0
        assert long_stay["final_amount"] == 448.0
        assert long_stay["discount_rule_name"] == "14 nights 20%"

        same_day = calculate_price(database, per_day, "2026-09-01", "2026-09-01", 1)
        assert same_day["nights"] == 0 and same_day["days"] == 1 and same_day["base_amount"] == 20.0

        try:
            calculate_price(database, per_night, "2026-09-04", "2026-09-01", 1)
            raise AssertionError("Departure before arrival was not rejected")
        except ValueError:
            pass

        window = MainWindow(database)
        assert window.windowTitle() == "Direct Booking Software - Build 004"
        assert window.nav.count() == 6
        assert window.setup_page.tabs.count() == 4
        assert window.pricing_test_button.text() == "Open Pricing Test"

        dialog = PricingTestDialog(database)
        assert dialog.element.count() >= 6
        dialog.close()
        window.close()
        database.close()

        reopened = Database(db_path)
        reopened.initialise()
        assert calculate_price(reopened, per_night, "2026-09-01", "2026-09-08", 1)["final_amount"] == 240.0
        reopened.close()

    app.quit()
    print("Build 004 smoke test: passed")


if __name__ == "__main__":
    main()
