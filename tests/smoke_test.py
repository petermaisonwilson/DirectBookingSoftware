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
        database = Database(Path(temp_dir) / "smoke.db")
        database.initialise()
        counts = database.counts()
        assert counts["elements"] == 3
        window = MainWindow(database)
        assert window.windowTitle() == "Direct Booking Software - Build 001"
        assert window.nav.count() == 6
        window.close()
        database.close()
    app.quit()
    print("Build 001 smoke test: passed")


if __name__ == "__main__":
    main()
