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

        adult_id = database.save_person_type(None, "Adult", "Ad")
        child_id = database.save_person_type(None, "Child", "Ch")
        database.save_element_capacity(night_id, 4, {adult_id: 2, child_id: 3})
        capacity = database.get_element_capacity(night_id)
        assert capacity["max_total"] == 4
        assert capacity["limits"][adult_id] == 2
        assert capacity["limits"][child_id] == 3
        assert database.validate_occupancy(night_id, {adult_id: 2, child_id: 2}) == []
        assert any("Adult" in error for error in database.validate_occupancy(night_id, {adult_id: 3, child_id: 1}))
        assert any("Total persons" in error for error in database.validate_occupancy(night_id, {adult_id: 2, child_id: 3}))

        peg_id = database.save_element(None, "Fishing Peg", "Fishing", "Per day", 20.0)
        database.save_element_capacity(peg_id, 1, {adult_id: 1, child_id: 0})
        assert database.validate_occupancy(peg_id, {adult_id: 1}) == []
        assert any("Child" in error for error in database.validate_occupancy(peg_id, {child_id: 1}))

        window = MainWindow(database)
        assert window.windowTitle() == "Direct Booking Software - Build 005"
        assert window.nav.count() == 6
        assert window.setup_page.tabs.count() == 6
        assert window.setup_page.tabs.tabText(4) == "Person types"
        assert window.setup_page.tabs.tabText(5) == "Occupancy"
        assert window.pricing_test_button.text() == "Open Pricing Test"

        dialog = PricingTestDialog(database)
        assert dialog.element.count() >= 3
        dialog.close()
        window.close()
        database.close()

        reopened = Database(db_path)
        reopened.initialise()
        assert any(row["name"] == "Adult" for row in reopened.list_person_types())
        reopened_capacity = reopened.get_element_capacity(night_id)
        assert reopened_capacity["max_total"] == 4
        assert reopened_capacity["limits"][adult_id] == 2
        assert reopened.validate_occupancy(night_id, {adult_id: 2, child_id: 2}) == []
        reopened.close()

    app.quit()
    print("Direct Booking Software Build 005 self-test: passed")
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
