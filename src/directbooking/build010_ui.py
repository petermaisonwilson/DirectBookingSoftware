from __future__ import annotations

from PySide6.QtWidgets import QLabel

from .main_window import MainWindow
from .pricing_test_dialog import PricingTestDialog


def apply_build010_labels() -> None:
    if getattr(MainWindow, "_build010_labels_applied", False):
        return

    original_main_init = MainWindow.__init__

    def main_init(self, *args, **kwargs):
        original_main_init(self, *args, **kwargs)
        self.setWindowTitle("Direct Booking Software - Build 010")
        for label in self.findChildren(QLabel):
            text = label.text()
            if "Build 009" in text:
                label.setText(text.replace("Build 009", "Build 010"))
        if hasattr(self, "setup_page") and hasattr(self.setup_page, "addon_rules_tab"):
            self.setup_page.addon_rules_tab.refresh_years()

    MainWindow.__init__ = main_init

    original_pricing_init = PricingTestDialog.__init__

    def pricing_init(self, *args, **kwargs):
        original_pricing_init(self, *args, **kwargs)
        self.setWindowTitle("Pricing Test - Build 010")
        for label in self.findChildren(QLabel):
            text = label.text()
            if "Build 009" in text:
                label.setText(text.replace("Build 009", "Build 010"))

    PricingTestDialog.__init__ = pricing_init
    MainWindow._build010_labels_applied = True
