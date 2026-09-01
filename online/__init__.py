"""Direct Booking Software fully web-based application."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

BUILD = "286"
DATABASE_FILENAME = "direct_booking_online_dev.db"


def permanent_database_path() -> Path:
    """Return the stable local database path used by normal Web V1 launches."""
    override = os.environ.get("DIRECTBOOKING_DB")
    if override:
        return Path(override)

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data)
    else:
        base = Path.home() / ".directbookingsoftware"
    return base / "DirectBookingSoftware" / DATABASE_FILENAME


def configure_default_database() -> Path:
    """Configure one persistent database path without ever overwriting an existing database.

    A DIRECTBOOKING_DB override is preserved unchanged. On the first normal launch only,
    a legacy database in the extracted build's online_data folder is copied into the
    permanent location if the permanent database does not already exist.
    """
    override = os.environ.get("DIRECTBOOKING_DB")
    if override:
        return Path(override)

    destination = permanent_database_path()
    destination.parent.mkdir(parents=True, exist_ok=True)

    legacy = Path.cwd() / "online_data" / DATABASE_FILENAME
    if not destination.exists() and legacy.exists():
        shutil.copy2(legacy, destination)

    os.environ["DIRECTBOOKING_DB"] = str(destination)
    return destination


DEFAULT_DATABASE_PATH = configure_default_database()
