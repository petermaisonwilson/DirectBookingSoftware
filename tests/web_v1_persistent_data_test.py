from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def marker_value(path: Path) -> str:
    with sqlite3.connect(path) as connection:
        return str(connection.execute('SELECT value FROM persistence_marker').fetchone()[0])


def main() -> None:
    old_cwd = Path.cwd()
    old_local_app_data = os.environ.get('LOCALAPPDATA')
    old_override = os.environ.get('DIRECTBOOKING_DB')

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            local_app_data = root / 'LocalAppData'
            build_one = root / 'BuildOne'
            build_two = root / 'BuildTwo'
            legacy_dir = build_one / 'online_data'
            legacy_dir.mkdir(parents=True)
            build_two.mkdir()

            legacy_db = legacy_dir / 'direct_booking_online_dev.db'
            with sqlite3.connect(legacy_db) as connection:
                connection.execute('CREATE TABLE persistence_marker(value TEXT NOT NULL)')
                connection.execute("INSERT INTO persistence_marker(value) VALUES ('keep-me')")

            os.environ['LOCALAPPDATA'] = str(local_app_data)
            os.environ.pop('DIRECTBOOKING_DB', None)
            os.chdir(build_one)

            import online

            first_path = online.configure_default_database()
            expected = local_app_data / 'DirectBookingSoftware' / 'direct_booking_online_dev.db'
            assert first_path == expected
            assert expected.exists()
            assert legacy_db.exists()
            assert marker_value(expected) == 'keep-me'

            # A later build must reuse the permanent database, not a database in its own folder.
            os.chdir(build_two)
            conflicting_legacy = build_two / 'online_data' / 'direct_booking_online_dev.db'
            conflicting_legacy.parent.mkdir()
            with sqlite3.connect(conflicting_legacy) as connection:
                connection.execute('CREATE TABLE persistence_marker(value TEXT NOT NULL)')
                connection.execute("INSERT INTO persistence_marker(value) VALUES ('do-not-use')")

            os.environ.pop('DIRECTBOOKING_DB', None)
            second_path = online.configure_default_database()
            assert second_path == expected
            assert marker_value(expected) == 'keep-me'
            assert marker_value(conflicting_legacy) == 'do-not-use'

            # The explicit override remains supported and is never replaced by the permanent default.
            override = root / 'explicit-test.db'
            os.environ['DIRECTBOOKING_DB'] = str(override)
            assert online.configure_default_database() == override
            assert os.environ['DIRECTBOOKING_DB'] == str(override)

    finally:
        os.chdir(old_cwd)
        if old_local_app_data is None:
            os.environ.pop('LOCALAPPDATA', None)
        else:
            os.environ['LOCALAPPDATA'] = old_local_app_data
        if old_override is None:
            os.environ.pop('DIRECTBOOKING_DB', None)
        else:
            os.environ['DIRECTBOOKING_DB'] = old_override

    print('Direct Booking Web V1 persistent local data test: passed')


if __name__ == '__main__':
    main()
