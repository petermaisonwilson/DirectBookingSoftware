from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .database import Database
from .main_window import MainWindow
from .person_pricing import get_element_person_rates, save_element_person_rates
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

        adult_id = database.save_person_type(None, "Adult", "Ad")
        child_id = database.save_person_type(None, "Child", "Ch")

        pitch_id = database.save_element(None, "Pitch 1", "Camping", "Per night", 20.0)
        database.save_element_capacity(pitch_id, 4, {adult_id: 4, child_id: 4})
        save_element_person_rates(database, pitch_id, {adult_id: 5.0, child_id: 3.0})
        result = calculate_price(
            database, pitch_id, "2026-09-01", "2026-09-08",
            person_counts={adult_id: 2, child_id: 2},
        )
        assert result["element_base_amount"] == 140.0
        assert result["person_amount"] == 112.0
        assert result["base_amount"] == 252.0
        assert result["people_summary"] == "2 Ad, 2 Ch"

        database.save_discount_rule(None, "7 nights 10%", 7, "Percentage", 10, "Element", element_id=pitch_id)
        discounted = calculate_price(
            database, pitch_id, "2026-09-01", "2026-09-08",
            person_counts={adult_id: 2, child_id: 2},
        )
        assert discounted["discount_amount"] == 25.2
        assert discounted["final_amount"] == 226.8

        bunk_id = database.save_element(None, "Bunk", "Rooms", "Per person per night", 20.0)
        database.save_element_capacity(bunk_id, 4, {adult_id: 2, child_id: 3})
        save_element_person_rates(database, bunk_id, {adult_id: 20.0, child_id: 10.0})
        bunk = calculate_price(
            database, bunk_id, "2026-09-01", "2026-09-04",
            person_counts={adult_id: 2, child_id: 2},
        )
        assert bunk["element_base_amount"] == 0.0
        assert bunk["person_amount"] == 180.0
        assert bunk["base_amount"] == 180.0

        window = MainWindow(database)
        assert window.windowTitle() == "Direct Booking Software - Build 007"
        assert window.setup_page.tabs.count() == 7
        assert window.setup_page.tabs.tabText(6) == "Person pricing"
        dialog = PricingTestDialog(database)
        assert len(dialog.person_controls) == 2
        assert dialog.windowTitle() == "Pricing Test - Build 007"
        dialog.close()
        window.close()
        database.close()

        reopened = Database(db_path)
        reopened.initialise()
        assert get_element_person_rates(reopened, pitch_id)[child_id] == 3.0
        reopened.close()

    app.quit()
    print("Direct Booking Software Build 007 self-test: passed")
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
