from __future__ import annotations

from datetime import date

from .database import Database
from .person_pricing import get_element_person_rates


PRICING_TYPES = {
    "Per night",
    "Per day",
    "Per stay",
    "Per person",
    "Per person per night",
    "Per package",
}


def _as_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Date must be a valid YYYY-MM-DD date") from exc


def calculate_price(
    database: Database,
    element_id: int,
    arrival_date: date | str,
    departure_date: date | str,
    guests: int | None = None,
    person_counts: dict[int, int] | None = None,
) -> dict[str, object]:
    """Calculate one complete element price.

    Build 007 keeps the element as the single bookable resource. For non-person pricing
    types, the configured Base Price is calculated first and any configured person rates
    are then treated as supplements. For Per person / Per person per night, person rates
    remain the actual rates for each person type. Occupancy is validated before pricing.
    """

    arrival = _as_date(arrival_date)
    departure = _as_date(departure_date)
    if departure < arrival:
        raise ValueError("Departure date cannot be before arrival date")

    element = next(
        (row for row in database.list_elements(include_inactive=True) if int(row["id"]) == int(element_id)),
        None,
    )
    if element is None:
        raise ValueError("Element does not exist")

    pricing_type = str(element["pricing_type"])
    if pricing_type not in PRICING_TYPES:
        raise ValueError(f"Unsupported pricing type: {pricing_type}")

    active_types = {int(row["id"]): row for row in database.list_person_types(False)}
    normalised_counts: dict[int, int] = {}
    if person_counts is not None:
        for person_type_id, count in person_counts.items():
            person_type_id = int(person_type_id)
            count = int(count)
            if count < 0:
                raise ValueError("Person counts cannot be negative")
            if count and person_type_id not in active_types:
                raise ValueError("Pricing can only use active person types")
            normalised_counts[person_type_id] = count
        total_persons = sum(normalised_counts.values())
        if total_persons < 1:
            raise ValueError("At least one person is required")
        occupancy_errors = database.validate_occupancy(int(element_id), normalised_counts)
        if occupancy_errors:
            raise ValueError("Occupancy limit exceeded: " + "; ".join(occupancy_errors))
    else:
        legacy_guests = 1 if guests is None else int(guests)
        if legacy_guests < 1:
            raise ValueError("Guests must be at least 1")
        total_persons = legacy_guests

    nights = (departure - arrival).days
    days = nights + 1
    base_rate = float(element["base_price"])
    rates = get_element_person_rates(database, int(element_id)) if person_counts is not None else {}
    person_breakdown: list[dict[str, object]] = []
    calculation_parts: list[str] = []

    # First calculate the element's own base charge.
    if pricing_type == "Per night":
        element_base_amount = base_rate * nights
        calculation_parts.append(f"Element: {nights} night{'s' if nights != 1 else ''} × €{base_rate:.2f}")
    elif pricing_type == "Per day":
        element_base_amount = base_rate * days
        calculation_parts.append(f"Element: {days} day{'s' if days != 1 else ''} × €{base_rate:.2f}")
    elif pricing_type == "Per stay":
        element_base_amount = base_rate
        calculation_parts.append(f"Element: 1 stay × €{base_rate:.2f}")
    elif pricing_type == "Per package":
        element_base_amount = base_rate
        calculation_parts.append(f"Element: 1 package × €{base_rate:.2f}")
    else:
        element_base_amount = 0.0

    person_amount = 0.0

    if pricing_type in {"Per person", "Per person per night"} and person_counts is not None:
        for person_type_id, count in normalised_counts.items():
            if count <= 0:
                continue
            person_type = active_types[person_type_id]
            rate = float(rates.get(person_type_id, base_rate))
            multiplier = nights if pricing_type == "Per person per night" else 1
            line_amount = rate * count * multiplier
            person_amount += line_amount
            person_breakdown.append(
                {
                    "person_type_id": person_type_id,
                    "name": str(person_type["name"]),
                    "short_label": str(person_type["short_label"]),
                    "count": count,
                    "rate": round(rate, 2),
                    "multiplier": multiplier,
                    "kind": "person rate",
                    "amount": round(line_amount, 2),
                }
            )
            if pricing_type == "Per person per night":
                calculation_parts.append(
                    f"{person_type['name']}: {count} × {nights} nights × €{rate:.2f}"
                )
            else:
                calculation_parts.append(f"{person_type['name']}: {count} × €{rate:.2f}")
    elif pricing_type == "Per person":
        person_amount = base_rate * total_persons
        calculation_parts.append(f"{total_persons} guests × €{base_rate:.2f}")
    elif pricing_type == "Per person per night":
        person_amount = base_rate * total_persons * nights
        calculation_parts.append(f"{total_persons} guests × {nights} nights × €{base_rate:.2f}")
    elif person_counts is not None:
        # Non-person-priced elements may carry optional person supplements.
        if pricing_type == "Per night":
            supplement_multiplier = nights
            unit_label = f"{nights} nights"
        elif pricing_type == "Per day":
            supplement_multiplier = days
            unit_label = f"{days} days"
        else:  # Per stay / Per package
            supplement_multiplier = 1
            unit_label = "1 stay"

        for person_type_id, count in normalised_counts.items():
            if count <= 0 or person_type_id not in rates:
                continue
            person_type = active_types[person_type_id]
            rate = float(rates[person_type_id])
            line_amount = rate * count * supplement_multiplier
            person_amount += line_amount
            person_breakdown.append(
                {
                    "person_type_id": person_type_id,
                    "name": str(person_type["name"]),
                    "short_label": str(person_type["short_label"]),
                    "count": count,
                    "rate": round(rate, 2),
                    "multiplier": supplement_multiplier,
                    "kind": "supplement",
                    "amount": round(line_amount, 2),
                }
            )
            calculation_parts.append(
                f"{person_type['name']} supplement: {count} × {unit_label} × €{rate:.2f}"
                if supplement_multiplier != 1
                else f"{person_type['name']} supplement: {count} × €{rate:.2f}"
            )

    pre_discount_amount = round(element_base_amount + person_amount, 2)
    discount = database.calculate_duration_discount(int(element_id), nights, pre_discount_amount)

    if person_counts is not None:
        people_summary = ", ".join(
            f"{count} {active_types[person_type_id]['short_label']}"
            for person_type_id, count in normalised_counts.items()
            if count > 0
        )
    else:
        people_summary = f"{total_persons} guest{'s' if total_persons != 1 else ''}"

    return {
        "element_id": int(element["id"]),
        "element_name": str(element["name"]),
        "group_name": str(element["group_name"]),
        "pricing_type": pricing_type,
        "rate": round(base_rate, 2),
        "arrival_date": arrival.isoformat(),
        "departure_date": departure.isoformat(),
        "nights": nights,
        "days": days,
        "guests": total_persons,
        "person_counts": normalised_counts,
        "people_summary": people_summary,
        "person_breakdown": person_breakdown,
        "element_base_amount": round(element_base_amount, 2),
        "person_amount": round(person_amount, 2),
        "calculation": " + ".join(calculation_parts),
        "base_amount": float(discount["base_amount"]),
        "discount_amount": float(discount["discount_amount"]),
        "discount_rule_id": discount["rule_id"],
        "discount_rule_name": str(discount["rule_name"]),
        "final_amount": float(discount["final_amount"]),
    }
