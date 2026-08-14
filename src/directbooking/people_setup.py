from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QComboBox,
)

from .database import Database


class PersonTypeDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, row=None):
        super().__init__(parent)
        self.setWindowTitle("Person type")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit()
        self.short_label = QLineEdit()
        self.short_label.setMaxLength(12)
        self.active = QCheckBox("Active")
        self.active.setChecked(True)
        form.addRow("Name", self.name)
        form.addRow("Short label", self.short_label)
        form.addRow("", self.active)
        layout.addLayout(form)
        note = QLabel("Examples: Adult / Ad, Child / Ch, Infant / Inf, Angler / Ang. You can create as many person types as your business needs.")
        note.setWordWrap(True)
        note.setObjectName("bodyText")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if row is not None:
            self.name.setText(row["name"])
            self.short_label.setText(row["short_label"])
            self.active.setChecked(bool(row["active"]))

    def values(self) -> dict:
        return {
            "name": self.name.text().strip(),
            "short_label": self.short_label.text().strip(),
            "active": self.active.isChecked(),
        }


class PersonTypesTab(QWidget):
    changed = Signal()

    def __init__(self, database: Database):
        super().__init__()
        self.database = database
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        actions = QHBoxLayout()
        add = QPushButton("Add person type")
        edit = QPushButton("Edit selected")
        toggle = QPushButton("Activate / inactivate")
        add.clicked.connect(self.add_person_type)
        edit.clicked.connect(self.edit_person_type)
        toggle.clicked.connect(self.toggle_person_type)
        for button in (add, edit, toggle):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        help_text = QLabel(
            "Define the kinds of people your business needs to count. Person types are operator-defined and unlimited. "
            "Inactivate old types rather than removing them so future booking history can remain understandable."
        )
        help_text.setWordWrap(True)
        help_text.setObjectName("bodyText")
        layout.addWidget(help_text)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Name", "Short label", "Status", "ID"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.hideColumn(3)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(self.edit_person_type)
        layout.addWidget(self.table, 1)
        self.refresh()

    def refresh(self) -> None:
        rows = self.database.list_person_types(True)
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = [row["name"], row["short_label"], "Active" if row["active"] else "Inactive", str(row["id"])]
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(value))

    def _selected_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 3)
        return int(item.text()) if item else None

    def add_person_type(self) -> None:
        dialog = PersonTypeDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            self.database.save_person_type(None, **dialog.values())
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot save person type", str(exc))
            return
        self.refresh()
        self.changed.emit()

    def edit_person_type(self) -> None:
        person_type_id = self._selected_id()
        if person_type_id is None:
            QMessageBox.information(self, "Select a person type", "Select a person type first.")
            return
        row = next(row for row in self.database.list_person_types(True) if row["id"] == person_type_id)
        dialog = PersonTypeDialog(self, row)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            self.database.save_person_type(person_type_id, **dialog.values())
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot save person type", str(exc))
            return
        self.refresh()
        self.changed.emit()

    def toggle_person_type(self) -> None:
        person_type_id = self._selected_id()
        if person_type_id is None:
            QMessageBox.information(self, "Select a person type", "Select a person type first.")
            return
        row = next(row for row in self.database.list_person_types(True) if row["id"] == person_type_id)
        self.database.set_person_type_active(person_type_id, not bool(row["active"]))
        self.refresh()
        self.changed.emit()


class OccupancyTab(QWidget):
    changed = Signal()

    def __init__(self, database: Database):
        super().__init__()
        self.database = database
        self.person_controls: dict[int, QSpinBox] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        help_text = QLabel(
            "Set the maximum total number of people allowed on each element, plus optional limits for individual person types. "
            "For example: Pitch max 4 total; Adult max 4; Child max 4. Fishing Peg max 1 total; Adult max 1; Child max 0."
        )
        help_text.setWordWrap(True)
        help_text.setObjectName("bodyText")
        layout.addWidget(help_text)

        top_form = QFormLayout()
        self.element = QComboBox()
        self.max_total = QSpinBox()
        self.max_total.setRange(0, 999)
        self.max_total.setSpecialValueText("No overall limit")
        top_form.addRow("Element", self.element)
        top_form.addRow("Maximum total persons", self.max_total)
        layout.addLayout(top_form)

        type_heading = QLabel("Maximum by person type")
        type_heading.setObjectName("sectionTitle")
        layout.addWidget(type_heading)
        type_note = QLabel("Set a type to 'No limit' if only the overall element capacity should control it. Set it to 0 if that person type is not allowed on the element.")
        type_note.setWordWrap(True)
        type_note.setObjectName("bodyText")
        layout.addWidget(type_note)

        self.type_container = QWidget()
        self.type_form = QFormLayout(self.type_container)
        layout.addWidget(self.type_container)
        actions = QHBoxLayout()
        actions.addStretch()
        save = QPushButton("Save occupancy limits")
        save.clicked.connect(self.save)
        actions.addWidget(save)
        layout.addLayout(actions)
        layout.addStretch()

        self.element.currentIndexChanged.connect(self.load_selected)
        self.refresh_elements()
        self.refresh_person_types()
        self.load_selected()

    def refresh_elements(self) -> None:
        current = self.element.currentData()
        self.element.blockSignals(True)
        self.element.clear()
        for row in self.database.list_elements(True):
            suffix = "" if row["active"] else " [Inactive]"
            self.element.addItem(f"{row['name']} ({row['group_name'] or 'No group'}){suffix}", int(row["id"]))
        if current is not None:
            index = self.element.findData(current)
            if index >= 0:
                self.element.setCurrentIndex(index)
        self.element.blockSignals(False)

    def refresh_person_types(self) -> None:
        while self.type_form.rowCount():
            self.type_form.removeRow(0)
        self.person_controls.clear()
        rows = self.database.list_person_types(True)
        if not rows:
            message = QLabel("No person types have been defined yet. Add them on the Person types tab first.")
            message.setWordWrap(True)
            self.type_form.addRow(message)
            return
        for row in rows:
            control = QSpinBox()
            control.setRange(-1, 999)
            control.setSpecialValueText("No limit")
            control.setValue(-1)
            label = row["name"] + ("" if row["active"] else " [Inactive]")
            self.type_form.addRow(label, control)
            self.person_controls[int(row["id"])] = control
        self.load_selected()

    def load_selected(self) -> None:
        element_id = self.element.currentData()
        if element_id is None:
            self.max_total.setValue(0)
            for control in self.person_controls.values():
                control.setValue(-1)
            return
        try:
            capacity = self.database.get_element_capacity(int(element_id))
        except ValueError:
            return
        self.max_total.setValue(int(capacity["max_total"]))
        limits = capacity["limits"]
        for person_type_id, control in self.person_controls.items():
            control.setValue(int(limits[person_type_id]) if person_type_id in limits else -1)

    def save(self) -> None:
        element_id = self.element.currentData()
        if element_id is None:
            QMessageBox.information(self, "No element", "Create an element before setting occupancy limits.")
            return
        limits = {
            person_type_id: (None if control.value() < 0 else control.value())
            for person_type_id, control in self.person_controls.items()
        }
        try:
            self.database.save_element_capacity(int(element_id), self.max_total.value(), limits)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot save occupancy", str(exc))
            return
        self.changed.emit()
        QMessageBox.information(self, "Saved", "Occupancy limits saved.")
