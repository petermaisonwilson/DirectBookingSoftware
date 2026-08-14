from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .database import Database
from .main_window import MainWindow
from .pricing import calculate_price
from .pricing_test_dialog import PricingTestDialog


def application_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    else:
        base = Path.home() / ".local" / "share"
    return base / "DirectBookingSoftware"


def run_self_test() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "self_test.db"
        database = Database(db_path)
        database.initialise()

        night_id = database.save_element(None, "Pitch 11", "Camping", "Per night", 40.0)
        database.save_discount_rule(None, "7 nights 10%", 7, "Percentage", 10, "Element", element_id=night_id)
        database.save_discount_rule(None, "7 nights one free", 7, "Free nights", 1, "Element", element_id=night_id)
        result = calculate_price(database, night_id, "2026-09-01", "2026-09-08", 1)
        assert result["nights"] == 7
        assert result["base_amount"] == 280.0
        assert result["discount_amount"] == 40.0
        assert result["final_amount"] == 240.0
        assert result["discount_rule_name"] == "7 nights one free"

        day_id = database.save_element(None, "Day Pitch", "Camping", "Per day", 20.0)
        day_result = calculate_price(database, day_id, "2026-09-01", "2026-09-02", 1)
        assert day_result["nights"] == 1
        assert day_result["days"] == 2
        assert day_result["base_amount"] == 40.0

        person_night_id = database.save_element(None, "Bunk", "Rooms", "Per person per night", 12.0)
        person_result = calculate_price(database, person_night_id, "2026-09-01", "2026-09-04", 3)
        assert person_result["base_amount"] == 108.0

        window = MainWindow(database)
        assert window.windowTitle() == "Direct Booking Software - Build 004"
        assert window.nav.count() == 6
        assert window.setup_page.tabs.count() == 4
        assert window.pricing_test_button.text() == "Open Pricing Test"

        dialog = PricingTestDialog(database)
        assert dialog.element.count() >= 3
        dialog.close()
        window.close()
        database.close()

        reopened = Database(db_path)
        reopened.initialise()
        reopened_result = calculate_price(reopened, night_id, "2026-09-01", "2026-09-08", 1)
        assert reopened_result["final_amount"] == 240.0
        reopened.close()

    app.quit()
    print("Direct Booking Software Build 004 self-test: passed")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return run_self_test()

    app = QApplication(sys.argv)
    app.setApplicationName("Direct Booking Software")
    app.setOrganizationName("Direct Booking Software")

    database = Database(application_data_dir() / "direct_booking.db")
    database.initialise()

    window = MainWindow(database)
    window.show()

    exit_code = app.exec()
    database.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
