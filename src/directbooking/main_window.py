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


NAV_ITEMS = ["Dashboard", "Enquiries", "Availability", "Bookings", "Finance", "Setup"]


class MetricCard(QFrame):
    def __init__(self, title: str, value: str):
        super().__init__()
        self.setObjectName("metricCard")
        layout = QVBoxLayout(self)
        title_label = QLabel(title)
        title_label.setObjectName("metricTitle")
        value_label = QLabel(value)
        value_label.setObjectName("metricValue")
        layout.addWidget(title_label)
        layout.addWidget(value_label)


class MainWindow(QMainWindow):
    def __init__(self, database: Database):
        super().__init__()
        self.database = database
        self.setWindowTitle("Direct Booking Software - Build 001")
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
        build = QLabel("Build 001")
        build.setObjectName("buildBadge")
        header.addWidget(build)
        content_layout.addLayout(header)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._dashboard_page())
        self.stack.addWidget(self._placeholder_page("Enquiries", "Enquiry intake and offer preparation will be migrated here in the next application builds."))
        self.stack.addWidget(self._placeholder_page("Availability", "The large element-by-date availability board will live here."))
        self.stack.addWidget(self._placeholder_page("Bookings", "Confirmed bookings, amendments, payments and booking audit logs will live here."))
        self.stack.addWidget(self._placeholder_page("Finance", "The booking ledger and global transaction view will live here."))
        self.stack.addWidget(self._setup_page())
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

        intro = QLabel("Build 001 confirms the Windows desktop shell, persistent SQLite database and core application structure.")
        intro.setWordWrap(True)
        intro.setObjectName("bodyText")
        layout.addWidget(intro)

        counts = self.database.counts()
        cards = QHBoxLayout()
        cards.setSpacing(14)
        cards.addWidget(MetricCard("Enquiries", str(counts["enquiries"])))
        cards.addWidget(MetricCard("Bookings", str(counts["bookings"])))
        cards.addWidget(MetricCard("Elements", str(counts["elements"])))
        cards.addWidget(MetricCard("Transactions", str(counts["transactions"])))
        layout.addLayout(cards)

        panel = QFrame()
        panel.setObjectName("panel")
        panel_layout = QVBoxLayout(panel)
        panel_title = QLabel("Build 001 foundation")
        panel_title.setObjectName("sectionTitle")
        panel_layout.addWidget(panel_title)
        for line in [
            "Windows-native PySide6 interface",
            "Persistent SQLite development database",
            "Database tables prepared for staff users and operator audit trails",
            "Data layer separated so a future central API/PostgreSQL service can replace local storage",
            "GitHub Actions Windows packaging pipeline",
        ]:
            label = QLabel(f"✓  {line}")
            label.setObjectName("bodyText")
            panel_layout.addWidget(label)
        panel_layout.addStretch()
        layout.addWidget(panel, 1)
        return page

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

    def _setup_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        heading = QLabel("Setup")
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)

        panel = QFrame()
        panel.setObjectName("panel")
        panel_layout = QVBoxLayout(panel)
        title = QLabel("Development database")
        title.setObjectName("sectionTitle")
        panel_layout.addWidget(title)
        path_label = QLabel(str(self.database.path))
        path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_label.setWordWrap(True)
        path_label.setObjectName("bodyText")
        panel_layout.addWidget(path_label)
        note = QLabel("The production design will keep operator data behind a secure service layer so this local database can later be replaced by a central hosted database without rebuilding the desktop interface.")
        note.setWordWrap(True)
        note.setObjectName("bodyText")
        panel_layout.addWidget(note)
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
            """
        )
