from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .database import Database
from .people_setup import OccupancyTab, PersonTypesTab
from .pricing_test_dialog import PricingTestDialog
from .setup_page import SetupPage


NAV_ITEMS = ["Dashboard", "Enquiries", "Availability", "Bookings", "Finance", "Setup"]


class MetricCard(QFrame):
    def __init__(self, title: str, value: str):
        super().__init__()
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        title_label = QLabel(title)
        title_label.setObjectName("metricTitle")
        self.value_label = QLabel(value)
        self.value_label.setObjectName("metricValue")
        layout.addWidget(title_label)
        layout.addWidget(self.value_label)

    def set_value(self, value: int) -> None:
        self.value_label.setText(str(value))


class MainWindow(QMainWindow):
    def __init__(self, database: Database):
        super().__init__()
        self.database = database
        self.setWindowTitle("Direct Booking Software - Build 005")
        self.resize(1280, 800)
        self.setMinimumSize(1000, 650)

        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setObjectName("navigation")
        self.nav.setFixedWidth(220)
        self.nav.setFocusPolicy(Qt.NoFocus)
        for name in NAV_ITEMS:
            item = QListWidgetItem(name)
            item.setSizeHint(item.sizeHint().expandedTo(item.sizeHint()))
            self.nav.addItem(item)
        root_layout.addWidget(self.nav)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 22, 28, 24)
        content_layout.setSpacing(16)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        app_title = QLabel("Direct Booking Software")
        app_title.setObjectName("appTitle")
        subtitle = QLabel("Booking handling for independent operators")
        subtitle.setObjectName("subtitle")
        title_box.addWidget(app_title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch()
        build = QLabel("Build 005")
        build.setObjectName("buildBadge")
        header.addWidget(build)
        content_layout.addLayout(header)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._dashboard_page())
        self.stack.addWidget(self._placeholder_page("Enquiries", "Enquiry intake and offer preparation will be added in a later build."))
        self.stack.addWidget(self._placeholder_page("Availability", "The large element-by-date availability board will be added in a later build."))
        self.stack.addWidget(self._placeholder_page("Bookings", "Confirmed bookings, amendments, payments and booking audit logs will be added in later builds."))
        self.stack.addWidget(self._placeholder_page("Finance", "The booking ledger and global transaction view will be added in a later build."))
        self.setup_page = SetupPage(database)
        self.person_types_tab = PersonTypesTab(database)
        self.occupancy_tab = OccupancyTab(database)
        self.setup_page.tabs.addTab(self.person_types_tab, "Person types")
        self.setup_page.tabs.addTab(self.occupancy_tab, "Occupancy")
        self.person_types_tab.changed.connect(self.occupancy_tab.refresh_person_types)
        self.setup_page.data_changed.connect(self.occupancy_tab.refresh_elements)
        self.setup_page.data_changed.connect(self.refresh_dashboard)
        self.stack.addWidget(self.setup_page)
        content_layout.addWidget(self.stack, 1)
        root_layout.addWidget(content, 1)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        self._apply_style()

    def _dashboard_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(18)

        heading = QLabel("Dashboard")
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)

        intro = QLabel("Build 005 adds operator-defined person types and per-element occupancy limits, ready for person-aware pricing in Build 006.")
        intro.setWordWrap(True)
        intro.setObjectName("bodyText")
        layout.addWidget(intro)

        counts = self.database.counts()
        cards = QHBoxLayout()
        cards.setSpacing(14)
        self.metric_cards = {
            "enquiries": MetricCard("Enquiries", str(counts["enquiries"])),
            "bookings": MetricCard("Bookings", str(counts["bookings"])),
            "elements": MetricCard("Active elements", str(counts["elements"])),
            "transactions": MetricCard("Transactions", str(counts["transactions"])),
        }
        for card in self.metric_cards.values():
            cards.addWidget(card)
        layout.addLayout(cards)

        panel = QFrame()
        panel.setObjectName("panel")
        panel_layout = QVBoxLayout(panel)
        panel_title = QLabel("Build 005 persons and occupancy")
        panel_title.setObjectName("sectionTitle")
        panel_layout.addWidget(panel_title)
        for line in [
            "Unlimited operator-defined person types with name and short label",
            "Person types can be edited, activated and inactivated",
            "Each element can have an overall maximum-person capacity",
            "Each element can also limit each individual person type",
            "A per-type limit of 0 means that person type is not allowed",
            "No limit can be used where only the overall element capacity should control occupancy",
            "Existing Build 004 setup, discounts and pricing test data remain intact",
        ]:
            label = QLabel(f"✓  {line}")
            label.setObjectName("bodyText")
            panel_layout.addWidget(label)

        test_row = QHBoxLayout()
        self.pricing_test_button = QPushButton("Open Pricing Test")
        self.pricing_test_button.clicked.connect(self.open_pricing_test)
        test_row.addWidget(self.pricing_test_button)
        test_row.addStretch()
        panel_layout.addLayout(test_row)
        pricing_note = QLabel("Build 004 Pricing Test still uses a single guest count. Build 006 will replace that with person-type quantities and enforce these occupancy limits during pricing.")
        pricing_note.setWordWrap(True)
        pricing_note.setObjectName("bodyText")
        panel_layout.addWidget(pricing_note)
        panel_layout.addStretch()
        layout.addWidget(panel, 1)
        return page

    def open_pricing_test(self) -> None:
        dialog = PricingTestDialog(self.database, self)
        dialog.exec()

    def refresh_dashboard(self) -> None:
        counts = self.database.counts()
        for key, card in self.metric_cards.items():
            card.set_value(counts[key])

    def _placeholder_page(self, title: str, description: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        panel = QFrame()
        panel.setObjectName("panel")
        panel_layout = QVBoxLayout(panel)
        text = QLabel(description)
        text.setWordWrap(True)
        text.setObjectName("bodyText")
        panel_layout.addWidget(text)
        panel_layout.addStretch()
        layout.addWidget(panel, 1)
        return page

    def _apply_style(self) -> None:
        self.setFont(QFont("Segoe UI", 10))
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f6f7f9; color: #1f2937; }
            QListWidget#navigation { background: #172033; color: #dbe4f0; border: none; padding: 18px 10px; }
            QListWidget#navigation::item { padding: 13px 14px; margin: 2px 0; border-radius: 7px; }
            QListWidget#navigation::item:selected { background: #2d3d59; color: white; }
            QLabel#appTitle { font-size: 22px; font-weight: 700; }
            QLabel#subtitle { color: #6b7280; }
            QLabel#buildBadge { background: #e8edf5; padding: 7px 11px; border-radius: 8px; font-weight: 600; }
            QLabel#pageTitle { font-size: 26px; font-weight: 700; margin-bottom: 4px; }
            QLabel#sectionTitle { font-size: 16px; font-weight: 700; }
            QLabel#bodyText { color: #4b5563; font-size: 11pt; }
            QFrame#panel, QFrame#metricCard { background: white; border: 1px solid #e5e7eb; border-radius: 12px; }
            QFrame#panel { padding: 14px; }
            QFrame#metricCard { min-height: 105px; padding: 10px; }
            QLabel#metricTitle { color: #6b7280; }
            QLabel#metricValue { font-size: 26px; font-weight: 700; }
            QPushButton { background: #172033; color: white; border: none; border-radius: 7px; padding: 9px 14px; }
            QPushButton:hover { background: #24314a; }
            QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTableWidget, QTabWidget::pane {
                background: white; border: 1px solid #d1d5db; border-radius: 6px; padding: 5px;
            }
            QHeaderView::section { background: #eef2f7; padding: 7px; border: none; border-bottom: 1px solid #d1d5db; font-weight: 600; }
            QTableWidget { gridline-color: #e5e7eb; }
            QTabBar::tab { padding: 9px 15px; margin-right: 3px; }
            QTabBar::tab:selected { background: white; font-weight: 600; }
            """
        )
