from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .database import Database


ANNUAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS pricing_years (
    company_id INTEGER NOT NULL,
    year INTEGER NOT NULL,
    copied_from_year INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(company_id, year),
    FOREIGN KEY(company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS annual_element_rates (
    company_id INTEGER NOT NULL,
    year INTEGER NOT NULL,
    element_id INTEGER NOT NULL,
    season_id INTEGER NOT NULL,
    rate REAL NOT NULL,
    PRIMARY KEY(company_id, year, element_id, season_id),
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(element_id) REFERENCES elements(id),
    FOREIGN KEY(season_id) REFERENCES seasons(id)
);

CREATE TABLE IF NOT EXISTS annual_person_rates (
    company_id INTEGER NOT NULL,
    year INTEGER NOT NULL,
    element_id INTEGER NOT NULL,
    person_type_id INTEGER NOT NULL,
    rate REAL NOT NULL,
    PRIMARY KEY(company_id, year, element_id, person_type_id),
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(element_id) REFERENCES elements(id),
    FOREIGN KEY(person_type_id) REFERENCES person_types(id)
);

CREATE TABLE IF NOT EXISTS annual_occupancy (
    company_id INTEGER NOT NULL,
    year INTEGER NOT NULL,
    element_id INTEGER NOT NULL,
    max_total INTEGER NOT NULL,
    PRIMARY KEY(company_id, year, element_id),
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(element_id) REFERENCES elements(id)
);

CREATE TABLE IF NOT EXISTS annual_person_limits (
    company_id INTEGER NOT NULL,
    year INTEGER NOT NULL,
    element_id INTEGER NOT NULL,
    person_type_id INTEGER NOT NULL,
    max_count INTEGER NOT NULL,
    PRIMARY KEY(company_id, year, element_id, person_type_id),
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(element_id) REFERENCES elements(id),
    FOREIGN KEY(person_type_id) REFERENCES person_types(id)
);
"""

PERSON_PRICING_TYPES = {"Per person", "Per person per night"}


def ensure_annual_schema(database: Database) -> None:
    database.connection.executescript(ANNUAL_SCHEMA)
    database.connection.commit()


def list_years(database: Database) -> list[int]:
    ensure_annual_schema(database)
    rows = database.connection.execute(
        "SELECT year FROM pricing_years WHERE company_id = ? ORDER BY year",
        (database.company_id(),),
    ).fetchall()
    return [int(row["year"]) for row in rows]


def year_exists(database: Database, year: int) -> bool:
    return int(year) in list_years(database)


def seasons_for_year(database: Database, year: int) -> list:
    start = f"{int(year):04d}-01-01"
    end = f"{int(year):04d}-12-31"
    return database.connection.execute(
        """
        SELECT * FROM seasons
        WHERE company_id = ? AND active = 1 AND start_date <= ? AND end_date >= ?
        ORDER BY priority DESC, start_date, name COLLATE NOCASE
        """,
        (database.company_id(), end, start),
    ).fetchall()


def _shift_one_year(value: str, target_year: int) -> str:
    old = date.fromisoformat(value)
    try:
        return old.replace(year=target_year).isoformat()
    except ValueError:
        return old.replace(year=target_year, day=28).isoformat()


def _create_default_season(database: Database, year: int) -> int:
    return database.save_season(None, f"All Year {year}", f"{year}-01-01", f"{year}-12-31", 0, True)


def create_blank_year(database: Database, year: int) -> None:
    ensure_annual_schema(database)
    year = int(year)
    if year_exists(database, year):
        raise ValueError(f"Pricing year {year} already exists")
    database.connection.execute(
        "INSERT INTO pricing_years(company_id, year) VALUES (?, ?)",
        (database.company_id(), year),
    )
    database.connection.commit()
    if not seasons_for_year(database, year):
        _create_default_season(database, year)


def copy_previous_year(database: Database, target_year: int) -> None:
    ensure_annual_schema(database)
    target_year = int(target_year)
    source_year = target_year - 1
    if year_exists(database, target_year):
        raise ValueError(f"Pricing year {target_year} already exists")
    if not year_exists(database, source_year):
        raise ValueError(f"Pricing year {source_year} does not exist")

    database.connection.execute(
        "INSERT INTO pricing_years(company_id, year, copied_from_year) VALUES (?, ?, ?)",
        (database.company_id(), target_year, source_year),
    )

    season_map: dict[int, int] = {}
    for season in seasons_for_year(database, source_year):
        cursor = database.connection.execute(
            """
            INSERT INTO seasons(company_id, name, start_date, end_date, priority, active)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                database.company_id(),
                str(season["name"]).replace(str(source_year), str(target_year)),
                _shift_one_year(str(season["start_date"]), target_year),
                _shift_one_year(str(season["end_date"]), target_year),
                int(season["priority"]),
                int(season["active"]),
            ),
        )
        season_map[int(season["id"])] = int(cursor.lastrowid)

    for row in database.connection.execute(
        "SELECT element_id, season_id, rate FROM annual_element_rates WHERE company_id = ? AND year = ?",
        (database.company_id(), source_year),
    ).fetchall():
        if int(row["season_id"]) in season_map:
            database.connection.execute(
                "INSERT INTO annual_element_rates(company_id, year, element_id, season_id, rate) VALUES (?, ?, ?, ?, ?)",
                (database.company_id(), target_year, int(row["element_id"]), season_map[int(row["season_id"])], float(row["rate"])),
            )

    for table, columns in [
        ("annual_person_rates", "element_id, person_type_id, rate"),
        ("annual_person_limits", "element_id, person_type_id, max_count"),
    ]:
        rows = database.connection.execute(
            f"SELECT {columns} FROM {table} WHERE company_id = ? AND year = ?",
            (database.company_id(), source_year),
        ).fetchall()
        value_name = "rate" if table == "annual_person_rates" else "max_count"
        for row in rows:
            database.connection.execute(
                f"INSERT INTO {table}(company_id, year, element_id, person_type_id, {value_name}) VALUES (?, ?, ?, ?, ?)",
                (database.company_id(), target_year, int(row["element_id"]), int(row["person_type_id"]), row[value_name]),
            )

    for row in database.connection.execute(
        "SELECT element_id, max_total FROM annual_occupancy WHERE company_id = ? AND year = ?",
        (database.company_id(), source_year),
    ).fetchall():
        database.connection.execute(
            "INSERT INTO annual_occupancy(company_id, year, element_id, max_total) VALUES (?, ?, ?, ?)",
            (database.company_id(), target_year, int(row["element_id"]), int(row["max_total"])),
        )
    database.connection.commit()


def migrate_legacy_current_year(database: Database) -> None:
    ensure_annual_schema(database)
    if list_years(database):
        return
    year = date.today().year
    database.connection.execute(
        "INSERT INTO pricing_years(company_id, year) VALUES (?, ?)",
        (database.company_id(), year),
    )
    seasons = seasons_for_year(database, year)
    if not seasons:
        _create_default_season(database, year)
        seasons = seasons_for_year(database, year)

    elements = database.list_elements(True)
    person_types = database.list_person_types(True)
    for element in elements:
        element_id = int(element["id"])
        if str(element["pricing_type"]) not in PERSON_PRICING_TYPES:
            for season in seasons:
                database.connection.execute(
                    "INSERT OR REPLACE INTO annual_element_rates(company_id, year, element_id, season_id, rate) VALUES (?, ?, ?, ?, ?)",
                    (database.company_id(), year, element_id, int(season["id"]), float(element["base_price"])),
                )

    has_legacy_person_rates = database.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='element_person_rates'"
    ).fetchone() is not None
    legacy_rates: dict[tuple[int, int], float] = {}
    if has_legacy_person_rates:
        rows = database.connection.execute(
            "SELECT element_id, person_type_id, rate FROM element_person_rates WHERE company_id = ?",
            (database.company_id(),),
        ).fetchall()
        legacy_rates = {(int(r["element_id"]), int(r["person_type_id"])): float(r["rate"]) for r in rows}

    for element in elements:
        element_id = int(element["id"])
        for person_type in person_types:
            person_type_id = int(person_type["id"])
            key = (element_id, person_type_id)
            if key in legacy_rates:
                value = legacy_rates[key]
            elif str(element["pricing_type"]) in PERSON_PRICING_TYPES:
                value = float(element["base_price"])
            else:
                value = 0.0
            database.connection.execute(
                "INSERT OR REPLACE INTO annual_person_rates(company_id, year, element_id, person_type_id, rate) VALUES (?, ?, ?, ?, ?)",
                (database.company_id(), year, element_id, person_type_id, value),
            )

    for row in database.connection.execute(
        "SELECT element_id, max_total FROM element_capacity WHERE company_id = ?",
        (database.company_id(),),
    ).fetchall():
        database.connection.execute(
            "INSERT OR REPLACE INTO annual_occupancy(company_id, year, element_id, max_total) VALUES (?, ?, ?, ?)",
            (database.company_id(), year, int(row["element_id"]), int(row["max_total"])),
        )
    for row in database.connection.execute(
        "SELECT element_id, person_type_id, max_count FROM element_person_limits WHERE company_id = ?",
        (database.company_id(),),
    ).fetchall():
        database.connection.execute(
            "INSERT OR REPLACE INTO annual_person_limits(company_id, year, element_id, person_type_id, max_count) VALUES (?, ?, ?, ?, ?)",
            (database.company_id(), year, int(row["element_id"]), int(row["person_type_id"]), int(row["max_count"])),
        )
    database.connection.commit()


def get_season_for_date(database: Database, value: date) -> object | None:
    rows = database.connection.execute(
        """
        SELECT * FROM seasons
        WHERE company_id = ? AND active = 1 AND start_date <= ? AND end_date >= ?
        ORDER BY priority DESC, id DESC
        """,
        (database.company_id(), value.isoformat(), value.isoformat()),
    ).fetchall()
    return rows[0] if rows else None


def get_annual_element_rate(database: Database, year: int, element_id: int, season_id: int) -> float | None:
    row = database.connection.execute(
        "SELECT rate FROM annual_element_rates WHERE company_id = ? AND year = ? AND element_id = ? AND season_id = ?",
        (database.company_id(), int(year), int(element_id), int(season_id)),
    ).fetchone()
    return float(row["rate"]) if row else None


def get_annual_person_rate(database: Database, year: int, element_id: int, person_type_id: int) -> float | None:
    row = database.connection.execute(
        "SELECT rate FROM annual_person_rates WHERE company_id = ? AND year = ? AND element_id = ? AND person_type_id = ?",
        (database.company_id(), int(year), int(element_id), int(person_type_id)),
    ).fetchone()
    return float(row["rate"]) if row else None


def validate_annual_occupancy(database: Database, year: int, element_id: int, person_counts: dict[int, int]) -> list[str]:
    row = database.connection.execute(
        "SELECT max_total FROM annual_occupancy WHERE company_id = ? AND year = ? AND element_id = ?",
        (database.company_id(), int(year), int(element_id)),
    ).fetchone()
    if row is None:
        return [f"Occupancy setup missing for {year}"]
    types = {int(r["id"]): r for r in database.list_person_types(True)}
    errors: list[str] = []
    total = sum(max(0, int(v)) for v in person_counts.values())
    max_total = int(row["max_total"])
    if max_total > 0 and total > max_total:
        errors.append(f"Total persons: maximum {max_total}")
    for person_type_id, count in person_counts.items():
        if int(count) <= 0:
            continue
        limit_row = database.connection.execute(
            "SELECT max_count FROM annual_person_limits WHERE company_id = ? AND year = ? AND element_id = ? AND person_type_id = ?",
            (database.company_id(), int(year), int(element_id), int(person_type_id)),
        ).fetchone()
        if limit_row is None:
            name = types.get(int(person_type_id), {"name": "Person type"})["name"]
            errors.append(f"{name}: occupancy setup missing for {year}")
            continue
        limit = int(limit_row["max_count"])
        if limit >= 0 and int(count) > limit:
            errors.append(f"{types[int(person_type_id)]['name']}: maximum {limit}")
    return errors


def validate_year(database: Database, year: int) -> dict[str, int]:
    elements = database.list_elements(False)
    person_types = database.list_person_types(False)
    seasons = seasons_for_year(database, year)
    missing_rates = 0
    for element in elements:
        if str(element["pricing_type"]) in PERSON_PRICING_TYPES:
            continue
        for season in seasons:
            if get_annual_element_rate(database, year, int(element["id"]), int(season["id"])) is None:
                missing_rates += 1
    missing_people = 0
    for element in elements:
        for person in person_types:
            if get_annual_person_rate(database, year, int(element["id"]), int(person["id"])) is None:
                missing_people += 1
    missing_occupancy = 0
    for element in elements:
        row = database.connection.execute(
            "SELECT 1 FROM annual_occupancy WHERE company_id=? AND year=? AND element_id=?",
            (database.company_id(), year, int(element["id"])),
        ).fetchone()
        if row is None:
            missing_occupancy += 1
        for person in person_types:
            row = database.connection.execute(
                "SELECT 1 FROM annual_person_limits WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?",
                (database.company_id(), year, int(element["id"]), int(person["id"])),
            ).fetchone()
            if row is None:
                missing_occupancy += 1
    return {
        "seasons": 0 if seasons else 1,
        "rates": missing_rates,
        "people": missing_people,
        "occupancy": missing_occupancy,
    }


def _item_text(table: QTableWidget, row: int, column: int) -> str:
    item = table.item(row, column)
    return item.text().strip() if item else ""


class AnnualConfigurationTab(QWidget):
    def __init__(self, database: Database):
        super().__init__()
        self.database = database
        migrate_legacy_current_year(database)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Pricing year"))
        self.year_combo = QComboBox()
        self.year_combo.currentIndexChanged.connect(self.refresh_all)
        controls.addWidget(self.year_combo)
        blank = QPushButton("New blank year")
        copy = QPushButton("Copy previous year")
        blank.clicked.connect(self.new_blank_year)
        copy.clicked.connect(self.copy_year)
        controls.addWidget(blank)
        controls.addWidget(copy)
        controls.addStretch()
        layout.addLayout(controls)

        self.overall_status = QLabel()
        self.overall_status.setWordWrap(True)
        self.overall_status.setObjectName("bodyText")
        layout.addWidget(self.overall_status)

        self.tabs = QTabWidget()
        self.rates_page, self.rates_status, self.rates_table = self._grid_page()
        self.people_page, self.people_status, self.people_table = self._grid_page()
        self.occupancy_page, self.occupancy_status, self.occupancy_table = self._grid_page()
        self.tabs.addTab(self.rates_page, "Seasonal element rates")
        self.tabs.addTab(self.people_page, "Person rates / supplements")
        self.tabs.addTab(self.occupancy_page, "Occupancy")
        layout.addWidget(self.tabs, 1)

        actions = QHBoxLayout()
        actions.addStretch()
        save = QPushButton("Save all annual grids")
        save.clicked.connect(self.save_all)
        actions.addWidget(save)
        layout.addLayout(actions)

        self.refresh_years()

    def _grid_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        status = QLabel()
        status.setWordWrap(True)
        status.setObjectName("bodyText")
        layout.addWidget(status)
        table = QTableWidget()
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setVisible(False)
        layout.addWidget(table, 1)
        return page, status, table

    def current_year(self) -> int | None:
        value = self.year_combo.currentData()
        return int(value) if value is not None else None

    def refresh_years(self, select_year: int | None = None) -> None:
        years = list_years(self.database)
        current = select_year if select_year is not None else self.current_year()
        self.year_combo.blockSignals(True)
        self.year_combo.clear()
        for year in years:
            self.year_combo.addItem(str(year), year)
        if current in years:
            self.year_combo.setCurrentIndex(years.index(current))
        elif years:
            self.year_combo.setCurrentIndex(len(years) - 1)
        self.year_combo.blockSignals(False)
        self.refresh_all()

    def refresh_all(self) -> None:
        year = self.current_year()
        if year is None:
            return
        self._load_rates(year)
        self._load_people(year)
        self._load_occupancy(year)
        self._update_status(year)

    def _active_elements(self):
        return self.database.list_elements(False)

    def _active_people(self):
        return self.database.list_person_types(False)

    def _load_rates(self, year: int) -> None:
        elements = self._active_elements()
        seasons = seasons_for_year(self.database, year)
        self.rates_table.clear()
        self.rates_table.setRowCount(len(elements))
        self.rates_table.setColumnCount(2 + len(seasons))
        self.rates_table.setHorizontalHeaderLabels(["Element", "Pricing type"] + [str(s["name"]) for s in seasons])
        for r, element in enumerate(elements):
            name = QTableWidgetItem(str(element["name"]))
            name.setFlags(name.flags() & ~name.flags().__class__.ItemIsEditable)
            ptype = QTableWidgetItem(str(element["pricing_type"]))
            ptype.setFlags(ptype.flags() & ~ptype.flags().__class__.ItemIsEditable)
            self.rates_table.setItem(r, 0, name)
            self.rates_table.setItem(r, 1, ptype)
            for c, season in enumerate(seasons, start=2):
                if str(element["pricing_type"]) in PERSON_PRICING_TYPES:
                    item = QTableWidgetItem("N/A")
                    item.setFlags(item.flags() & ~item.flags().__class__.ItemIsEditable)
                else:
                    value = get_annual_element_rate(self.database, year, int(element["id"]), int(season["id"]))
                    item = QTableWidgetItem("" if value is None else f"{value:.2f}")
                item.setData(32, int(element["id"]))
                item.setData(33, int(season["id"]))
                self.rates_table.setItem(r, c, item)

    def _load_people(self, year: int) -> None:
        elements = self._active_elements()
        people = self._active_people()
        self.people_table.clear()
        self.people_table.setRowCount(len(elements))
        self.people_table.setColumnCount(2 + len(people))
        self.people_table.setHorizontalHeaderLabels(["Element", "Pricing type"] + [str(p["name"]) for p in people])
        for r, element in enumerate(elements):
            self.people_table.setItem(r, 0, QTableWidgetItem(str(element["name"])))
            self.people_table.setItem(r, 1, QTableWidgetItem(str(element["pricing_type"])))
            for c, person in enumerate(people, start=2):
                value = get_annual_person_rate(self.database, year, int(element["id"]), int(person["id"]))
                item = QTableWidgetItem("" if value is None else f"{value:.2f}")
                item.setData(32, int(element["id"]))
                item.setData(33, int(person["id"]))
                self.people_table.setItem(r, c, item)

    def _load_occupancy(self, year: int) -> None:
        elements = self._active_elements()
        people = self._active_people()
        self.occupancy_table.clear()
        self.occupancy_table.setRowCount(len(elements))
        self.occupancy_table.setColumnCount(2 + len(people))
        self.occupancy_table.setHorizontalHeaderLabels(["Element", "Max total"] + [str(p["name"]) for p in people])
        for r, element in enumerate(elements):
            element_id = int(element["id"])
            self.occupancy_table.setItem(r, 0, QTableWidgetItem(str(element["name"])))
            row = self.database.connection.execute(
                "SELECT max_total FROM annual_occupancy WHERE company_id=? AND year=? AND element_id=?",
                (self.database.company_id(), year, element_id),
            ).fetchone()
            total_item = QTableWidgetItem("" if row is None else str(int(row["max_total"])))
            total_item.setData(32, element_id)
            self.occupancy_table.setItem(r, 1, total_item)
            for c, person in enumerate(people, start=2):
                person_id = int(person["id"])
                limit = self.database.connection.execute(
                    "SELECT max_count FROM annual_person_limits WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?",
                    (self.database.company_id(), year, element_id, person_id),
                ).fetchone()
                if limit is None:
                    text = ""
                elif int(limit["max_count"]) < 0:
                    text = "No limit"
                else:
                    text = str(int(limit["max_count"]))
                item = QTableWidgetItem(text)
                item.setData(32, element_id)
                item.setData(33, person_id)
                self.occupancy_table.setItem(r, c, item)

    def _update_status(self, year: int) -> None:
        missing = validate_year(self.database, year)
        self.rates_status.setText("Seasonal rates complete." if missing["rates"] == 0 and missing["seasons"] == 0 else f"Missing seasonal setup: {missing['rates']} rate cells; {missing['seasons']} season definition issue(s).")
        self.people_status.setText("Person rates / supplements complete." if missing["people"] == 0 else f"Missing person pricing cells: {missing['people']}. Enter 0.00 explicitly when there is no supplement.")
        self.occupancy_status.setText("Occupancy grid complete." if missing["occupancy"] == 0 else f"Missing occupancy cells: {missing['occupancy']}. Use 0 where a type is not allowed; 'No limit' where appropriate.")
        total = sum(missing.values())
        if total == 0:
            self.overall_status.setText(f"{year} annual setup is complete.")
        else:
            self.overall_status.setText(
                f"{year} setup incomplete — seasonal rates: {missing['rates']}, person pricing: {missing['people']}, occupancy: {missing['occupancy']}, season issues: {missing['seasons']}."
            )

    def new_blank_year(self) -> None:
        default = (max(list_years(self.database)) + 1) if list_years(self.database) else date.today().year
        year, ok = QInputDialog.getInt(self, "New blank pricing year", "Year", default, 2000, 2200)
        if not ok:
            return
        try:
            create_blank_year(self.database, year)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot create year", str(exc))
            return
        self.refresh_years(year)

    def copy_year(self) -> None:
        default = (max(list_years(self.database)) + 1) if list_years(self.database) else date.today().year + 1
        year, ok = QInputDialog.getInt(self, "Copy previous pricing year", "New year", default, 2000, 2200)
        if not ok:
            return
        try:
            copy_previous_year(self.database, year)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot copy year", str(exc))
            return
        self.refresh_years(year)

    def save_all(self) -> None:
        year = self.current_year()
        if year is None:
            return
        seasons = seasons_for_year(self.database, year)
        elements = self._active_elements()
        people = self._active_people()
        try:
            for r, element in enumerate(elements):
                if str(element["pricing_type"]) not in PERSON_PRICING_TYPES:
                    for c, season in enumerate(seasons, start=2):
                        text = _item_text(self.rates_table, r, c)
                        if not text:
                            self.database.connection.execute(
                                "DELETE FROM annual_element_rates WHERE company_id=? AND year=? AND element_id=? AND season_id=?",
                                (self.database.company_id(), year, int(element["id"]), int(season["id"])),
                            )
                            continue
                        value = float(text.replace(",", "."))
                        if value < 0:
                            raise ValueError("Seasonal rates cannot be negative")
                        self.database.connection.execute(
                            "INSERT OR REPLACE INTO annual_element_rates(company_id, year, element_id, season_id, rate) VALUES (?, ?, ?, ?, ?)",
                            (self.database.company_id(), year, int(element["id"]), int(season["id"]), value),
                        )

                for c, person in enumerate(people, start=2):
                    text = _item_text(self.people_table, r, c)
                    if not text:
                        self.database.connection.execute(
                            "DELETE FROM annual_person_rates WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?",
                            (self.database.company_id(), year, int(element["id"]), int(person["id"])),
                        )
                    else:
                        value = float(text.replace(",", "."))
                        if value < 0:
                            raise ValueError("Person rates / supplements cannot be negative")
                        self.database.connection.execute(
                            "INSERT OR REPLACE INTO annual_person_rates(company_id, year, element_id, person_type_id, rate) VALUES (?, ?, ?, ?, ?)",
                            (self.database.company_id(), year, int(element["id"]), int(person["id"]), value),
                        )

                total_text = _item_text(self.occupancy_table, r, 1)
                if not total_text:
                    self.database.connection.execute(
                        "DELETE FROM annual_occupancy WHERE company_id=? AND year=? AND element_id=?",
                        (self.database.company_id(), year, int(element["id"])),
                    )
                else:
                    max_total = int(total_text)
                    if max_total < 0:
                        raise ValueError("Maximum total occupancy cannot be negative")
                    self.database.connection.execute(
                        "INSERT OR REPLACE INTO annual_occupancy(company_id, year, element_id, max_total) VALUES (?, ?, ?, ?)",
                        (self.database.company_id(), year, int(element["id"]), max_total),
                    )
                for c, person in enumerate(people, start=2):
                    text = _item_text(self.occupancy_table, r, c)
                    if not text:
                        self.database.connection.execute(
                            "DELETE FROM annual_person_limits WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?",
                            (self.database.company_id(), year, int(element["id"]), int(person["id"])),
                        )
                        continue
                    if text.lower() in {"no limit", "nl", "-"}:
                        value = -1
                    else:
                        value = int(text)
                        if value < 0:
                            raise ValueError("Occupancy limits must be 0 or greater, or 'No limit'")
                    self.database.connection.execute(
                        "INSERT OR REPLACE INTO annual_person_limits(company_id, year, element_id, person_type_id, max_count) VALUES (?, ?, ?, ?, ?)",
                        (self.database.company_id(), year, int(element["id"]), int(person["id"]), value),
                    )
            self.database.connection.commit()
        except (ValueError, TypeError) as exc:
            self.database.connection.rollback()
            QMessageBox.warning(self, "Cannot save annual grids", str(exc))
            return
        self.refresh_all()
        QMessageBox.information(self, "Saved", f"Annual grids for {year} saved.")
