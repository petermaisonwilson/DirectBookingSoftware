from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QMessageBox, QTableWidgetItem

from . import annual_config


MISSING_COLOUR = QColor("#f8d7da")
ZERO_COLOUR = QColor("#fff3cd")
NORMAL_COLOUR = QColor("#ffffff")


def _is_zero(text: str) -> bool:
    value = text.strip().lower()
    if value in {"no limit", "nl", "-", "n/a"}:
        return False
    try:
        return float(value.replace(",", ".")) == 0.0
    except (TypeError, ValueError):
        return False


def _paint_item(item: QTableWidgetItem | None, required: bool) -> None:
    if item is None:
        return
    text = item.text().strip()
    if required and not text:
        item.setBackground(MISSING_COLOUR)
        item.setToolTip("Required: enter a value or 0 before this annual grid can be saved.")
    elif required and _is_zero(text):
        item.setBackground(ZERO_COLOUR)
        item.setToolTip("0 is valid and deliberately highlighted for review.")
    else:
        item.setBackground(NORMAL_COLOUR)
        item.setToolTip("")


def missing_counts_from_tables(tab) -> dict[str, int]:
    counts = {"rates": 0, "people": 0, "occupancy": 0}

    for row in range(tab.rates_table.rowCount()):
        pricing_item = tab.rates_table.item(row, 1)
        pricing_type = pricing_item.text().strip() if pricing_item else ""
        required = pricing_type not in annual_config.PERSON_PRICING_TYPES
        for column in range(2, tab.rates_table.columnCount()):
            item = tab.rates_table.item(row, column)
            if required and (item is None or not item.text().strip()):
                counts["rates"] += 1

    for row in range(tab.people_table.rowCount()):
        for column in range(2, tab.people_table.columnCount()):
            item = tab.people_table.item(row, column)
            if item is None or not item.text().strip():
                counts["people"] += 1

    for row in range(tab.occupancy_table.rowCount()):
        for column in range(1, tab.occupancy_table.columnCount()):
            item = tab.occupancy_table.item(row, column)
            if item is None or not item.text().strip():
                counts["occupancy"] += 1

    return counts


def apply_cell_highlights(tab) -> None:
    for row in range(tab.rates_table.rowCount()):
        pricing_item = tab.rates_table.item(row, 1)
        pricing_type = pricing_item.text().strip() if pricing_item else ""
        required = pricing_type not in annual_config.PERSON_PRICING_TYPES
        for column in range(2, tab.rates_table.columnCount()):
            _paint_item(tab.rates_table.item(row, column), required)

    for row in range(tab.people_table.rowCount()):
        for column in range(2, tab.people_table.columnCount()):
            _paint_item(tab.people_table.item(row, column), True)

    for row in range(tab.occupancy_table.rowCount()):
        for column in range(1, tab.occupancy_table.columnCount()):
            _paint_item(tab.occupancy_table.item(row, column), True)


def _show_unsaved_missing_status(tab, counts: dict[str, int]) -> None:
    year = tab.current_year()
    tab.rates_status.setText(
        "Seasonal rates complete."
        if counts["rates"] == 0
        else f"Missing seasonal rate cells: {counts['rates']}. Every required cell must contain a value or 0."
    )
    tab.people_status.setText(
        "Person rates / supplements complete."
        if counts["people"] == 0
        else f"Missing person pricing cells: {counts['people']}. Every required cell must contain a value or 0.00."
    )
    tab.occupancy_status.setText(
        "Occupancy grid complete."
        if counts["occupancy"] == 0
        else f"Missing occupancy cells: {counts['occupancy']}. Enter a number, 0, or 'No limit' as appropriate."
    )
    tab.overall_status.setText(
        f"{year} setup incomplete — seasonal rates: {counts['rates']}, "
        f"person pricing: {counts['people']}, occupancy: {counts['occupancy']}. "
        "Blank required cells are highlighted and must be completed before saving."
    )


def apply_annual_grid_safety() -> None:
    cls = annual_config.AnnualConfigurationTab
    if getattr(cls, "_build009_safety_applied", False):
        return

    original_init = cls.__init__
    original_refresh_all = cls.refresh_all
    original_save_all = cls.save_all
    original_grid_page = cls._grid_page

    def grid_page_with_legend(self):
        page, status, table = original_grid_page(self)
        layout = page.layout()
        legend = QLabel(
            "Cell guide:  blank = REQUIRED / not set   |   0 = valid zero, highlighted for review   |   value = configured"
        )
        legend.setWordWrap(True)
        legend.setObjectName("bodyText")
        layout.insertWidget(1, legend)
        return page, status, table

    def refresh_with_highlights(self):
        original_refresh_all(self)
        apply_cell_highlights(self)

    def init_with_safety(self, database):
        original_init(self, database)
        for table in (self.rates_table, self.people_table, self.occupancy_table):
            table.itemChanged.connect(lambda _item, tab=self: apply_cell_highlights(tab))
        apply_cell_highlights(self)

    def save_with_blank_guard(self):
        counts = missing_counts_from_tables(self)
        total = sum(counts.values())
        if total:
            apply_cell_highlights(self)
            _show_unsaved_missing_status(self, counts)
            if counts["rates"]:
                self.tabs.setCurrentIndex(0)
            elif counts["people"]:
                self.tabs.setCurrentIndex(1)
            else:
                self.tabs.setCurrentIndex(2)
            QMessageBox.warning(
                self,
                "Annual grids incomplete",
                f"Cannot save this pricing year yet. There are {total} required blank cell(s).\n\n"
                "Every highlighted blank must contain either a figure, 0, or 'No limit' where appropriate.",
            )
            return
        original_save_all(self)

    cls._grid_page = grid_page_with_legend
    cls.refresh_all = refresh_with_highlights
    cls.__init__ = init_with_safety
    cls.save_all = save_with_blank_guard
    cls._build009_safety_applied = True
