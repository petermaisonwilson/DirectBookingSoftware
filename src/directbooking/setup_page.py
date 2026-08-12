from __future__ import annotations

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .database import Database


PRICING_TYPES = [
    "Per night",
    "Per day",
    "Per stay",
    "Per person",
    "Per person per night",
    "Per package",
]
DISCOUNT_TYPES = ["Percentage", "Fixed amount", "Free nights"]
DISCOUNT_SCOPES = ["All elements", "Group", "Element"]


def iso_to_qdate(value: str) -> QDate:
    parsed = QDate.fromString(value, "yyyy-MM-dd")
    return parsed if parsed.isValid() else QDate.currentDate()


def display_date(value: str) -> str:
    parsed = QDate.fromString(value, "yyyy-MM-dd")
    return parsed.toString("dd/MM/yyyy") if parsed.isValid() else value


class ElementDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, row=None):
        super().__init__(parent)
        self.setWindowTitle("Element")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.group_name = QLineEdit()
        self.pricing_type = QComboBox()
        self.pricing_type.addItems(PRICING_TYPES)
        self.base_price = QDoubleSpinBox()
        self.base_price.setRange(0, 1_000_000)
        self.base_price.setDecimals(2)
        self.base_price.setPrefix("€ ")
        self.active = QCheckBox("Active")
        self.active.setChecked(True)
        form.addRow("Name", self.name)
        form.addRow("Group", self.group_name)
        form.addRow("Pricing type", self.pricing_type)
        form.addRow("Base price", self.base_price)
        form.addRow("", self.active)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if row is not None:
            self.name.setText(row["name"])
            self.group_name.setText(row["group_name"])
            index = self.pricing_type.findText(row["pricing_type"])
            if index >= 0:
                self.pricing_type.setCurrentIndex(index)
            self.base_price.setValue(float(row["base_price"]))
            self.active.setChecked(bool(row["active"]))

    def values(self) -> dict:
        return {
            "name": self.name.text().strip(),
            "group_name": self.group_name.text().strip(),
            "pricing_type": self.pricing_type.currentText(),
            "base_price": self.base_price.value(),
            "active": self.active.isChecked(),
        }


class SeasonDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, row=None):
        super().__init__(parent)
        self.setWindowTitle("Season")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.start_date = QDateEdit()
        self.end_date = QDateEdit()
        for control in (self.start_date, self.end_date):
            control.setCalendarPopup(True)
            control.setDisplayFormat("dd/MM/yyyy")
            control.setDate(QDate.currentDate())
        self.priority = QSpinBox()
        self.priority.setRange(0, 999)
        self.active = QCheckBox("Active")
        self.active.setChecked(True)
        form.addRow("Name", self.name)
        form.addRow("Start date", self.start_date)
        form.addRow("End date", self.end_date)
        form.addRow("Priority", self.priority)
        form.addRow("", self.active)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if row is not None:
            self.name.setText(row["name"])
            self.start_date.setDate(iso_to_qdate(row["start_date"]))
            self.end_date.setDate(iso_to_qdate(row["end_date"]))
            self.priority.setValue(int(row["priority"]))
            self.active.setChecked(bool(row["active"]))

    def values(self) -> dict:
        return {
            "name": self.name.text().strip(),
            "start_date": self.start_date.date().toString("yyyy-MM-dd"),
            "end_date": self.end_date.date().toString("yyyy-MM-dd"),
            "priority": self.priority.value(),
            "active": self.active.isChecked(),
        }


class DiscountRuleDialog(QDialog):
    def __init__(self, database: Database, parent: QWidget | None = None, row=None):
        super().__init__(parent)
        self.database = database
        self.setWindowTitle("Duration discount rule")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.min_nights = QSpinBox()
        self.min_nights.setRange(1, 365)
        self.discount_type = QComboBox()
        self.discount_type.addItems(DISCOUNT_TYPES)
        self.discount_value = QDoubleSpinBox()
        self.discount_value.setRange(0.01, 1_000_000)
        self.discount_value.setDecimals(2)
        self.scope_type = QComboBox()
        self.scope_type.addItems(DISCOUNT_SCOPES)
        self.group_name = QComboBox()
        groups = sorted({item["group_name"] for item in database.list_elements(True) if item["group_name"]}, key=str.casefold)
        self.group_name.addItems(groups)
        self.element = QComboBox()
        for item in database.list_elements(True):
            self.element.addItem(f"{item['name']} ({item['group_name'] or 'No group'})", int(item["id"]))
        self.active = QCheckBox("Active")
        self.active.setChecked(True)
        form.addRow("Rule name", self.name)
        form.addRow("Minimum stay (nights)", self.min_nights)
        form.addRow("Discount type", self.discount_type)
        form.addRow("Discount value", self.discount_value)
        form.addRow("Applies to", self.scope_type)
        form.addRow("Group", self.group_name)
        form.addRow("Element", self.element)
        form.addRow("", self.active)
        layout.addLayout(form)
        note = QLabel("If several rules qualify, the single rule giving the customer the largest discount is used. Free-night rules apply only to night-based pricing.")
        note.setWordWrap(True)
        note.setObjectName("bodyText")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.scope_type.currentTextChanged.connect(self._refresh_scope_controls)
        self.discount_type.currentTextChanged.connect(self._refresh_value_control)
        if row is not None:
            self.name.setText(row["name"])
            self.min_nights.setValue(int(row["min_nights"]))
            type_index = self.discount_type.findText(row["discount_type"])
            if type_index >= 0:
                self.discount_type.setCurrentIndex(type_index)
            self.discount_value.setValue(float(row["discount_value"]))
            scope_index = self.scope_type.findText(row["scope_type"])
            if scope_index >= 0:
                self.scope_type.setCurrentIndex(scope_index)
            if row["group_name"]:
                group_index = self.group_name.findText(row["group_name"])
                if group_index >= 0:
                    self.group_name.setCurrentIndex(group_index)
            if row["element_id"] is not None:
                element_index = self.element.findData(int(row["element_id"]))
                if element_index >= 0:
                    self.element.setCurrentIndex(element_index)
            self.active.setChecked(bool(row["active"]))
        self._refresh_scope_controls()
        self._refresh_value_control()

    def _refresh_scope_controls(self) -> None:
        scope = self.scope_type.currentText()
        self.group_name.setEnabled(scope == "Group")
        self.element.setEnabled(scope == "Element")

    def _refresh_value_control(self) -> None:
        kind = self.discount_type.currentText()
        self.discount_value.setPrefix("")
        self.discount_value.setSuffix("")
        if kind == "Percentage":
            self.discount_value.setRange(0.01, 100)
            self.discount_value.setDecimals(2)
            self.discount_value.setSuffix(" %")
        elif kind == "Fixed amount":
            self.discount_value.setRange(0.01, 1_000_000)
            self.discount_value.setDecimals(2)
            self.discount_value.setPrefix("€ ")
        else:
            self.discount_value.setRange(1, 365)
            self.discount_value.setDecimals(0)
            self.discount_value.setSuffix(" nights")

    def values(self) -> dict:
        scope = self.scope_type.currentText()
        return {
            "name": self.name.text().strip(),
            "min_nights": self.min_nights.value(),
            "discount_type": self.discount_type.currentText(),
            "discount_value": self.discount_value.value(),
            "scope_type": scope,
            "element_id": self.element.currentData() if scope == "Element" else None,
            "group_name": self.group_name.currentText() if scope == "Group" else "",
            "active": self.active.isChecked(),
        }


class SetupPage(QWidget):
    data_changed = Signal()

    def __init__(self, database: Database):
        super().__init__()
        self.database = database
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)
        heading = QLabel("Setup")
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        intro = QLabel("Configure operator details, global booking rules, seasons, bookable elements and automatic duration discounts.")
        intro.setWordWrap(True)
        intro.setObjectName("bodyText")
        layout.addWidget(intro)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._operator_tab(), "Operator & reminders")
        self.tabs.addTab(self._seasons_tab(), "Seasons")
        self.tabs.addTab(self._elements_tab(), "Elements")
        self.tabs.addTab(self._discounts_tab(), "Discount rules")
        layout.addWidget(self.tabs, 1)
        self.load_settings()
        self.refresh_seasons()
        self.refresh_elements()
        self.refresh_discount_rules()

    def _operator_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        form = QFormLayout()
        form.setHorizontalSpacing(22)
        form.setVerticalSpacing(12)
        self.operator_name = QLineEdit()
        self.operator_address = QTextEdit()
        self.operator_address.setMaximumHeight(80)
        self.operator_email = QLineEdit()
        self.operator_phone = QLineEdit()
        self.offer_expiry_days = QSpinBox(); self.offer_expiry_days.setRange(0, 365)
        self.balance_due_weeks = QSpinBox(); self.balance_due_weeks.setRange(0, 52)
        self.deposit_mode = QComboBox(); self.deposit_mode.addItems(["Percentage", "Fixed amount"])
        self.deposit_percentage = QDoubleSpinBox(); self.deposit_percentage.setRange(0, 100); self.deposit_percentage.setSuffix(" %")
        self.deposit_fixed_amount = QDoubleSpinBox(); self.deposit_fixed_amount.setRange(0, 1_000_000); self.deposit_fixed_amount.setPrefix("€ ")
        self.small_booking_threshold = QDoubleSpinBox(); self.small_booking_threshold.setRange(0, 1_000_000); self.small_booking_threshold.setPrefix("€ ")
        self.balance_payment_weeks = QSpinBox(); self.balance_payment_weeks.setRange(0, 52)
        form.addRow("Business / operator name", self.operator_name)
        form.addRow("Address", self.operator_address)
        form.addRow("Email", self.operator_email)
        form.addRow("Phone", self.operator_phone)
        form.addRow("Offer expiry (days)", self.offer_expiry_days)
        form.addRow("Balance reminder (weeks before arrival)", self.balance_due_weeks)
        form.addRow("Deposit mode", self.deposit_mode)
        form.addRow("Deposit percentage", self.deposit_percentage)
        form.addRow("Fixed deposit amount", self.deposit_fixed_amount)
        form.addRow("Full-payment threshold", self.small_booking_threshold)
        form.addRow("Balance-payment period (weeks)", self.balance_payment_weeks)
        layout.addLayout(form)
        actions = QHBoxLayout(); actions.addStretch()
        save = QPushButton("Save operator settings"); save.clicked.connect(self.save_settings); actions.addWidget(save)
        layout.addLayout(actions); layout.addStretch()
        return page

    def _seasons_tab(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(16, 16, 16, 16)
        actions = QHBoxLayout()
        add = QPushButton("Add season"); edit = QPushButton("Edit selected"); toggle = QPushButton("Activate / inactivate")
        add.clicked.connect(self.add_season); edit.clicked.connect(self.edit_season); toggle.clicked.connect(self.toggle_season)
        for button in (add, edit, toggle): actions.addWidget(button)
        actions.addStretch(); layout.addLayout(actions)
        self.seasons_table = QTableWidget(0, 6)
        self.seasons_table.setHorizontalHeaderLabels(["Name", "Start", "End", "Priority", "Status", "ID"])
        self._configure_table(self.seasons_table, 5)
        self.seasons_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col in (1, 2, 3, 4): self.seasons_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.seasons_table.doubleClicked.connect(self.edit_season)
        layout.addWidget(self.seasons_table, 1)
        return page

    def _elements_tab(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(16, 16, 16, 16)
        actions = QHBoxLayout()
        add = QPushButton("Add element"); edit = QPushButton("Edit selected"); toggle = QPushButton("Activate / inactivate"); delete = QPushButton("Delete selected")
        add.clicked.connect(self.add_element); edit.clicked.connect(self.edit_element); toggle.clicked.connect(self.toggle_element); delete.clicked.connect(self.delete_element)
        for button in (add, edit, toggle, delete): actions.addWidget(button)
        actions.addStretch(); layout.addLayout(actions)
        help_text = QLabel("Delete is permanent and is only allowed for an element that has never been used in an offer or booking. Otherwise use Inactive.")
        help_text.setWordWrap(True); help_text.setObjectName("bodyText"); layout.addWidget(help_text)
        self.elements_table = QTableWidget(0, 6)
        self.elements_table.setHorizontalHeaderLabels(["Name", "Group", "Pricing type", "Base price", "Status", "ID"])
        self._configure_table(self.elements_table, 5)
        self.elements_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.elements_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for col in (2, 3, 4): self.elements_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.elements_table.doubleClicked.connect(self.edit_element)
        layout.addWidget(self.elements_table, 1)
        return page

    def _discounts_tab(self) -> QWidget:
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(16, 16, 16, 16)
        actions = QHBoxLayout()
        add = QPushButton("Add discount rule"); edit = QPushButton("Edit selected"); toggle = QPushButton("Activate / inactivate")
        add.clicked.connect(self.add_discount_rule); edit.clicked.connect(self.edit_discount_rule); toggle.clicked.connect(self.toggle_discount_rule)
        for button in (add, edit, toggle): actions.addWidget(button)
        actions.addStretch(); layout.addLayout(actions)
        help_text = QLabel("Rules are based on stay duration. They may apply to all elements, a group or one element. If more than one qualifies, only the best customer discount is applied.")
        help_text.setWordWrap(True); help_text.setObjectName("bodyText"); layout.addWidget(help_text)
        self.discounts_table = QTableWidget(0, 7)
        self.discounts_table.setHorizontalHeaderLabels(["Name", "Minimum stay", "Discount", "Applies to", "Status", "ID", "Value"])
        self._configure_table(self.discounts_table, 5)
        self.discounts_table.hideColumn(6)
        self.discounts_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.discounts_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        for col in (1, 2, 4): self.discounts_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.discounts_table.doubleClicked.connect(self.edit_discount_rule)
        layout.addWidget(self.discounts_table, 1)
        return page

    def _configure_table(self, table: QTableWidget, id_column: int) -> None:
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.hideColumn(id_column)

    def load_settings(self) -> None:
        values = self.database.get_settings()
        self.operator_name.setText(values["operator_name"])
        self.operator_address.setPlainText(values["operator_address"])
        self.operator_email.setText(values["operator_email"])
        self.operator_phone.setText(values["operator_phone"])
        self.offer_expiry_days.setValue(int(float(values["offer_expiry_days"] or 0)))
        self.balance_due_weeks.setValue(int(float(values["balance_due_weeks"] or 0)))
        index = self.deposit_mode.findText(values["deposit_mode"]); self.deposit_mode.setCurrentIndex(index if index >= 0 else 0)
        self.deposit_percentage.setValue(float(values["deposit_percentage"] or 0))
        self.deposit_fixed_amount.setValue(float(values["deposit_fixed_amount"] or 0))
        self.small_booking_threshold.setValue(float(values["small_booking_threshold"] or 0))
        self.balance_payment_weeks.setValue(int(float(values["balance_payment_weeks"] or 0)))

    def save_settings(self) -> None:
        if not self.operator_name.text().strip():
            QMessageBox.warning(self, "Missing name", "Business / operator name is required."); return
        self.database.save_settings({
            "operator_name": self.operator_name.text().strip(),
            "operator_address": self.operator_address.toPlainText().strip(),
            "operator_email": self.operator_email.text().strip(),
            "operator_phone": self.operator_phone.text().strip(),
            "offer_expiry_days": str(self.offer_expiry_days.value()),
            "balance_due_weeks": str(self.balance_due_weeks.value()),
            "deposit_mode": self.deposit_mode.currentText(),
            "deposit_percentage": str(self.deposit_percentage.value()),
            "deposit_fixed_amount": str(self.deposit_fixed_amount.value()),
            "small_booking_threshold": str(self.small_booking_threshold.value()),
            "balance_payment_weeks": str(self.balance_payment_weeks.value()),
        })
        self.data_changed.emit(); QMessageBox.information(self, "Saved", "Operator settings saved.")

    def refresh_elements(self) -> None:
        rows = self.database.list_elements(True); self.elements_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = [row["name"], row["group_name"], row["pricing_type"], f"€ {float(row['base_price']):.2f}", "Active" if row["active"] else "Inactive", str(row["id"])]
            for column, value in enumerate(values): self.elements_table.setItem(index, column, QTableWidgetItem(value))

    def refresh_seasons(self) -> None:
        rows = self.database.list_seasons(True); self.seasons_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = [row["name"], display_date(row["start_date"]), display_date(row["end_date"]), str(row["priority"]), "Active" if row["active"] else "Inactive", str(row["id"])]
            for column, value in enumerate(values): self.seasons_table.setItem(index, column, QTableWidgetItem(value))

    def refresh_discount_rules(self) -> None:
        rows = self.database.list_discount_rules(True); self.discounts_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            if row["discount_type"] == "Percentage": discount = f"{float(row['discount_value']):g}%"
            elif row["discount_type"] == "Fixed amount": discount = f"€ {float(row['discount_value']):.2f}"
            else: discount = f"{int(row['discount_value'])} free night(s)"
            if row["scope_type"] == "Element": applies = f"Element: {row['element_name'] or 'Missing element'}"
            elif row["scope_type"] == "Group": applies = f"Group: {row['group_name']}"
            else: applies = "All elements"
            values = [row["name"], f"{row['min_nights']} nights", discount, applies, "Active" if row["active"] else "Inactive", str(row["id"]), str(row["discount_value"])]
            for column, value in enumerate(values): self.discounts_table.setItem(index, column, QTableWidgetItem(value))

    def _selected_id(self, table: QTableWidget, id_column: int = 5) -> int | None:
        row = table.currentRow()
        if row < 0: return None
        item = table.item(row, id_column)
        return int(item.text()) if item else None

    def add_element(self) -> None:
        dialog = ElementDialog(self)
        if dialog.exec() != QDialog.Accepted: return
        try: self.database.save_element(None, **dialog.values())
        except ValueError as exc: QMessageBox.warning(self, "Cannot save element", str(exc)); return
        self.refresh_elements(); self.refresh_discount_rules(); self.data_changed.emit()

    def edit_element(self) -> None:
        element_id = self._selected_id(self.elements_table)
        if element_id is None: QMessageBox.information(self, "Select an element", "Select an element first."); return
        row = next(row for row in self.database.list_elements(True) if row["id"] == element_id)
        dialog = ElementDialog(self, row)
        if dialog.exec() != QDialog.Accepted: return
        try: self.database.save_element(element_id, **dialog.values())
        except ValueError as exc: QMessageBox.warning(self, "Cannot save element", str(exc)); return
        self.refresh_elements(); self.refresh_discount_rules(); self.data_changed.emit()

    def toggle_element(self) -> None:
        element_id = self._selected_id(self.elements_table)
        if element_id is None: QMessageBox.information(self, "Select an element", "Select an element first."); return
        row = next(row for row in self.database.list_elements(True) if row["id"] == element_id)
        self.database.set_element_active(element_id, not bool(row["active"]))
        self.refresh_elements(); self.data_changed.emit()

    def delete_element(self) -> None:
        element_id = self._selected_id(self.elements_table)
        if element_id is None: QMessageBox.information(self, "Select an element", "Select an element first."); return
        row = next(row for row in self.database.list_elements(True) if row["id"] == element_id)
        answer = QMessageBox.question(self, "Delete element", f"Permanently delete '{row['name']}'?\n\nThis cannot be undone.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes: return
        try: self.database.delete_element(element_id)
        except ValueError as exc: QMessageBox.warning(self, "Cannot delete element", str(exc)); return
        self.refresh_elements(); self.refresh_discount_rules(); self.data_changed.emit()

    def add_season(self) -> None:
        dialog = SeasonDialog(self)
        if dialog.exec() != QDialog.Accepted: return
        try: self.database.save_season(None, **dialog.values())
        except ValueError as exc: QMessageBox.warning(self, "Cannot save season", str(exc)); return
        self.refresh_seasons(); self.data_changed.emit()

    def edit_season(self) -> None:
        season_id = self._selected_id(self.seasons_table)
        if season_id is None: QMessageBox.information(self, "Select a season", "Select a season first."); return
        row = next(row for row in self.database.list_seasons(True) if row["id"] == season_id)
        dialog = SeasonDialog(self, row)
        if dialog.exec() != QDialog.Accepted: return
        try: self.database.save_season(season_id, **dialog.values())
        except ValueError as exc: QMessageBox.warning(self, "Cannot save season", str(exc)); return
        self.refresh_seasons(); self.data_changed.emit()

    def toggle_season(self) -> None:
        season_id = self._selected_id(self.seasons_table)
        if season_id is None: QMessageBox.information(self, "Select a season", "Select a season first."); return
        row = next(row for row in self.database.list_seasons(True) if row["id"] == season_id)
        self.database.set_season_active(season_id, not bool(row["active"]))
        self.refresh_seasons(); self.data_changed.emit()

    def add_discount_rule(self) -> None:
        dialog = DiscountRuleDialog(self.database, self)
        if dialog.exec() != QDialog.Accepted: return
        try: self.database.save_discount_rule(None, **dialog.values())
        except ValueError as exc: QMessageBox.warning(self, "Cannot save discount rule", str(exc)); return
        self.refresh_discount_rules(); self.data_changed.emit()

    def edit_discount_rule(self) -> None:
        rule_id = self._selected_id(self.discounts_table)
        if rule_id is None: QMessageBox.information(self, "Select a discount rule", "Select a discount rule first."); return
        row = next(row for row in self.database.list_discount_rules(True) if row["id"] == rule_id)
        dialog = DiscountRuleDialog(self.database, self, row)
        if dialog.exec() != QDialog.Accepted: return
        try: self.database.save_discount_rule(rule_id, **dialog.values())
        except ValueError as exc: QMessageBox.warning(self, "Cannot save discount rule", str(exc)); return
        self.refresh_discount_rules(); self.data_changed.emit()

    def toggle_discount_rule(self) -> None:
        rule_id = self._selected_id(self.discounts_table)
        if rule_id is None: QMessageBox.information(self, "Select a discount rule", "Select a discount rule first."); return
        row = next(row for row in self.database.list_discount_rules(True) if row["id"] == rule_id)
        self.database.set_discount_rule_active(rule_id, not bool(row["active"]))
        self.refresh_discount_rules(); self.data_changed.emit()
