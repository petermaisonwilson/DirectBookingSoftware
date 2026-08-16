from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .annual_config import (
    get_annual_element_rate,
    get_annual_person_rate,
    get_season_for_date,
    list_years,
    validate_annual_occupancy,
)
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
PERSON_PRICING_TYPES = {"Per person", "Per person per night"}


def _as_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Date must be a valid YYYY-MM-DD date") from exc


def _dates_inclusive(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _night_dates(arrival: date, departure: date) -> list[date]:
    return [arrival + timedelta(days=i) for i in range((departure - arrival).days)]


def calculate_price(
    database: Database,
    element_id: int,
    arrival_date: date | str,
    departure_date: date | str,
    guests: int | None = None,
    person_counts: dict[int, int] | None = None,
) -> dict[str, object]:
    """Calculate one complete element price.

    Build 008 uses annual configuration whenever pricing years exist. Required annual
    cells are never silently guessed: a missing pricing year, seasonal rate, person
    rate/supplement or occupancy value blocks the calculation. Earlier Build 007 data
    remains available as a legacy fallback only if no annual pricing years exist.
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
    else:
        legacy_guests = 1 if guests is None else int(guests)
        if legacy_guests < 1:
            raise ValueError("Guests must be at least 1")
        total_persons = legacy_guests

    nights = (departure - arrival).days
    days = nights + 1
    base_rate = float(element["base_price"])
    configured_years = set(list_years(database))
    annual_mode = bool(configured_years) and person_counts is not None

    if annual_mode:
        occupancy_years = {d.year for d in _dates_inclusive(arrival, departure)}
        for year in sorted(occupancy_years):
            if year not in configured_years:
                raise ValueError(f"Pricing year {year} has not been configured")
            occupancy_errors = validate_annual_occupancy(database, year, int(element_id), normalised_counts)
            if occupancy_errors:
                raise ValueError("Occupancy setup/limit problem: " + "; ".join(occupancy_errors))
    elif person_counts is not None:
        occupancy_errors = database.validate_occupancy(int(element_id), normalised_counts)
        if occupancy_errors:
            raise ValueError("Occupancy limit exceeded: " + "; ".join(occupancy_errors))

    person_breakdown: list[dict[str, object]] = []
    calculation_parts: list[str] = []
    element_base_amount = 0.0
    person_amount = 0.0

    if annual_mode:
        if pricing_type == "Per night":
            unit_dates = _night_dates(arrival, departure)
            unit_word = "night"
        elif pricing_type == "Per day":
            unit_dates = _dates_inclusive(arrival, departure)
            unit_word = "day"
        elif pricing_type in {"Per stay", "Per package"}:
            unit_dates = [arrival]
            unit_word = "stay" if pricing_type == "Per stay" else "package"
        else:
            unit_dates = []
            unit_word = ""

        if pricing_type not in PERSON_PRICING_TYPES:
            grouped_base: dict[tuple[int, str, float], int] = defaultdict(int)
            for unit_date in unit_dates:
                if unit_date.year not in configured_years:
                    raise ValueError(f"Pricing year {unit_date.year} has not been configured")
                season = get_season_for_date(database, unit_date)
                if season is None:
                    raise ValueError(f"No season covers {unit_date.strftime('%d/%m/%Y')}")
                rate = get_annual_element_rate(database, unit_date.year, int(element_id), int(season["id"]))
                if rate is None:
                    raise ValueError(
                        f"Seasonal price missing for {element['name']} / {season['name']} / {unit_date.year}"
                    )
                element_base_amount += rate
                grouped_base[(unit_date.year, str(season["name"]), float(rate))] += 1
            for (year, season_name, rate), count in grouped_base.items():
                calculation_parts.append(
                    f"Element {year} {season_name}: {count} {unit_word}{'s' if count != 1 else ''} × €{rate:.2f}"
                )

        if pricing_type in PERSON_PRICING_TYPES:
            person_unit_dates = [arrival] if pricing_type == "Per person" else _night_dates(arrival, departure)
            grouped_people: dict[tuple[int, int, float], int] = defaultdict(int)
            for person_type_id, count in normalised_counts.items():
                if count <= 0:
                    continue
                for unit_date in person_unit_dates:
                    if unit_date.year not in configured_years:
                        raise ValueError(f"Pricing year {unit_date.year} has not been configured")
                    rate = get_annual_person_rate(database, unit_date.year, int(element_id), person_type_id)
                    if rate is None:
                        raise ValueError(
                            f"Person price missing for {element['name']} / {active_types[person_type_id]['name']} / {unit_date.year}"
                        )
                    person_amount += rate * count
                    grouped_people[(person_type_id, unit_date.year, float(rate))] += 1
            for (person_type_id, year, rate), unit_count in grouped_people.items():
                person_type = active_types[person_type_id]
                count = normalised_counts[person_type_id]
                amount = rate * count * unit_count
                person_breakdown.append({
                    "person_type_id": person_type_id,
                    "name": str(person_type["name"]),
                    "short_label": str(person_type["short_label"]),
                    "count": count,
                    "rate": round(rate, 2),
                    "multiplier": unit_count,
                    "kind": "person rate",
                    "amount": round(amount, 2),
                })
                if pricing_type == "Per person per night":
                    calculation_parts.append(
                        f"{person_type['name']} {year}: {count} × {unit_count} nights × €{rate:.2f}"
                    )
                else:
                    calculation_parts.append(f"{person_type['name']} {year}: {count} × €{rate:.2f}")
        else:
            grouped_supplements: dict[tuple[int, int, float], int] = defaultdict(int)
            for person_type_id, count in normalised_counts.items():
                if count <= 0:
                    continue
                for unit_date in unit_dates:
                    rate = get_annual_person_rate(database, unit_date.year, int(element_id), person_type_id)
                    if rate is None:
                        raise ValueError(
                            f"Person supplement missing for {element['name']} / {active_types[person_type_id]['name']} / {unit_date.year}"
                        )
                    person_amount += rate * count
                    grouped_supplements[(person_type_id, unit_date.year, float(rate))] += 1
            for (person_type_id, year, rate), unit_count in grouped_supplements.items():
                person_type = active_types[person_type_id]
                count = normalised_counts[person_type_id]
                amount = rate * count * unit_count
                person_breakdown.append({
                    "person_type_id": person_type_id,
                    "name": str(person_type["name"]),
                    "short_label": str(person_type["short_label"]),
                    "count": count,
                    "rate": round(rate, 2),
                    "multiplier": unit_count,
                    "kind": "supplement",
                    "amount": round(amount, 2),
                })
                if rate != 0:
                    calculation_parts.append(
                        f"{person_type['name']} supplement {year}: {count} × {unit_count} {unit_word}{'s' if unit_count != 1 else ''} × €{rate:.2f}"
                    )

    else:
        rates = get_element_person_rates(database, int(element_id)) if person_counts is not None else {}
        if pricing_type == "Per night":
            element_base_amount = base_rate * nights
            calculation_parts.append(f"Element: {nights} nights × €{base_rate:.2f}")
        elif pricing_type == "Per day":
            element_base_amount = base_rate * days
            calculation_parts.append(f"Element: {days} days × €{base_rate:.2f}")
        elif pricing_type == "Per stay":
            element_base_amount = base_rate
            calculation_parts.append(f"Element: 1 stay × €{base_rate:.2f}")
        elif pricing_type == "Per package":
            element_base_amount = base_rate
            calculation_parts.append(f"Element: 1 package × €{base_rate:.2f}")

        if pricing_type in PERSON_PRICING_TYPES and person_counts is not None:
            for person_type_id, count in normalised_counts.items():
                if count <= 0:
                    continue
                person_type = active_types[person_type_id]
                rate = float(rates.get(person_type_id, base_rate))
                multiplier = nights if pricing_type == "Per person per night" else 1
                line_amount = rate * count * multiplier
                person_amount += line_amount
                calculation_parts.append(
                    f"{person_type['name']}: {count} × {multiplier} × €{rate:.2f}"
                )
        elif pricing_type == "Per person":
            person_amount = base_rate * total_persons
            calculation_parts.append(f"{total_persons} guests × €{base_rate:.2f}")
        elif pricing_type == "Per person per night":
            person_amount = base_rate * total_persons * nights
            calculation_parts.append(f"{total_persons} guests × {nights} nights × €{base_rate:.2f}")
        elif person_counts is not None:
            multiplier = nights if pricing_type == "Per night" else days if pricing_type == "Per day" else 1
            for person_type_id, count in normalised_counts.items():
                if count <= 0 or person_type_id not in rates:
                    continue
                rate = float(rates[person_type_id])
                person_amount += rate * count * multiplier
                if rate != 0:
                    calculation_parts.append(
                        f"{active_types[person_type_id]['name']} supplement: {count} × {multiplier} × €{rate:.2f}"
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
        "calculation": " + ".join(calculation_parts) if calculation_parts else "No chargeable units",
        "base_amount": float(discount["base_amount"]),
        "discount_amount": float(discount["discount_amount"]),
        "discount_rule_id": discount["rule_id"],
        "discount_rule_name": str(discount["rule_name"]),
        "final_amount": float(discount["final_amount"]),
        "annual_mode": annual_mode,
    }
