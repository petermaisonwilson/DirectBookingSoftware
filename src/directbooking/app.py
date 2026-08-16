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
        bunk_id = database.save_element(None, "Bunk", "Rooms", "Per person per night", 20.0)
        database.save_element_capacity(bunk_id, 4, {adult_id: 2, child_id: 3})
        save_element_person_rates(database, bunk_id, {adult_id: 20.0, child_id: 10.0})
        assert get_element_person_rates(database, bunk_id)[child_id] == 10.0

        result = calculate_price(
            database, bunk_id, "2026-09-01", "2026-09-04",
            person_counts={adult_id: 2, child_id: 2},
        )
        assert result["guests"] == 4
        assert result["base_amount"] == 180.0
        assert result["people_summary"] == "2 Ad, 2 Ch"

        blocked = False
        try:
            calculate_price(database, bunk_id, "2026-09-01", "2026-09-04", person_counts={adult_id: 3, child_id: 1})
        except ValueError:
            blocked = True
        assert blocked

        window = MainWindow(database)
        assert window.windowTitle() == "Direct Booking Software - Build 006"
        assert window.setup_page.tabs.count() == 7
        assert window.setup_page.tabs.tabText(6) == "Person pricing"
        dialog = PricingTestDialog(database)
        assert len(dialog.person_controls) == 2
        dialog.close()
        window.close()
        database.close()

        reopened = Database(db_path)
        reopened.initialise()
        assert get_element_person_rates(reopened, bunk_id)[child_id] == 10.0
        reopened.close()

    app.quit()
    print("Direct Booking Software Build 006 self-test: passed")
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
