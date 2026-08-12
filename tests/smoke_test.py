from __future__ import annotations

import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from directbooking.database import Database
from directbooking.main_window import MainWindow


def main() -> None:
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "smoke.db"
        database = Database(db_path)
        database.initialise()

        element_id = database.save_element(None, "Pitch 11", "Camping", "Per night", 40.0)
        database.save_discount_rule(None, "7 nights 10%", 7, "Percentage", 10, "Element", element_id=element_id)
        database.save_discount_rule(None, "14 nights 20%", 14, "Percentage", 20, "Group", group_name="Camping")
        database.save_discount_rule(None, "7 nights one free", 7, "Free nights", 1, "All elements")

        seven_nights = database.calculate_duration_discount(element_id, 7, 280.0)
        assert seven_nights["discount_amount"] == 40.0
        assert seven_nights["rule_name"] == "7 nights one free"

        fourteen_nights = database.calculate_duration_discount(element_id, 14, 560.0)
        assert fourteen_nights["discount_amount"] == 112.0
        assert fourteen_nights["rule_name"] == "14 nights 20%"

        unused_id = database.save_element(None, "Delete me", "Test", "Per stay", 5.0)
        database.delete_element(unused_id)
        assert all(row["id"] != unused_id for row in database.list_elements())

        offer = database.connection.execute(
            "INSERT INTO offers(company_id, total_amount) VALUES (?, ?)",
            (database.company_id(), 280.0),
        )
        database.connection.execute(
            "INSERT INTO offer_lines(offer_id, element_id, description, amount) VALUES (?, ?, ?, ?)",
            (offer.lastrowid, element_id, "Pitch 11", 280.0),
        )
        database.connection.commit()
        blocked = False
        try:
            database.delete_element(element_id)
        except ValueError:
            blocked = True
        assert blocked

        window = MainWindow(database)
        assert window.windowTitle() == "Direct Booking Software - Build 003"
        assert window.nav.count() == 6
        assert window.setup_page.tabs.count() == 4
        window.close()
        database.close()

        reopened = Database(db_path)
        reopened.initialise()
        rules = reopened.list_discount_rules()
        assert len(rules) == 3
        assert reopened.calculate_duration_discount(element_id, 14, 560.0)["final_amount"] == 448.0
        reopened.close()

    app.quit()
    print("Build 003 smoke test: passed")


if __name__ == "__main__":
    main()
