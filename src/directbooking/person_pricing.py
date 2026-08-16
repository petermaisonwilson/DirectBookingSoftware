from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .database import Database


PERSON_RATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS element_person_rates (
    element_id INTEGER NOT NULL,
    person_type_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    rate REAL NOT NULL,
    PRIMARY KEY(element_id, person_type_id),
    FOREIGN KEY(company_id) REFERENCES companies(id),
    FOREIGN KEY(element_id) REFERENCES elements(id),
    FOREIGN KEY(person_type_id) REFERENCES person_types(id)
);
"""


def ensure_person_pricing_schema(database: Database) -> None:
    database.connection.executescript(PERSON_RATE_SCHEMA)
    database.connection.commit()


def get_element_person_rates(database: Database, element_id: int) -> dict[int, float]:
    ensure_person_pricing_schema(database)
    rows = database.connection.execute(
        "SELECT person_type_id, rate FROM element_person_rates WHERE element_id = ? AND company_id = ?",
        (int(element_id), database.company_id()),
    ).fetchall()
    return {int(row["person_type_id"]): float(row["rate"]) for row in rows}


def save_element_person_rates(database: Database, element_id: int, rates: dict[int, float | None]) -> None:
    ensure_person_pricing_schema(database)
    element = database.connection.execute(
        "SELECT id FROM elements WHERE id = ? AND company_id = ?",
        (int(element_id), database.company_id()),
    ).fetchone()
    if element is None:
        raise ValueError("Element does not exist")

    database.connection.execute(
        "DELETE FROM element_person_rates WHERE element_id = ? AND company_id = ?",
        (int(element_id), database.company_id()),
    )
    for person_type_id, rate in rates.items():
        if rate is None:
            continue
        rate = float(rate)
        if rate < 0:
            raise ValueError("Person rates cannot be negative")
        person_type = database.connection.execute(
            "SELECT id FROM person_types WHERE id = ? AND company_id = ?",
            (int(person_type_id), database.company_id()),
        ).fetchone()
        if person_type is None:
            raise ValueError("A selected person type no longer exists")
        database.connection.execute(
            "INSERT INTO element_person_rates(element_id, person_type_id, company_id, rate) VALUES (?, ?, ?, ?)",
            (int(element_id), int(person_type_id), database.company_id(), rate),
        )
    database.connection.commit()


class PersonPricingTab(QWidget):
    """Configure element-specific person rates/supplements."""

    def __init__(self, database: Database):
        super().__init__()
        self.database = database
        ensure_person_pricing_schema(database)
        self.rate_controls: dict[int, QDoubleSpinBox] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)

        help_text = QLabel(
            "Person values work in two ways. For Per person and Per person per night elements they are the actual "
            "rate charged for that person type. For Per night, Per day, Per stay and Per package elements they are "
            "supplements added on top of the element Base Price. Leave a supplement as 'No supplement' when that "
            "person type should not add anything to a fixed/base element charge."
        )
        help_text.setWordWrap(True)
        help_text.setObjectName("bodyText")
        layout.addWidget(help_text)

        top_form = QFormLayout()
        self.element = QComboBox()
        top_form.addRow("Element", self.element)
        layout.addLayout(top_form)

        self.base_price_note = QLabel("—")
        self.base_price_note.setObjectName("bodyText")
        layout.addWidget(self.base_price_note)

        heading = QLabel("Person rates / supplements")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)

        self.rates_container = QWidget()
        self.rates_form = QFormLayout(self.rates_container)
        layout.addWidget(self.rates_container)

        actions = QHBoxLayout()
        actions.addStretch()
        save = QPushButton("Save person rates")
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
            self.element.addItem(f"{row['name']} — {row['pricing_type']}{suffix}", int(row["id"]))
        if current is not None:
            index = self.element.findData(current)
            if index >= 0:
                self.element.setCurrentIndex(index)
        self.element.blockSignals(False)

    def refresh_person_types(self) -> None:
        while self.rates_form.rowCount():
            self.rates_form.removeRow(0)
        self.rate_controls.clear()
        rows = self.database.list_person_types(True)
        if not rows:
            message = QLabel("No person types have been defined yet. Add them on the Person types tab first.")
            message.setWordWrap(True)
            self.rates_form.addRow(message)
            return
        for row in rows:
            control = QDoubleSpinBox()
            control.setRange(-1.0, 1_000_000.0)
            control.setDecimals(2)
            control.setPrefix("€ ")
            control.setSpecialValueText("No supplement / use base")
            control.setValue(-1.0)
            label = row["name"] + ("" if row["active"] else " [Inactive]")
            self.rates_form.addRow(label, control)
            self.rate_controls[int(row["id"])] = control
        self.load_selected()

    def load_selected(self) -> None:
        element_id = self.element.currentData()
        if element_id is None:
            self.base_price_note.setText("No element selected")
            for control in self.rate_controls.values():
                control.setValue(-1.0)
            return
        element = next(
            (row for row in self.database.list_elements(True) if int(row["id"]) == int(element_id)),
            None,
        )
        if element is None:
            return
        pricing_type = str(element["pricing_type"])
        if pricing_type in {"Per person", "Per person per night"}:
            meaning = "Values below are person-specific rates; unset types use Base Price."
        else:
            meaning = "Values below are person supplements added on top of Base Price; unset types add nothing."
        self.base_price_note.setText(
            f"Base Price: €{float(element['base_price']):.2f} — Pricing type: {pricing_type}. {meaning}"
        )
        rates = get_element_person_rates(self.database, int(element_id))
        for person_type_id, control in self.rate_controls.items():
            control.setValue(float(rates[person_type_id]) if person_type_id in rates else -1.0)

    def save(self) -> None:
        element_id = self.element.currentData()
        if element_id is None:
            QMessageBox.information(self, "No element", "Create an element before setting person rates.")
            return
        rates = {
            person_type_id: (None if control.value() < 0 else control.value())
            for person_type_id, control in self.rate_controls.items()
        }
        try:
            save_element_person_rates(self.database, int(element_id), rates)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot save person rates", str(exc))
            return
        QMessageBox.information(self, "Saved", "Person rates / supplements saved.")
