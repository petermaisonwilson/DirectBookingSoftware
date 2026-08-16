from __future__ import annotations

from PySide6.QtWidgets import QLabel

from .main_window import MainWindow
from .pricing_test_dialog import PricingTestDialog
from .setup_page import ElementDialog


def apply_build011_labels() -> None:
    if getattr(MainWindow, "_build011_labels_applied", False):
        return

    original_element_init = ElementDialog.__init__

    def element_init(self, *args, **kwargs):
        original_element_init(self, *args, **kwargs)
        for label in self.findChildren(QLabel):
            if label.text() == "Group":
                label.setText("Element Type")

    ElementDialog.__init__ = element_init

    original_main_init = MainWindow.__init__

    def main_init(self, *args, **kwargs):
        original_main_init(self, *args, **kwargs)
        self.setWindowTitle("Direct Booking Software - Build 011")
        for label in self.findChildren(QLabel):
            text = label.text()
            if "Build 010" in text:
                label.setText(text.replace("Build 010", "Build 011"))
        if hasattr(self, "setup_page"):
            if hasattr(self.setup_page, "elements_table") and self.setup_page.elements_table.horizontalHeaderItem(1):
                self.setup_page.elements_table.horizontalHeaderItem(1).setText("Element Type")
            if hasattr(self.setup_page, "addon_rules_tab"):
                self.setup_page.addon_rules_tab.refresh_years()

    MainWindow.__init__ = main_init

    original_pricing_init = PricingTestDialog.__init__

    def pricing_init(self, *args, **kwargs):
        original_pricing_init(self, *args, **kwargs)
        self.setWindowTitle("Pricing Test - Build 011")
        for label in self.findChildren(QLabel):
            text = label.text()
            if "Build 010" in text:
                label.setText(text.replace("Build 010", "Build 011"))

    PricingTestDialog.__init__ = pricing_init
    MainWindow._build011_labels_applied = True
