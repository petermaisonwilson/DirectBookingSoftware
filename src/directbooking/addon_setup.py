from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .addon_model import (
    ADDON_PRICING_METHODS,
    delete_addon,
    ensure_addon_schema,
    get_addon_rule,
    list_addons,
    save_addon,
    save_addon_rule,
    set_addon_active,
    validate_addon_year,
)
from .annual_config import list_years
from .database import Database

MISSING_BG = QColor(255, 224, 224)
ZERO_BG = QColor(255, 243, 205)
NO_BG = QColor(239, 241, 244)
NORMAL_BG = QColor(255, 255, 255)


class AddonDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, row=None):
        super().__init__(parent)
        self.setWindowTitle("Add-on")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.pricing_method = QComboBox()
        self.pricing_method.addItems(ADDON_PRICING_METHODS)
        self.active = QCheckBox("Active")
        self.active.setChecked(True)
        form.addRow("Name", self.name)
        form.addRow("Pricing method", self.pricing_method)
        form.addRow("", self.active)
        layout.addLayout(form)
        help_text = QLabel(
            "An Add-on inherits the dates of the Element it is attached to. Its pricing method decides whether the charge is once, per quantity, or based on the parent Element duration."
        )
        help_text.setWordWrap(True)
        help_text.setObjectName("bodyText")
        layout.addWidget(help_text)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if row is not None:
            self.name.setText(str(row["name"]))
            index = self.pricing_method.findText(str(row["pricing_method"]))
            if index >= 0:
                self.pricing_method.setCurrentIndex(index)
            self.active.setChecked(bool(row["active"]))

    def values(self) -> dict:
        return {
            "name": self.name.text().strip(),
            "pricing_method": self.pricing_method.currentText(),
            "active": self.active.isChecked(),
        }


class AddonsTab(QWidget):
    changed = Signal()

    def __init__(self, database: Database):
        super().__init__()
        self.database = database
        ensure_addon_schema(database)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        actions = QHBoxLayout()
        add = QPushButton("Add Add-on")
        edit = QPushButton("Edit selected")
        toggle = QPushButton("Activate / inactivate")
        delete = QPushButton("Delete selected")
        add.clicked.connect(self.add_addon)
        edit.clicked.connect(self.edit_addon)
        toggle.clicked.connect(self.toggle_addon)
        delete.clicked.connect(self.delete_selected)
        for button in (add, edit, toggle, delete):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        help_text = QLabel(
            "Add-ons are extras attached to an Element and inherit that Element's dates. Examples: Dogs, Electric Hook-up, Extra Car or Landing Net hire."
        )
        help_text.setWordWrap(True)
        help_text.setObjectName("bodyText")
        layout.addWidget(help_text)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Add-on", "Pricing method", "Status", "ID"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setColumnHidden(3, True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(self.edit_addon)
        layout.addWidget(self.table, 1)
        self.refresh()

    def refresh(self) -> None:
        rows = list_addons(self.database, True)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            values = [str(row["name"]), str(row["pricing_method"]), "Active" if row["active"] else "Inactive", str(row["id"])]
            for c, value in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(value))

    def selected_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 3)
        return int(item.text()) if item else None

    def selected_row(self):
        addon_id = self.selected_id()
        if addon_id is None:
            return None
        return self.database.connection.execute(
            "SELECT * FROM add_ons WHERE company_id=? AND id=?",
            (self.database.company_id(), addon_id),
        ).fetchone()

    def add_addon(self) -> None:
        dialog = AddonDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            values = dialog.values()
            save_addon(self.database, None, **values)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot save Add-on", str(exc))
            return
        self.refresh()
        self.changed.emit()

    def edit_addon(self) -> None:
        row = self.selected_row()
        if row is None:
            QMessageBox.information(self, "Select Add-on", "Select an Add-on first.")
            return
        dialog = AddonDialog(self, row)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            save_addon(self.database, int(row["id"]), **dialog.values())
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot save Add-on", str(exc))
            return
        self.refresh()
        self.changed.emit()

    def toggle_addon(self) -> None:
        row = self.selected_row()
        if row is None:
            QMessageBox.information(self, "Select Add-on", "Select an Add-on first.")
            return
        set_addon_active(self.database, int(row["id"]), not bool(row["active"]))
        self.refresh()
        self.changed.emit()

    def delete_selected(self) -> None:
        row = self.selected_row()
        if row is None:
            QMessageBox.information(self, "Select Add-on", "Select an Add-on first.")
            return
        answer = QMessageBox.question(
            self,
            "Delete Add-on",
            f"Permanently delete {row['name']}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            delete_addon(self.database, int(row["id"]))
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot delete Add-on", str(exc))
            return
        self.refresh()
        self.changed.emit()


class ElementAddonRulesTab(QWidget):
    changed = Signal()

    def __init__(self, database: Database):
        super().__init__()
        self.database = database
        ensure_addon_schema(database)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Pricing year"))
        self.year_combo = QComboBox()
        self.year_combo.currentIndexChanged.connect(self.refresh)
        controls.addWidget(self.year_combo)
        controls.addStretch()
        layout.addLayout(controls)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setObjectName("bodyText")
        layout.addWidget(self.status)
        legend = QLabel(
            "Every Element/Add-on pair must be reviewed.  Blank Available? = not reviewed (must be fixed).  No = Add-on is not offered.  Yes = enter Min, Max and Price.  A zero Price is valid and highlighted for attention."
        )
        legend.setWordWrap(True)
        legend.setObjectName("bodyText")
        layout.addWidget(legend)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Element", "Add-on", "Pricing method", "Available?", "Min", "Max", "Price €"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        for col in (3, 4, 5, 6):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(self._style_rows)
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        actions.addStretch()
        save = QPushButton("Save Add-on rules")
        save.clicked.connect(self.save_all)
        actions.addWidget(save)
        layout.addLayout(actions)
        self.refresh_years()

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
            self.table.setRowCount(0)
            self.status.setText("No pricing year is available.")
            return
        elements = self.database.list_elements(False)
        addons = list_addons(self.database, False)
        self.table.blockSignals(True)
        self.table.setRowCount(len(elements) * len(addons))
        r = 0
        for element in elements:
            for addon in addons:
                self.table.setItem(r, 0, self._locked(str(element["name"]), int(element["id"])))
                self.table.setItem(r, 1, self._locked(str(addon["name"]), int(addon["id"])))
                self.table.setItem(r, 2, self._locked(str(addon["pricing_method"])))
                rule = get_addon_rule(self.database, year, int(element["id"]), int(addon["id"]))
                if rule is None:
                    available, minimum, maximum, price = "", "", "", ""
                elif bool(rule["allowed"]):
                    available = "Yes"
                    minimum = "" if rule["min_qty"] is None else str(int(rule["min_qty"]))
                    maximum = "" if rule["max_qty"] is None else str(int(rule["max_qty"]))
                    price = "" if rule["rate"] is None else f"{float(rule['rate']):.2f}"
                else:
                    available, minimum, maximum, price = "No", "", "", ""
                for c, text in ((3, available), (4, minimum), (5, maximum), (6, price)):
                    self.table.setItem(r, c, QTableWidgetItem(text))
                r += 1
        self.table.blockSignals(False)
        self._style_rows()
        self._update_status()

    def _locked(self, text: str, data: int | None = None) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~item.flags().__class__.ItemIsEditable)
        if data is not None:
            item.setData(32, data)
        return item

    def _text(self, row: int, col: int) -> str:
        item = self.table.item(row, col)
        return item.text().strip() if item else ""

    def _style_rows(self, *_args) -> None:
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            available = self._text(r, 3).casefold()
            if not available:
                self.table.item(r, 3).setBackground(MISSING_BG)
                for c in (4, 5, 6):
                    self.table.item(r, c).setBackground(NORMAL_BG)
                continue
            if available == "no":
                for c in (3, 4, 5, 6):
                    self.table.item(r, c).setBackground(NO_BG)
                continue
            self.table.item(r, 3).setBackground(NORMAL_BG if available == "yes" else MISSING_BG)
            if available == "yes":
                for c in (4, 5, 6):
                    text = self._text(r, c)
                    if not text:
                        self.table.item(r, c).setBackground(MISSING_BG)
                    elif c == 6:
                        try:
                            self.table.item(r, c).setBackground(ZERO_BG if float(text.replace(",", ".")) == 0 else NORMAL_BG)
                        except ValueError:
                            self.table.item(r, c).setBackground(MISSING_BG)
                    else:
                        self.table.item(r, c).setBackground(NORMAL_BG)
        self.table.blockSignals(False)

    def _scan(self) -> tuple[list[str], int]:
        errors: list[str] = []
        zero_prices = 0
        for r in range(self.table.rowCount()):
            element = self._text(r, 0)
            addon = self._text(r, 1)
            available = self._text(r, 3).casefold()
            if available not in {"yes", "no"}:
                errors.append(f"{element} / {addon}: Available? must be Yes or No")
                continue
            if available == "no":
                continue
            minimum = self._text(r, 4)
            maximum = self._text(r, 5)
            price = self._text(r, 6)
            if not minimum or not maximum or not price:
                errors.append(f"{element} / {addon}: Min, Max and Price are required when Available? is Yes")
                continue
            try:
                min_value = int(minimum)
                max_value = int(maximum)
                rate = float(price.replace(",", "."))
            except ValueError:
                errors.append(f"{element} / {addon}: Min/Max must be whole numbers and Price must be numeric")
                continue
            if min_value < 0 or max_value < min_value or rate < 0:
                errors.append(f"{element} / {addon}: check Min, Max and Price values")
                continue
            if rate == 0:
                zero_prices += 1
        return errors, zero_prices

    def _update_status(self) -> None:
        year = self.current_year()
        if year is None:
            return
        persisted = validate_addon_year(self.database, year)
        errors, zero_prices = self._scan()
        if errors:
            self.status.setText(
                f"{year} Add-on rules need attention: {len(errors)} row(s) are unreviewed or incomplete. "
                f"Every pair must explicitly say Yes or No. Zero-price rows are valid and are not errors."
            )
        elif persisted["unreviewed"] or persisted["incomplete"]:
            self.status.setText(f"{year} Add-on rules have unsaved or incomplete setup. Save after reviewing all rows.")
        else:
            suffix = f" {zero_prices} allowed Add-on price(s) are deliberately €0.00." if zero_prices else ""
            self.status.setText(f"{year} Add-on rules are complete.{suffix}")

    def save_all(self) -> None:
        year = self.current_year()
        if year is None:
            return
        errors, _zero_prices = self._scan()
        if errors:
            self._style_rows()
            self._update_status()
            QMessageBox.warning(
                self,
                "Add-on rules incomplete",
                "The Add-on rules cannot be saved yet. Every Element/Add-on pair must explicitly be Yes or No. "
                "For Yes rows, Min, Max and Price are required.\n\n" + errors[0],
            )
            return
        try:
            for r in range(self.table.rowCount()):
                element_id = int(self.table.item(r, 0).data(32))
                addon_id = int(self.table.item(r, 1).data(32))
                allowed = self._text(r, 3).casefold() == "yes"
                if allowed:
                    minimum = int(self._text(r, 4))
                    maximum = int(self._text(r, 5))
                    rate = float(self._text(r, 6).replace(",", "."))
                    save_addon_rule(self.database, year, element_id, addon_id, True, minimum, maximum, rate)
                else:
                    save_addon_rule(self.database, year, element_id, addon_id, False)
            self.database.connection.commit()
        except (ValueError, TypeError) as exc:
            self.database.connection.rollback()
            QMessageBox.warning(self, "Cannot save Add-on rules", str(exc))
            return
        self.refresh()
        self.changed.emit()
        QMessageBox.information(self, "Saved", f"Add-on rules for {year} saved.")
