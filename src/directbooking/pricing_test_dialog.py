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
    """Manual Build 004 calculator for validating configured pricing rules."""

    def __init__(self, database: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self.database = database
        self.setWindowTitle("Pricing Test - Build 004")
        self.setMinimumWidth(650)

        layout = QVBoxLayout(self)

        heading = QLabel("Pricing calculation test")
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)

        note = QLabel(
            "This test calculates a price only. It does not create an enquiry, offer or booking. "
            "Use it to prove pricing types and duration discounts before they are connected to live booking workflow."
        )
        note.setWordWrap(True)
        note.setObjectName("bodyText")
        layout.addWidget(note)

        form = QFormLayout()
        self.element = QComboBox()
        for row in database.list_elements(include_inactive=False):
            self.element.addItem(
                f"{row['name']} — {row['pricing_type']} — €{float(row['base_price']):.2f}",
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

        self.guests = QSpinBox()
        self.guests.setRange(1, 999)
        self.guests.setValue(1)

        form.addRow("Element", self.element)
        form.addRow("Arrival", self.arrival)
        form.addRow("Departure", self.departure)
        form.addRow("Guests", self.guests)
        layout.addLayout(form)

        actions = QHBoxLayout()
        self.calculate_button = QPushButton("Calculate price")
        self.calculate_button.clicked.connect(self.calculate)
        actions.addWidget(self.calculate_button)
        actions.addStretch()
        layout.addLayout(actions)

        self.result_panel = QFrame()
        self.result_panel.setObjectName("panel")
        result_layout = QFormLayout(self.result_panel)
        self.result_element = QLabel("—")
        self.result_duration = QLabel("—")
        self.result_pricing_type = QLabel("—")
        self.result_calculation = QLabel("—")
        self.result_base = QLabel("—")
        self.result_rule = QLabel("—")
        self.result_discount = QLabel("—")
        self.result_final = QLabel("—")
        self.result_final.setObjectName("sectionTitle")
        result_layout.addRow("Element", self.result_element)
        result_layout.addRow("Duration", self.result_duration)
        result_layout.addRow("Pricing type", self.result_pricing_type)
        result_layout.addRow("Base calculation", self.result_calculation)
        result_layout.addRow("Base amount", self.result_base)
        result_layout.addRow("Discount rule", self.result_rule)
        result_layout.addRow("Discount", self.result_discount)
        result_layout.addRow("Final price", self.result_final)
        layout.addWidget(self.result_panel)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if self.element.count() == 0:
            self.calculate_button.setEnabled(False)
            self.result_element.setText("No active elements are configured")

    def calculate(self) -> None:
        element_id = self.element.currentData()
        if element_id is None:
            QMessageBox.information(self, "No element", "There are no active elements to price.")
            return

        try:
            result = calculate_price(
                self.database,
                int(element_id),
                self.arrival.date().toString("yyyy-MM-dd"),
                self.departure.date().toString("yyyy-MM-dd"),
                self.guests.value(),
            )
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot calculate price", str(exc))
            return

        self.result_element.setText(str(result["element_name"]))
        self.result_duration.setText(
            f"{result['nights']} night{'s' if result['nights'] != 1 else ''}; "
            f"{result['days']} chargeable day{'s' if result['days'] != 1 else ''}; "
            f"{result['guests']} guest{'s' if result['guests'] != 1 else ''}"
        )
        self.result_pricing_type.setText(str(result["pricing_type"]))
        self.result_calculation.setText(str(result["calculation"]))
        self.result_base.setText(f"€ {float(result['base_amount']):.2f}")
        rule_name = str(result["discount_rule_name"])
        self.result_rule.setText(rule_name if rule_name else "No qualifying discount")
        self.result_discount.setText(f"− € {float(result['discount_amount']):.2f}")
        self.result_final.setText(f"€ {float(result['final_amount']):.2f}")
