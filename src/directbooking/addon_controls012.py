from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from .addon_inheritance import (
    delete_element_override,
    get_group_addon_rule,
    list_element_types,
    resolve_addon_rule,
    save_group_addon_rule,
    validate_group_addon_year,
)
from .addon_model import get_addon_rule, list_addons, save_addon_rule
from .addon_rules011 import (
    INHERIT_BG,
    NO_BG,
    NORMAL_BG,
    ZERO_BG,
    MISSING_BG,
    ElementAddonRules011Tab,
)


def _centre_widget(child: QWidget) -> QWidget:
    wrapper = QWidget()
    layout = QHBoxLayout(wrapper)
    layout.setContentsMargins(2, 0, 2, 0)
    layout.setSpacing(4)
    layout.addStretch()
    layout.addWidget(child)
    layout.addStretch()
    return wrapper


def _availability_widget(checked: bool) -> QWidget:
    check = QCheckBox()
    check.setChecked(bool(checked))
    check.setToolTip("Ticked = Y (available). Unticked = N (not available).")
    wrapper = _centre_widget(check)
    wrapper._availability_check = check  # type: ignore[attr-defined]
    return wrapper


def _override_widget(state: str) -> QWidget:
    wrapper = QWidget()
    layout = QHBoxLayout(wrapper)
    layout.setContentsMargins(2, 0, 2, 0)
    layout.setSpacing(5)
    layout.addStretch()
    group = QButtonGroup(wrapper)
    buttons: dict[str, QRadioButton] = {}
    for key in ("I", "Y", "N"):
        button = QRadioButton(key)
        button.setToolTip({
            "I": "Inherit the Element Type rule",
            "Y": "Yes — allow this Add-on for this Element",
            "N": "No — do not allow this Add-on for this Element",
        }[key])
        group.addButton(button)
        layout.addWidget(button)
        buttons[key] = button
    layout.addStretch()
    buttons.get(state, buttons["I"]).setChecked(True)
    wrapper._override_group = group  # type: ignore[attr-defined]
    wrapper._override_buttons = buttons  # type: ignore[attr-defined]
    return wrapper


def _type_yes(tab: ElementAddonRules011Tab, row: int) -> bool:
    wrapper = tab.type_table.cellWidget(row, 3)
    if wrapper is None:
        return False
    check = getattr(wrapper, "_availability_check", None)
    return bool(check and check.isChecked())


def _override_state(tab: ElementAddonRules011Tab, row: int) -> str:
    wrapper = tab.override_table.cellWidget(row, 4)
    buttons = getattr(wrapper, "_override_buttons", {}) if wrapper is not None else {}
    for key in ("I", "Y", "N"):
        button = buttons.get(key)
        if button is not None and button.isChecked():
            return key
    return "I"


def _set_editable(item: QTableWidgetItem, enabled: bool) -> None:
    flags = item.flags()
    if enabled:
        item.setFlags(flags | Qt.ItemFlag.ItemIsEditable)
    else:
        item.setFlags(flags & ~Qt.ItemFlag.ItemIsEditable)


def apply_build012_controls() -> None:
    if getattr(ElementAddonRules011Tab, "_build012_controls_applied", False):
        return

    original_init = ElementAddonRules011Tab.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        for label in self.findChildren(QLabel):
            text = label.text()
            if "Blank Available?" in text:
                label.setText(
                    "Availability: ticked = Y (Yes, offer this Add-on). Unticked = N (No, do not offer it). "
                    "When Y is selected, enter Min, Max and Price. A €0.00 price is valid."
                )
            elif "Override = Inherit" in text:
                label.setText(
                    "I = Inherit Element Type rule    Y = Yes, allow this Add-on    N = No, do not allow this Add-on. "
                    "Use Y or N only when the individual Element is an exception."
                )
        self.type_table.setHorizontalHeaderItem(3, QTableWidgetItem("Y / N"))
        self.override_table.setHorizontalHeaderItem(4, QTableWidgetItem("I / Y / N"))

    def load_type_defaults(self, year: int) -> None:
        groups = list_element_types(self.database)
        addons = list_addons(self.database, False)
        table = self.type_table
        table.blockSignals(True)
        table.setRowCount(len(groups) * len(addons))
        row = 0
        for group_name in groups:
            for addon in addons:
                addon_id = int(addon["id"])
                table.setItem(row, 0, self._locked(group_name))
                table.setItem(row, 1, self._locked(str(addon["name"]), addon_id))
                table.setItem(row, 2, self._locked(str(addon["pricing_method"])))
                rule = get_group_addon_rule(self.database, year, group_name, addon_id)
                allowed = bool(rule["allowed"]) if rule is not None else False
                table.setCellWidget(row, 3, _availability_widget(allowed))
                if rule is not None and allowed:
                    values = (
                        "" if rule["min_qty"] is None else str(int(rule["min_qty"])),
                        "" if rule["max_qty"] is None else str(int(rule["max_qty"])),
                        "" if rule["rate"] is None else f"{float(rule['rate']):.2f}",
                    )
                else:
                    values = ("", "", "")
                for col, value in enumerate(values, start=4):
                    table.setItem(row, col, QTableWidgetItem(value))
                check = getattr(table.cellWidget(row, 3), "_availability_check")
                check.stateChanged.connect(lambda _state, tab=self: tab._style_type_rows())
                row += 1
        table.blockSignals(False)
        self._style_type_rows()

    def load_overrides(self, year: int) -> None:
        elements = self.database.list_elements(False)
        addons = list_addons(self.database, False)
        table = self.override_table
        table.blockSignals(True)
        table.setRowCount(len(elements) * len(addons))
        row = 0
        for element in elements:
            element_id = int(element["id"])
            group_name = str(element["group_name"] or "").strip() or "(No type)"
            for addon in addons:
                addon_id = int(addon["id"])
                table.setItem(row, 0, self._locked(str(element["name"]), element_id))
                table.setItem(row, 1, self._locked(group_name))
                table.setItem(row, 2, self._locked(str(addon["name"]), addon_id))
                table.setItem(row, 3, self._locked(str(addon["pricing_method"])))
                override = get_addon_rule(self.database, year, element_id, addon_id)
                if override is None:
                    state, values = "I", ("", "", "")
                elif bool(override["allowed"]):
                    state = "Y"
                    values = (
                        "" if override["min_qty"] is None else str(int(override["min_qty"])),
                        "" if override["max_qty"] is None else str(int(override["max_qty"])),
                        "" if override["rate"] is None else f"{float(override['rate']):.2f}",
                    )
                else:
                    state, values = "N", ("", "", "")
                table.setCellWidget(row, 4, _override_widget(state))
                for col, value in enumerate(values, start=5):
                    table.setItem(row, col, QTableWidgetItem(value))
                effective = resolve_addon_rule(self.database, year, element_id, addon_id)
                if not effective["configured"]:
                    effective_text = "No configured default"
                elif not effective["allowed"]:
                    effective_text = f"No — {effective['source']}"
                else:
                    effective_text = (
                        f"Yes {effective['min_qty']}–{effective['max_qty']} @ €{float(effective['rate']):.2f} — {effective['source']}"
                    )
                table.setItem(row, 8, self._locked(effective_text))
                buttons = getattr(table.cellWidget(row, 4), "_override_buttons")
                for button in buttons.values():
                    button.toggled.connect(lambda _checked, tab=self: tab._style_override_rows())
                row += 1
        table.blockSignals(False)
        self._style_override_rows()

    def style_type_rows(self, *_args) -> None:
        table = self.type_table
        table.blockSignals(True)
        for row in range(table.rowCount()):
            allowed = _type_yes(self, row)
            for col in (4, 5, 6):
                item = table.item(row, col)
                _set_editable(item, allowed)
                if not allowed:
                    item.setBackground(NO_BG)
                else:
                    value = self._text(table, row, col)
                    if not value:
                        item.setBackground(MISSING_BG)
                    elif col == 6:
                        try:
                            item.setBackground(ZERO_BG if float(value.replace(",", ".")) == 0 else NORMAL_BG)
                        except ValueError:
                            item.setBackground(MISSING_BG)
                    else:
                        item.setBackground(NORMAL_BG)
        table.blockSignals(False)

    def style_override_rows(self, *_args) -> None:
        table = self.override_table
        table.blockSignals(True)
        for row in range(table.rowCount()):
            state = _override_state(self, row)
            enabled = state == "Y"
            background = INHERIT_BG if state == "I" else NO_BG if state == "N" else NORMAL_BG
            for col in (5, 6, 7):
                item = table.item(row, col)
                _set_editable(item, enabled)
                if not enabled:
                    item.setBackground(background)
                else:
                    value = self._text(table, row, col)
                    if not value:
                        item.setBackground(MISSING_BG)
                    elif col == 7:
                        try:
                            item.setBackground(ZERO_BG if float(value.replace(",", ".")) == 0 else NORMAL_BG)
                        except ValueError:
                            item.setBackground(MISSING_BG)
                    else:
                        item.setBackground(NORMAL_BG)
        table.blockSignals(False)

    def scan_type_defaults(self) -> list[str]:
        errors: list[str] = []
        for row in range(self.type_table.rowCount()):
            if _type_yes(self, row):
                errors.extend(
                    self._validate_values(
                        self.type_table, row, 4,
                        self._text(self.type_table, row, 0),
                        self._text(self.type_table, row, 1),
                    )
                )
        return errors

    def scan_overrides(self) -> list[str]:
        errors: list[str] = []
        for row in range(self.override_table.rowCount()):
            if _override_state(self, row) == "Y":
                errors.extend(
                    self._validate_values(
                        self.override_table, row, 5,
                        self._text(self.override_table, row, 0),
                        self._text(self.override_table, row, 2),
                    )
                )
        return errors

    def update_status(self, year: int) -> None:
        type_errors = self._scan_type_defaults()
        override_errors = self._scan_overrides()
        if type_errors:
            self.type_status.setText(f"{year} Element Type Add-on setup needs attention: {len(type_errors)} Y row(s) are incomplete.")
        else:
            self.type_status.setText(f"{year} Element Type Add-on defaults are ready to save. Unticked rows explicitly mean N.")
        if override_errors:
            self.override_status.setText(f"{len(override_errors)} Y override row(s) need Min, Max or Price attention.")
        else:
            override_count = self.database.connection.execute(
                "SELECT COUNT(*) FROM annual_element_addons WHERE company_id=? AND year=?",
                (self.database.company_id(), year),
            ).fetchone()[0]
            self.override_status.setText(
                f"{override_count} individual Element override(s) stored. I means inherit the Element Type default."
            )

    def save_type_defaults(self) -> None:
        year = self.current_year()
        if year is None:
            return
        errors = self._scan_type_defaults()
        if errors:
            self._style_type_rows()
            QMessageBox.warning(self, "Cannot save Element Type defaults", errors[0] + (f"\n\n{len(errors)} row(s) need attention." if len(errors) > 1 else ""))
            return
        try:
            for row in range(self.type_table.rowCount()):
                group_name = self._text(self.type_table, row, 0)
                addon_id = int(self.type_table.item(row, 1).data(32))
                if not _type_yes(self, row):
                    save_group_addon_rule(self.database, year, group_name, addon_id, False)
                else:
                    save_group_addon_rule(
                        self.database, year, group_name, addon_id, True,
                        int(self._text(self.type_table, row, 4)),
                        int(self._text(self.type_table, row, 5)),
                        float(self._text(self.type_table, row, 6).replace(",", ".")),
                    )
            self.database.connection.commit()
        except ValueError as exc:
            self.database.connection.rollback()
            QMessageBox.warning(self, "Cannot save Element Type defaults", str(exc))
            return
        self.refresh()
        self.changed.emit()
        QMessageBox.information(self, "Saved", f"Element Type Add-on defaults for {year} saved.")

    def save_overrides(self) -> None:
        year = self.current_year()
        if year is None:
            return
        errors = self._scan_overrides()
        if errors:
            self._style_override_rows()
            QMessageBox.warning(self, "Cannot save Element overrides", errors[0] + (f"\n\n{len(errors)} row(s) need attention." if len(errors) > 1 else ""))
            return
        try:
            for row in range(self.override_table.rowCount()):
                element_id = int(self.override_table.item(row, 0).data(32))
                addon_id = int(self.override_table.item(row, 2).data(32))
                state = _override_state(self, row)
                if state == "I":
                    delete_element_override(self.database, year, element_id, addon_id)
                elif state == "N":
                    save_addon_rule(self.database, year, element_id, addon_id, False)
                else:
                    save_addon_rule(
                        self.database, year, element_id, addon_id, True,
                        int(self._text(self.override_table, row, 5)),
                        int(self._text(self.override_table, row, 6)),
                        float(self._text(self.override_table, row, 7).replace(",", ".")),
                    )
            self.database.connection.commit()
        except ValueError as exc:
            self.database.connection.rollback()
            QMessageBox.warning(self, "Cannot save Element overrides", str(exc))
            return
        self.refresh()
        self.changed.emit()
        QMessageBox.information(self, "Saved", f"Element overrides for {year} saved.")

    ElementAddonRules011Tab.__init__ = patched_init
    ElementAddonRules011Tab._load_type_defaults = load_type_defaults
    ElementAddonRules011Tab._load_overrides = load_overrides
    ElementAddonRules011Tab._style_type_rows = style_type_rows
    ElementAddonRules011Tab._style_override_rows = style_override_rows
    ElementAddonRules011Tab._scan_type_defaults = scan_type_defaults
    ElementAddonRules011Tab._scan_overrides = scan_overrides
    ElementAddonRules011Tab._update_status = update_status
    ElementAddonRules011Tab.save_type_defaults = save_type_defaults
    ElementAddonRules011Tab.save_overrides = save_overrides
    ElementAddonRules011Tab._build012_controls_applied = True
