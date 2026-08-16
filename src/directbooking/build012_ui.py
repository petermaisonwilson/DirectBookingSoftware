from __future__ import annotations

from PySide6.QtWidgets import QLabel

from .main_window import MainWindow
from .pricing_test_dialog import PricingTestDialog


def apply_build012_labels() -> None:
    if getattr(MainWindow, "_build012_labels_applied", False):
        return

    original_main_init = MainWindow.__init__

    def main_init(self, *args, **kwargs):
        original_main_init(self, *args, **kwargs)
        self.setWindowTitle("Direct Booking Software - Build 012")
        for label in self.findChildren(QLabel):
            text = label.text()
            if "Build 011" in text:
                label.setText(text.replace("Build 011", "Build 012"))

    MainWindow.__init__ = main_init

    original_pricing_init = PricingTestDialog.__init__

    def pricing_init(self, *args, **kwargs):
        original_pricing_init(self, *args, **kwargs)
        self.setWindowTitle("Pricing Test - Build 012")
        for label in self.findChildren(QLabel):
            text = label.text()
            if "Build 011" in text:
                label.setText(text.replace("Build 011", "Build 012"))

    PricingTestDialog.__init__ = pricing_init
    MainWindow._build012_labels_applied = True
