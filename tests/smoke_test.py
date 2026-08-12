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

        assert database.counts()["elements"] == 3
        database.save_settings(
            {
                "operator_name": "Build 002 Test Operator",
                "operator_email": "test@example.invalid",
                "offer_expiry_days": "12",
                "deposit_mode": "Fixed amount",
                "deposit_fixed_amount": "125",
            }
        )
        element_id = database.save_element(None, "Pitch 11", "Camping", "Per night", 32.5)
        season_id = database.save_season(None, "Summer", "2026-06-01", "2026-08-31", 20)
        database.set_element_active(element_id, False)
        database.set_season_active(season_id, False)

        assert database.counts()["elements"] == 3
        assert any(row["id"] == element_id and row["active"] == 0 for row in database.list_elements())
        assert any(row["id"] == season_id and row["active"] == 0 for row in database.list_seasons())

        window = MainWindow(database)
        assert window.windowTitle() == "Direct Booking Software - Build 002"
        assert window.nav.count() == 6
        assert window.setup_page.tabs.count() == 3
        window.close()
        database.close()

        reopened = Database(db_path)
        reopened.initialise()
        settings = reopened.get_settings()
        assert settings["operator_name"] == "Build 002 Test Operator"
        assert settings["offer_expiry_days"] == "12"
        assert settings["deposit_mode"] == "Fixed amount"
        assert any(row["name"] == "Pitch 11" for row in reopened.list_elements())
        assert any(row["name"] == "Summer" for row in reopened.list_seasons())
        reopened.close()

    app.quit()
    print("Build 002 smoke test: passed")


if __name__ == "__main__":
    main()
