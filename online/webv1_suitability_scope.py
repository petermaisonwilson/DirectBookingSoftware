from __future__ import annotations

from .setup015_calculator import _addon_rule
from .setup015_core import one, rows


def _relevant_requirement_ids(database, cid: int, year: int, element_type: str) -> set[int]:
    """Requirements only matter to an Element Type when that Type allows them,
    or one of its Elements explicitly allows them.

    This stops Camping-only requirements (Motorhome, Caravan, EHU, Pets, etc.)
    making unrelated Element Types such as Fishing unsuitable.
    """
    relevant = {
        int(r['addon_id'])
        for r in rows(
            database,
            'SELECT addon_id FROM setup_type_addons WHERE company_id=? AND year=? AND element_type=? AND allowed=1',
            (cid, year, element_type),
        )
    }
    relevant.update(
        int(r['addon_id'])
        for r in rows(
            database,
            '''SELECT DISTINCT ea.addon_id
               FROM setup_element_addons ea
               JOIN setup_elements e ON e.id=ea.element_id AND e.company_id=ea.company_id
               WHERE ea.company_id=? AND ea.year=? AND e.element_type=? AND e.active=1 AND ea.state='Y' ''',
            (cid, year, element_type),
        )
    )
    return relevant


def scoped_element_reasons(database, cid: int, year: int, element, people: dict, addons: dict) -> list[str]:
    reasons: list[str] = []
    element_type = str(element['element_type'])

    # Whole-party occupancy belongs to accommodation-style Elements. A per-day
    # activity/resource (for example a fishing peg) is selected for the people
    # actually using that resource, so the entire accommodation party must not
    # make every resource row unsuitable.
    pricing_method = str(element['pricing_method'] or '')
    apply_whole_party = pricing_method != 'Per day'

    if apply_whole_party:
        total = sum(int(v.get('quantity', 0)) for v in people.values())
        occupancy = one(
            database,
            'SELECT max_total FROM setup_occupancy WHERE company_id=? AND year=? AND element_id=?',
            (cid, year, int(element['id'])),
        )
        if occupancy is None:
            reasons.append('occupancy setup incomplete')
        elif total > int(occupancy['max_total']):
            reasons.append(f'maximum occupancy {int(occupancy["max_total"])}')

        for pid, data in people.items():
            qty = int(data.get('quantity', 0))
            if qty <= 0:
                continue
            person = one(database, 'SELECT name FROM setup_person_types WHERE company_id=? AND id=?', (cid, pid))
            limit = one(
                database,
                'SELECT max_count FROM setup_person_limits WHERE company_id=? AND year=? AND element_id=? AND person_type_id=?',
                (cid, year, int(element['id']), pid),
            )
            name = str(person['name']) if person else 'Person type'
            if limit is None:
                reasons.append(f'{name} not configured')
            elif qty > int(limit['max_count']):
                reasons.append(f'{name} not allowed' if int(limit['max_count']) == 0 else f'{name} max {int(limit["max_count"])}')

    relevant = _relevant_requirement_ids(database, cid, year, element_type)
    for aid, raw_qty in addons.items():
        aid = int(aid)
        qty = int(raw_qty)
        if qty <= 0 or aid not in relevant:
            continue
        addon = one(database, 'SELECT name FROM setup_addons WHERE company_id=? AND id=?', (cid, aid))
        name = str(addon['name']) if addon else 'Requirement'
        rule = _addon_rule(database, cid, year, element, aid)
        if not rule['allowed']:
            reasons.append(f'no {name}')
        elif rule['max'] is not None and qty > int(rule['max']):
            reasons.append(f'{name} max {int(rule["max"])}')

    return reasons


def install_suitability_scope() -> None:
    """Replace the original global suitability checker everywhere it is used."""
    from . import webv1_booking_requirements as requirements
    from . import webv1_booking_requirements_refinements as refinements

    requirements._element_reasons = scoped_element_reasons
    refinements._element_reasons = scoped_element_reasons
