from __future__ import annotations

from .setup015_core import one


def resolve_element_item_rule(database, company_id: int, year: int, element, item_id: int) -> dict:
    """Resolve one Feature/Extra rule from Type default plus Element override.

    This is the canonical Availability/popup resolver.  Default/inherit rows are
    deliberately treated as inheritance, never as an implicit Yes.
    """
    override = one(
        database,
        'SELECT * FROM setup_element_addons WHERE company_id=? AND year=? AND element_id=? AND addon_id=?',
        (company_id, year, int(element['id']), item_id),
    )
    if override:
        state = str(override['state'] or 'I').upper()
        if state == 'N':
            return {'allowed': False, 'source': 'Element override N', 'min': None, 'max': None, 'rate': None}
        if state == 'Y':
            return {
                'allowed': True,
                'source': 'Element override Y',
                'min': None if override['min_qty'] is None else int(override['min_qty']),
                'max': None if override['max_qty'] is None else int(override['max_qty']),
                'rate': None if override['rate'] is None else float(override['rate']),
            }
        # I/default falls through to the Element Type rule.

    type_rule = one(
        database,
        'SELECT * FROM setup_type_addons WHERE company_id=? AND year=? AND element_type=? AND addon_id=?',
        (company_id, year, str(element['element_type']), item_id),
    )
    if not type_rule or not int(type_rule['allowed'] or 0):
        return {'allowed': False, 'source': 'Element Type default N', 'min': None, 'max': None, 'rate': None}
    return {
        'allowed': True,
        'source': 'Element Type default Y',
        'min': None if type_rule['min_qty'] is None else int(type_rule['min_qty']),
        'max': None if type_rule['max_qty'] is None else int(type_rule['max_qty']),
        'rate': None if type_rule['rate'] is None else float(type_rule['rate']),
    }
