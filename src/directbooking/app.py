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
        assert database.counts()["elements"] == 3

        database.save_settings({"operator_name": "Self Test Operator", "offer_expiry_days": "9"})
        element_id = database.save_element(None, "Pitch 11", "Camping", "Per night", 30.0)
        season_id = database.save_season(None, "High Season", "2026-07-01", "2026-08-31", 10)
        assert database.get_settings()["operator_name"] == "Self Test Operator"
        assert any(row["id"] == element_id for row in database.list_elements())
        assert any(row["id"] == season_id for row in database.list_seasons())

        window = MainWindow(database)
        assert window.windowTitle() == "Direct Booking Software - Build 002"
        assert window.nav.count() == 6
        window.close()
        database.close()

        reopened = Database(db_path)
        reopened.initialise()
        assert reopened.get_settings()["offer_expiry_days"] == "9"
        assert any(row["name"] == "Pitch 11" for row in reopened.list_elements())
        assert any(row["name"] == "High Season" for row in reopened.list_seasons())
        reopened.close()

    app.quit()
    print("Direct Booking Software Build 002 self-test: passed")
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
