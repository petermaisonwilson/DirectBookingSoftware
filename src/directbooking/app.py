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
        database = Database(Path(temp_dir) / "self_test.db")
        database.initialise()
        counts = database.counts()
        if counts["elements"] != 3:
            raise RuntimeError("Database seed self-test failed")
        window = MainWindow(database)
        if window.nav.count() != 6:
            raise RuntimeError("Navigation self-test failed")
        window.close()
        database.close()
    app.quit()
    print("Direct Booking Software Build 001 self-test: passed")
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
