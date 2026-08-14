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

        pitch = database.save_element(None, "Pitch 11", "Camping", "Per night", 40.0)
        peg = database.save_element(None, "Fishing Peg", "Fishing", "Per day", 20.0)
        database.save_discount_rule(None, "7 nights one free", 7, "Free nights", 1, "Element", element_id=pitch)
        discounted = calculate_price(database, pitch, "2026-09-01", "2026-09-08", 1)
        assert discounted["base_amount"] == 280.0
        assert discounted["discount_amount"] == 40.0
        assert discounted["final_amount"] == 240.0

        adult = database.save_person_type(None, "Adult", "Ad")
        child = database.save_person_type(None, "Child", "Ch")
        infant = database.save_person_type(None, "Infant", "Inf")
        assert len(database.list_person_types()) == 3
        database.set_person_type_active(infant, False)
        assert len(database.list_person_types(False)) == 2

        database.save_element_capacity(pitch, 4, {adult: 4, child: 4})
        database.save_element_capacity(peg, 1, {adult: 1, child: 0})
        pitch_capacity = database.get_element_capacity(pitch)
        assert pitch_capacity["max_total"] == 4
        assert pitch_capacity["limits"][adult] == 4
        assert pitch_capacity["limits"][child] == 4
        assert database.validate_occupancy(pitch, {adult: 2, child: 2}) == []
        assert any("Total persons" in error for error in database.validate_occupancy(pitch, {adult: 3, child: 2}))
        assert database.validate_occupancy(peg, {adult: 1}) == []
        assert any("Child" in error for error in database.validate_occupancy(peg, {child: 1}))
        assert any("Total persons" in error for error in database.validate_occupancy(peg, {adult: 1, child: 1}))

        database.save_person_type(adult, "Adult guest", "Ad", True)
        assert any(row["name"] == "Adult guest" for row in database.list_person_types())

        window = MainWindow(database)
        assert window.windowTitle() == "Direct Booking Software - Build 005"
        assert window.nav.count() == 6
        assert window.setup_page.tabs.count() == 6
        assert window.setup_page.tabs.tabText(4) == "Person types"
        assert window.setup_page.tabs.tabText(5) == "Occupancy"
        assert window.person_types_tab.table.rowCount() == 3
        assert window.occupancy_tab.element.count() >= 2
        assert window.pricing_test_button.text() == "Open Pricing Test"

        dialog = PricingTestDialog(database)
        assert dialog.element.count() >= 2
        dialog.close()
        window.close()
        database.close()

        reopened = Database(db_path)
        reopened.initialise()
        assert any(row["name"] == "Adult guest" for row in reopened.list_person_types())
        reopened_capacity = reopened.get_element_capacity(pitch)
        assert reopened_capacity["max_total"] == 4
        assert reopened_capacity["limits"][adult] == 4
        assert reopened.validate_occupancy(pitch, {adult: 2, child: 2}) == []
        assert calculate_price(reopened, pitch, "2026-09-01", "2026-09-08", 1)["final_amount"] == 240.0
        reopened.close()

    app.quit()
    print("Build 005 smoke test: passed")


if __name__ == "__main__":
    main()
