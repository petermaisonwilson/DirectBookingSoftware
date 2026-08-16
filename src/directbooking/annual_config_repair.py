from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import QMessageBox, QPushButton

from . import annual_config as base
from .database import Database


def _shift_years(value: str, delta: int) -> str:
    old = date.fromisoformat(value)
    target_year = old.year + int(delta)
    try:
        return old.replace(year=target_year).isoformat()
    except ValueError:
        return old.replace(year=target_year, day=28).isoformat()


def _rows_as_tuples(database: Database, table: str, year: int, columns: str) -> list[tuple]:
    rows = database.connection.execute(
        f"SELECT {columns} FROM {table} WHERE company_id = ? AND year = ? ORDER BY {columns}",
        (database.company_id(), int(year)),
    ).fetchall()
    return [tuple(row) for row in rows]


def copy_previous_year_verified(database: Database, target_year: int) -> dict[str, int]:
    """Copy the previous annual setup transactionally and verify every copied dataset."""
    base.ensure_annual_schema(database)
    target_year = int(target_year)
    source_year = target_year - 1
    if base.year_exists(database, target_year):
        raise ValueError(f"Pricing year {target_year} already exists")
    if not base.year_exists(database, source_year):
        raise ValueError(f"Pricing year {source_year} does not exist")

    company_id = database.company_id()
    delta = target_year - source_year

    source_element_rates = database.connection.execute(
        "SELECT element_id, season_id, rate FROM annual_element_rates WHERE company_id=? AND year=? ORDER BY element_id, season_id",
        (company_id, source_year),
    ).fetchall()
    source_person_rates = _rows_as_tuples(database, "annual_person_rates", source_year, "element_id, person_type_id, rate")
    source_occupancy = _rows_as_tuples(database, "annual_occupancy", source_year, "element_id, max_total")
    source_person_limits = _rows_as_tuples(database, "annual_person_limits", source_year, "element_id, person_type_id, max_count")

    season_rows: dict[int, object] = {int(row["id"]): row for row in base.seasons_for_year(database, source_year)}
    referenced = database.connection.execute(
        """
        SELECT DISTINCT s.*
        FROM seasons s
        JOIN annual_element_rates aer ON aer.season_id = s.id
        WHERE aer.company_id=? AND aer.year=?
        """,
        (company_id, source_year),
    ).fetchall()
    for row in referenced:
        season_rows[int(row["id"])] = row

    try:
        database.connection.execute("BEGIN")
        database.connection.execute(
            "INSERT INTO pricing_years(company_id, year, copied_from_year) VALUES (?, ?, ?)",
            (company_id, target_year, source_year),
        )

        season_map: dict[int, int] = {}
        for source_season_id in sorted(season_rows):
            season = season_rows[source_season_id]
            cursor = database.connection.execute(
                """
                INSERT INTO seasons(company_id, name, start_date, end_date, priority, active)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    company_id,
                    str(season["name"]).replace(str(source_year), str(target_year)),
                    _shift_years(str(season["start_date"]), delta),
                    _shift_years(str(season["end_date"]), delta),
                    int(season["priority"]),
                    int(season["active"]),
                ),
            )
            season_map[source_season_id] = int(cursor.lastrowid)

        for row in source_element_rates:
            source_season_id = int(row["season_id"])
            if source_season_id not in season_map:
                raise ValueError(
                    f"Cannot copy {source_year}: a stored price refers to season {source_season_id}, which could not be copied."
                )
            database.connection.execute(
                "INSERT INTO annual_element_rates(company_id, year, element_id, season_id, rate) VALUES (?, ?, ?, ?, ?)",
                (company_id, target_year, int(row["element_id"]), season_map[source_season_id], float(row["rate"])),
            )

        for element_id, person_type_id, rate in source_person_rates:
            database.connection.execute(
                "INSERT INTO annual_person_rates(company_id, year, element_id, person_type_id, rate) VALUES (?, ?, ?, ?, ?)",
                (company_id, target_year, int(element_id), int(person_type_id), float(rate)),
            )
        for element_id, max_total in source_occupancy:
            database.connection.execute(
                "INSERT INTO annual_occupancy(company_id, year, element_id, max_total) VALUES (?, ?, ?, ?)",
                (company_id, target_year, int(element_id), int(max_total)),
            )
        for element_id, person_type_id, max_count in source_person_limits:
            database.connection.execute(
                "INSERT INTO annual_person_limits(company_id, year, element_id, person_type_id, max_count) VALUES (?, ?, ?, ?, ?)",
                (company_id, target_year, int(element_id), int(person_type_id), int(max_count)),
            )

        target_element_rates = database.connection.execute(
            "SELECT element_id, season_id, rate FROM annual_element_rates WHERE company_id=? AND year=? ORDER BY element_id, season_id",
            (company_id, target_year),
        ).fetchall()
        expected_rates = sorted(
            (int(row["element_id"]), season_map[int(row["season_id"])], float(row["rate"]))
            for row in source_element_rates
        )
        actual_rates = sorted((int(row["element_id"]), int(row["season_id"]), float(row["rate"])) for row in target_element_rates)
        if actual_rates != expected_rates:
            raise ValueError("Seasonal element prices did not copy completely; the new year has been rolled back.")

        target_person_rates = _rows_as_tuples(database, "annual_person_rates", target_year, "element_id, person_type_id, rate")
        target_occupancy = _rows_as_tuples(database, "annual_occupancy", target_year, "element_id, max_total")
        target_person_limits = _rows_as_tuples(database, "annual_person_limits", target_year, "element_id, person_type_id, max_count")
        if target_person_rates != source_person_rates:
            raise ValueError("Person rates/supplements did not copy completely; the new year has been rolled back.")
        if target_occupancy != source_occupancy:
            raise ValueError("Occupancy totals did not copy completely; the new year has been rolled back.")
        if target_person_limits != source_person_limits:
            raise ValueError("Person occupancy limits did not copy completely; the new year has been rolled back.")

        database.connection.commit()
        return {
            "seasons": len(season_map),
            "element_rates": len(actual_rates),
            "person_rates": len(target_person_rates),
            "occupancy": len(target_occupancy),
            "person_limits": len(target_person_limits),
        }
    except Exception as exc:
        database.connection.rollback()
        if isinstance(exc, ValueError):
            raise
        raise ValueError(f"Could not copy pricing year {source_year} to {target_year}: {exc}") from exc


def delete_pricing_year(database: Database, year: int) -> None:
    """Delete one annual setup year while protecting frozen historic booking pricing."""
    base.ensure_annual_schema(database)
    year = int(year)
    years = base.list_years(database)
    if year not in years:
        raise ValueError(f"Pricing year {year} does not exist")
    if len(years) <= 1:
        raise ValueError("The only pricing year cannot be deleted. Create or copy another year first.")

    company_id = database.company_id()
    snapshot_table = database.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='booking_pricing_snapshots'"
    ).fetchone()
    if snapshot_table is not None:
        used = database.connection.execute(
            "SELECT COUNT(*) FROM booking_pricing_snapshots WHERE company_id=? AND pricing_year=?",
            (company_id, year),
        ).fetchone()[0]
        if int(used) > 0:
            raise ValueError(f"Pricing year {year} is referenced by historic booking pricing and cannot be deleted.")

    referenced_seasons = {
        int(row["season_id"])
        for row in database.connection.execute(
            "SELECT DISTINCT season_id FROM annual_element_rates WHERE company_id=? AND year=?",
            (company_id, year),
        ).fetchall()
    }
    contained_seasons = {
        int(row["id"])
        for row in database.connection.execute(
            """
            SELECT id FROM seasons
            WHERE company_id=? AND start_date>=? AND end_date<=?
            """,
            (company_id, f"{year}-01-01", f"{year}-12-31"),
        ).fetchall()
    }
    candidate_seasons = referenced_seasons | contained_seasons

    try:
        database.connection.execute("BEGIN")
        for table in ("annual_element_rates", "annual_person_rates", "annual_person_limits", "annual_occupancy"):
            database.connection.execute(
                f"DELETE FROM {table} WHERE company_id=? AND year=?",
                (company_id, year),
            )
        database.connection.execute(
            "DELETE FROM pricing_years WHERE company_id=? AND year=?",
            (company_id, year),
        )

        for season_id in candidate_seasons:
            still_used = database.connection.execute(
                "SELECT 1 FROM annual_element_rates WHERE company_id=? AND season_id=? LIMIT 1",
                (company_id, season_id),
            ).fetchone()
            if still_used is None:
                database.connection.execute(
                    "DELETE FROM seasons WHERE company_id=? AND id=?",
                    (company_id, season_id),
                )
        database.connection.commit()
    except Exception as exc:
        database.connection.rollback()
        raise ValueError(f"Could not delete pricing year {year}: {exc}") from exc


def apply_annual_config_repair() -> None:
    """Patch Build 008 annual UI/copy behaviour without disturbing the working grid engine."""
    if getattr(base.AnnualConfigurationTab, "_copy_delete_repair_applied", False):
        return

    base.copy_previous_year = copy_previous_year_verified
    original_init = base.AnnualConfigurationTab.__init__

    def patched_init(self, database: Database):
        original_init(self, database)
        self.delete_year_button = QPushButton("Delete year")
        self.delete_year_button.clicked.connect(lambda: _delete_year_from_ui(self))
        controls = self.layout().itemAt(0).layout()
        controls.insertWidget(4, self.delete_year_button)

    base.AnnualConfigurationTab.__init__ = patched_init
    base.AnnualConfigurationTab._copy_delete_repair_applied = True


def _delete_year_from_ui(widget) -> None:
    year = widget.current_year()
    if year is None:
        return
    answer = QMessageBox.question(
        widget,
        "Delete pricing year",
        f"Delete pricing year {year}?\n\nThis removes that year's annual prices, person rates/supplements and occupancy setup.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return
    try:
        delete_pricing_year(widget.database, year)
    except ValueError as exc:
        QMessageBox.warning(widget, "Cannot delete year", str(exc))
        return
    remaining = base.list_years(widget.database)
    widget.refresh_years(max(remaining) if remaining else None)
    QMessageBox.information(widget, "Year deleted", f"Pricing year {year} deleted.")
