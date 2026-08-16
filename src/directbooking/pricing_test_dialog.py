from __future__ import annotations

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .database import Database
from .pricing import calculate_price


class PricingTestDialog(QDialog):
    """Build 008 calculator for annual/seasonal combined element pricing."""

    def __init__(self, database: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.database = database
        self.person_controls: dict[int, QSpinBox] = {}
        self.setWindowTitle("Pricing Test - Build 008")
        self.setMinimumWidth(760)

        layout = QVBoxLayout(self)

        heading = QLabel("Annual combined pricing test")
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)

        note = QLabel(
            "This calculates one complete element price only; it does not create an enquiry, offer or booking. "
            "Build 008 uses the annual grids for the selected dates and blocks the calculation if required annual data is missing."
        )
        note.setWordWrap(True)
        note.setObjectName("bodyText")
        layout.addWidget(note)

        form = QFormLayout()
        self.element = QComboBox()
        for row in database.list_elements(include_inactive=False):
            self.element.addItem(
                f"{row['name']} — {row['pricing_type']} — Base €{float(row['base_price']):.2f}",
                int(row["id"]),
            )

        self.arrival = QDateEdit()
        self.departure = QDateEdit()
        for control in (self.arrival, self.departure):
            control.setCalendarPopup(True)
            control.setDisplayFormat("dd/MM/yyyy")
        today = QDate.currentDate()
        self.arrival.setDate(today)
        self.departure.setDate(today.addDays(1))

        form.addRow("Element", self.element)
        form.addRow("Arrival", self.arrival)
        form.addRow("Departure", self.departure)
        layout.addLayout(form)

        people_heading = QLabel("People")
        people_heading.setObjectName("sectionTitle")
        layout.addWidget(people_heading)
        self.people_form = QFormLayout()
        active_types = database.list_person_types(False)
        for index, row in enumerate(active_types):
            control = QSpinBox()
            control.setRange(0, 999)
            control.setValue(1 if index == 0 else 0)
            self.people_form.addRow(f"{row['name']} ({row['short_label']})", control)
            self.person_controls[int(row["id"])] = control
        if not active_types:
            self.people_form.addRow(QLabel("No active person types. Add one in Setup → Person types."))
        layout.addLayout(self.people_form)

        actions = QHBoxLayout()
        self.calculate_button = QPushButton("Calculate price")
        self.calculate_button.clicked.connect(self.calculate)
        self.calculate_button.setEnabled(bool(active_types) and self.element.count() > 0)
        actions.addWidget(self.calculate_button)
        actions.addStretch()
        layout.addLayout(actions)

        self.result_panel = QFrame()
        self.result_panel.setObjectName("panel")
        result_layout = QFormLayout(self.result_panel)
        self.result_element = QLabel("—")
        self.result_duration = QLabel("—")
        self.result_people = QLabel("—")
        self.result_pricing_type = QLabel("—")
        self.result_calculation = QLabel("—")
        self.result_calculation.setWordWrap(True)
        self.result_element_base = QLabel("—")
        self.result_person_amount = QLabel("—")
        self.result_base = QLabel("—")
        self.result_rule = QLabel("—")
        self.result_discount = QLabel("—")
        self.result_final = QLabel("—")
        self.result_final.setObjectName("sectionTitle")
        result_layout.addRow("Element", self.result_element)
        result_layout.addRow("Duration", self.result_duration)
        result_layout.addRow("People", self.result_people)
        result_layout.addRow("Pricing type", self.result_pricing_type)
        result_layout.addRow("Calculation", self.result_calculation)
        result_layout.addRow("Element base charge", self.result_element_base)
        result_layout.addRow("Person charges", self.result_person_amount)
        result_layout.addRow("Combined before discount", self.result_base)
        result_layout.addRow("Discount rule", self.result_rule)
        result_layout.addRow("Discount", self.result_discount)
        result_layout.addRow("Final price", self.result_final)
        layout.addWidget(self.result_panel)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if self.element.count() == 0:
            self.result_element.setText("No active elements are configured")

    def calculate(self) -> None:
        element_id = self.element.currentData()
        if element_id is None:
            QMessageBox.information(self, "No element", "There are no active elements to price.")
            return

        person_counts = {person_type_id: control.value() for person_type_id, control in self.person_controls.items()}
        try:
            result = calculate_price(
                self.database,
                int(element_id),
                self.arrival.date().toString("yyyy-MM-dd"),
                self.departure.date().toString("yyyy-MM-dd"),
                person_counts=person_counts,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot calculate price", str(exc))
            return

        self.result_element.setText(str(result["element_name"]))
        self.result_duration.setText(
            f"{result['nights']} night{'s' if result['nights'] != 1 else ''}; "
            f"{result['days']} chargeable day{'s' if result['days'] != 1 else ''}"
        )
        self.result_people.setText(str(result["people_summary"]))
        self.result_pricing_type.setText(str(result["pricing_type"]))
        self.result_calculation.setText(str(result["calculation"]))
        self.result_element_base.setText(f"€ {float(result['element_base_amount']):.2f}")
        self.result_person_amount.setText(f"€ {float(result['person_amount']):.2f}")
        self.result_base.setText(f"€ {float(result['base_amount']):.2f}")
        rule_name = str(result["discount_rule_name"])
        self.result_rule.setText(rule_name if rule_name else "No qualifying discount")
        self.result_discount.setText(f"− € {float(result['discount_amount']):.2f}")
        self.result_final.setText(f"€ {float(result['final_amount']):.2f}")
