from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication

from .database import Database
from .main_window import MainWindow


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

        element_id = database.save_element(None, "Pitch 11", "Camping", "Per night", 40.0)
        rule_id = database.save_discount_rule(None, "7 nights 10%", 7, "Percentage", 10, "Element", element_id=element_id)
        result = database.calculate_duration_discount(element_id, 7, 280.0)
        assert result["discount_amount"] == 28.0
        assert result["final_amount"] == 252.0
        assert result["rule_id"] == rule_id

        unused_id = database.save_element(None, "Temporary", "Test", "Per stay", 1.0)
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
        try:
            database.delete_element(element_id)
            raise RuntimeError("Used element deletion was not blocked")
        except ValueError:
            pass

        window = MainWindow(database)
        assert window.windowTitle() == "Direct Booking Software - Build 003"
        assert window.nav.count() == 6
        assert window.setup_page.tabs.count() == 4
        window.close()
        database.close()

        reopened = Database(db_path)
        reopened.initialise()
        assert any(row["name"] == "7 nights 10%" for row in reopened.list_discount_rules())
        reopened_result = reopened.calculate_duration_discount(element_id, 7, 280.0)
        assert reopened_result["final_amount"] == 252.0
        reopened.close()

    app.quit()
    print("Direct Booking Software Build 003 self-test: passed")
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
