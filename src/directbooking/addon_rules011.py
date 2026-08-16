from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .addon_inheritance import (
    delete_element_override,
    delete_group_addon_rule,
    get_group_addon_rule,
    list_element_types,
    resolve_addon_rule,
    save_group_addon_rule,
    validate_group_addon_year,
)
from .addon_model import get_addon_rule, list_addons, save_addon_rule
from .annual_config import list_years
from .database import Database

MISSING_BG = QColor(255, 224, 224)
ZERO_BG = QColor(255, 243, 205)
INHERIT_BG = QColor(232, 240, 249)
NO_BG = QColor(239, 241, 244)
NORMAL_BG = QColor(255, 255, 255)


class ElementAddonRules011Tab(QWidget):
    changed = Signal()

    def __init__(self, database: Database):
        super().__init__()
        self.database = database
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Pricing year"))
        self.year_combo = QComboBox()
        self.year_combo.currentIndexChanged.connect(self.refresh)
        controls.addWidget(self.year_combo)
        controls.addStretch()
        layout.addLayout(controls)

        help_text = QLabel(
            "Set normal Add-on rules once for each Element Type. Individual Elements inherit those rules automatically. "
            "Use Element overrides only for exceptions; Inherit removes the exception and returns to the Element Type rule."
        )
        help_text.setWordWrap(True)
        help_text.setObjectName("bodyText")
        layout.addWidget(help_text)

        self.pages = QTabWidget()
        self.type_page = self._build_type_page()
        self.override_page = self._build_override_page()
        self.pages.addTab(self.type_page, "Element Type defaults")
        self.pages.addTab(self.override_page, "Element overrides")
        layout.addWidget(self.pages, 1)
        self.refresh_years()

    def _build_type_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.type_status = QLabel()
        self.type_status.setWordWrap(True)
        self.type_status.setObjectName("bodyText")
        layout.addWidget(self.type_status)
        legend = QLabel(
            "Blank Available? = not reviewed and must be fixed.  No = unavailable for this Element Type.  "
            "Yes = enter Min, Max and Price.  A €0.00 price is valid and highlighted for review."
        )
        legend.setWordWrap(True)
        legend.setObjectName("bodyText")
        layout.addWidget(legend)
        self.type_table = QTableWidget(0, 7)
        self.type_table.setHorizontalHeaderLabels(["Element Type", "Add-on", "Pricing method", "Available?", "Min", "Max", "Price €"])
        self._configure(self.type_table)
        self.type_table.itemChanged.connect(self._style_type_rows)
        layout.addWidget(self.type_table, 1)
        actions = QHBoxLayout()
        actions.addStretch()
        save = QPushButton("Save Element Type defaults")
        save.clicked.connect(self.save_type_defaults)
        actions.addWidget(save)
        layout.addLayout(actions)
        return page

    def _build_override_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.override_status = QLabel()
        self.override_status.setWordWrap(True)
        self.override_status.setObjectName("bodyText")
        layout.addWidget(self.override_status)
        legend = QLabel(
            "Override = Inherit means use the Element Type default. Choose Yes or No only when this individual Element is an exception. "
            "A Yes override must contain Min, Max and Price."
        )
        legend.setWordWrap(True)
        legend.setObjectName("bodyText")
        layout.addWidget(legend)
        self.override_table = QTableWidget(0, 9)
        self.override_table.setHorizontalHeaderLabels(
            ["Element", "Element Type", "Add-on", "Pricing method", "Override", "Min", "Max", "Price €", "Effective rule"]
        )
        self._configure(self.override_table)
        self.override_table.itemChanged.connect(self._style_override_rows)
        layout.addWidget(self.override_table, 1)
        actions = QHBoxLayout()
        actions.addStretch()
        save = QPushButton("Save Element overrides")
        save.clicked.connect(self.save_overrides)
        actions.addWidget(save)
        layout.addLayout(actions)
        return page

    def _configure(self, table: QTableWidget) -> None:
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectItems)
        header = table.horizontalHeader()
        for column in range(table.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)

    def current_year(self) -> int | None:
        data = self.year_combo.currentData()
        return int(data) if data is not None else None

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
        self.refresh()

    def refresh(self) -> None:
        year = self.current_year()
        if year is None:
            self.type_table.setRowCount(0)
            self.override_table.setRowCount(0)
            self.type_status.setText("No pricing year is available.")
            self.override_status.setText("No pricing year is available.")
            return
        self._load_type_defaults(year)
        self._load_overrides(year)
        self._update_status(year)

    def _locked(self, text: str, data: int | None = None) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~item.flags().__class__.ItemIsEditable)
        if data is not None:
            item.setData(32, data)
        return item

    def _text(self, table: QTableWidget, row: int, col: int) -> str:
        item = table.item(row, col)
        return item.text().strip() if item else ""

    def _load_type_defaults(self, year: int) -> None:
        groups = list_element_types(self.database)
        addons = list_addons(self.database, False)
        table = self.type_table
        table.blockSignals(True)
        table.setRowCount(len(groups) * len(addons))
        row = 0
        for group_name in groups:
            for addon in addons:
                addon_id = int(addon["id"])
                table.setItem(row, 0, self._locked(group_name))
                table.setItem(row, 1, self._locked(str(addon["name"]), addon_id))
                table.setItem(row, 2, self._locked(str(addon["pricing_method"])))
                rule = get_group_addon_rule(self.database, year, group_name, addon_id)
                if rule is None:
                    values = ("", "", "", "")
                elif bool(rule["allowed"]):
                    values = (
                        "Yes",
                        "" if rule["min_qty"] is None else str(int(rule["min_qty"])),
                        "" if rule["max_qty"] is None else str(int(rule["max_qty"])),
                        "" if rule["rate"] is None else f"{float(rule['rate']):.2f}",
                    )
                else:
                    values = ("No", "", "", "")
                for col, value in enumerate(values, start=3):
                    table.setItem(row, col, QTableWidgetItem(value))
                row += 1
        table.blockSignals(False)
        self._style_type_rows()

    def _load_overrides(self, year: int) -> None:
        elements = self.database.list_elements(False)
        addons = list_addons(self.database, False)
        table = self.override_table
        table.blockSignals(True)
        table.setRowCount(len(elements) * len(addons))
        row = 0
        for element in elements:
            element_id = int(element["id"])
            group_name = str(element["group_name"] or "").strip() or "(No type)"
            for addon in addons:
                addon_id = int(addon["id"])
                table.setItem(row, 0, self._locked(str(element["name"]), element_id))
                table.setItem(row, 1, self._locked(group_name))
                table.setItem(row, 2, self._locked(str(addon["name"]), addon_id))
                table.setItem(row, 3, self._locked(str(addon["pricing_method"])))
                override = get_addon_rule(self.database, year, element_id, addon_id)
                if override is None:
                    values = ("Inherit", "", "", "")
                elif bool(override["allowed"]):
                    values = (
                        "Yes",
                        "" if override["min_qty"] is None else str(int(override["min_qty"])),
                        "" if override["max_qty"] is None else str(int(override["max_qty"])),
                        "" if override["rate"] is None else f"{float(override['rate']):.2f}",
                    )
                else:
                    values = ("No", "", "", "")
                for col, value in enumerate(values, start=4):
                    table.setItem(row, col, QTableWidgetItem(value))
                effective = resolve_addon_rule(self.database, year, element_id, addon_id)
                if not effective["configured"]:
                    effective_text = "No configured default"
                elif not effective["allowed"]:
                    effective_text = f"No — {effective['source']}"
                else:
                    effective_text = (
                        f"Yes {effective['min_qty']}–{effective['max_qty']} @ €{float(effective['rate']):.2f} — {effective['source']}"
                    )
                table.setItem(row, 8, self._locked(effective_text))
                row += 1
        table.blockSignals(False)
        self._style_override_rows()

    def _style_type_rows(self, *_args) -> None:
        table = self.type_table
        table.blockSignals(True)
        for row in range(table.rowCount()):
            state = self._text(table, row, 3).casefold()
            if state == "no":
                for col in range(3, 7):
                    table.item(row, col).setBackground(NO_BG)
            elif state == "yes":
                table.item(row, 3).setBackground(NORMAL_BG)
                for col in (4, 5, 6):
                    value = self._text(table, row, col)
                    if not value:
                        table.item(row, col).setBackground(MISSING_BG)
                    elif col == 6:
                        try:
                            table.item(row, col).setBackground(ZERO_BG if float(value.replace(",", ".")) == 0 else NORMAL_BG)
                        except ValueError:
                            table.item(row, col).setBackground(MISSING_BG)
                    else:
                        table.item(row, col).setBackground(NORMAL_BG)
            else:
                table.item(row, 3).setBackground(MISSING_BG)
                for col in (4, 5, 6):
                    table.item(row, col).setBackground(NORMAL_BG)
        table.blockSignals(False)

    def _style_override_rows(self, *_args) -> None:
        table = self.override_table
        table.blockSignals(True)
        for row in range(table.rowCount()):
            state = self._text(table, row, 4).casefold()
            if state == "inherit":
                for col in range(4, 8):
                    table.item(row, col).setBackground(INHERIT_BG)
            elif state == "no":
                for col in range(4, 8):
                    table.item(row, col).setBackground(NO_BG)
            elif state == "yes":
                table.item(row, 4).setBackground(NORMAL_BG)
                for col in (5, 6, 7):
                    value = self._text(table, row, col)
                    if not value:
                        table.item(row, col).setBackground(MISSING_BG)
                    elif col == 7:
                        try:
                            table.item(row, col).setBackground(ZERO_BG if float(value.replace(",", ".")) == 0 else NORMAL_BG)
                        except ValueError:
                            table.item(row, col).setBackground(MISSING_BG)
                    else:
                        table.item(row, col).setBackground(NORMAL_BG)
            else:
                table.item(row, 4).setBackground(MISSING_BG)
        table.blockSignals(False)

    def _scan_type_defaults(self) -> list[str]:
        errors: list[str] = []
        table = self.type_table
        for row in range(table.rowCount()):
            group_name = self._text(table, row, 0)
            addon = self._text(table, row, 1)
            state = self._text(table, row, 3).casefold()
            if state not in {"yes", "no"}:
                errors.append(f"{group_name} / {addon}: Available? must be Yes or No")
                continue
            if state == "yes":
                errors.extend(self._validate_values(table, row, 4, group_name, addon))
        return errors

    def _scan_overrides(self) -> list[str]:
        errors: list[str] = []
        table = self.override_table
        for row in range(table.rowCount()):
            element = self._text(table, row, 0)
            addon = self._text(table, row, 2)
            state = self._text(table, row, 4).casefold()
            if state not in {"inherit", "yes", "no"}:
                errors.append(f"{element} / {addon}: Override must be Inherit, Yes or No")
                continue
            if state == "yes":
                errors.extend(self._validate_values(table, row, 5, element, addon))
        return errors

    def _validate_values(self, table: QTableWidget, row: int, start_col: int, owner: str, addon: str) -> list[str]:
        minimum = self._text(table, row, start_col)
        maximum = self._text(table, row, start_col + 1)
        price = self._text(table, row, start_col + 2)
        if not minimum or not maximum or not price:
            return [f"{owner} / {addon}: Min, Max and Price are required when Yes"]
        try:
            min_value = int(minimum)
            max_value = int(maximum)
            rate = float(price.replace(",", "."))
        except ValueError:
            return [f"{owner} / {addon}: Min/Max must be whole numbers and Price must be numeric"]
        if min_value < 0 or max_value < min_value or rate < 0:
            return [f"{owner} / {addon}: check Min, Max and Price values"]
        return []

    def _update_status(self, year: int) -> None:
        type_errors = self._scan_type_defaults()
        override_errors = self._scan_overrides()
        persisted = validate_group_addon_year(self.database, year)
        if type_errors:
            self.type_status.setText(
                f"{year} Element Type Add-on setup needs attention: {len(type_errors)} row(s) are unreviewed or incomplete."
            )
        elif persisted["unreviewed"] or persisted["incomplete"]:
            self.type_status.setText(f"{year} Element Type defaults have unsaved changes. Save after reviewing all rows.")
        else:
            self.type_status.setText(f"{year} Element Type Add-on defaults are complete.")
        if override_errors:
            self.override_status.setText(f"{len(override_errors)} Element override row(s) need attention.")
        else:
            override_count = self.database.connection.execute(
                "SELECT COUNT(*) FROM annual_element_addons WHERE company_id=? AND year=?",
                (self.database.company_id(), year),
            ).fetchone()[0]
            self.override_status.setText(
                f"{override_count} individual Element override(s) stored. All other rows inherit their Element Type default."
            )

    def save_type_defaults(self) -> None:
        year = self.current_year()
        if year is None:
            return
        errors = self._scan_type_defaults()
        if errors:
            self._style_type_rows()
            QMessageBox.warning(self, "Cannot save Element Type defaults", errors[0] + (f"\n\n{len(errors)} row(s) need attention." if len(errors) > 1 else ""))
            return
        try:
            table = self.type_table
            for row in range(table.rowCount()):
                group_name = self._text(table, row, 0)
                addon_id = int(table.item(row, 1).data(32))
                state = self._text(table, row, 3).casefold()
                if state == "no":
                    save_group_addon_rule(self.database, year, group_name, addon_id, False)
                else:
                    save_group_addon_rule(
                        self.database, year, group_name, addon_id, True,
                        int(self._text(table, row, 4)),
                        int(self._text(table, row, 5)),
                        float(self._text(table, row, 6).replace(",", ".")),
                    )
            self.database.connection.commit()
        except ValueError as exc:
            self.database.connection.rollback()
            QMessageBox.warning(self, "Cannot save Element Type defaults", str(exc))
            return
        self.refresh()
        self.changed.emit()
        QMessageBox.information(self, "Saved", f"Element Type Add-on defaults for {year} saved.")

    def save_overrides(self) -> None:
        year = self.current_year()
        if year is None:
            return
        errors = self._scan_overrides()
        if errors:
            self._style_override_rows()
            QMessageBox.warning(self, "Cannot save Element overrides", errors[0] + (f"\n\n{len(errors)} row(s) need attention." if len(errors) > 1 else ""))
            return
        try:
            table = self.override_table
            for row in range(table.rowCount()):
                element_id = int(table.item(row, 0).data(32))
                addon_id = int(table.item(row, 2).data(32))
                state = self._text(table, row, 4).casefold()
                if state == "inherit":
                    delete_element_override(self.database, year, element_id, addon_id)
                elif state == "no":
                    save_addon_rule(self.database, year, element_id, addon_id, False)
                else:
                    save_addon_rule(
                        self.database, year, element_id, addon_id, True,
                        int(self._text(table, row, 5)),
                        int(self._text(table, row, 6)),
                        float(self._text(table, row, 7).replace(",", ".")),
                    )
            self.database.connection.commit()
        except ValueError as exc:
            self.database.connection.rollback()
            QMessageBox.warning(self, "Cannot save Element overrides", str(exc))
            return
        self.refresh()
        self.changed.emit()
        QMessageBox.information(self, "Saved", f"Element overrides for {year} saved.")
