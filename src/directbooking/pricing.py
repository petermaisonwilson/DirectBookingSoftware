from __future__ import annotations

from datetime import date

from .database import Database


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
    guests: int = 1,
) -> dict[str, object]:
    """Calculate one element line using its configured pricing type and duration discounts.

    Build 004 deliberately calculates a price only. It does not create an enquiry,
    offer or booking. Nights are departure minus arrival. Per-day pricing includes
    both the arrival and departure date, so chargeable days are nights + 1.
    """

    arrival = _as_date(arrival_date)
    departure = _as_date(departure_date)
    if departure < arrival:
        raise ValueError("Departure date cannot be before arrival date")
    if int(guests) < 1:
        raise ValueError("Guests must be at least 1")

    element = next(
        (row for row in database.list_elements(include_inactive=True) if int(row["id"]) == int(element_id)),
        None,
    )
    if element is None:
        raise ValueError("Element does not exist")

    pricing_type = str(element["pricing_type"])
    if pricing_type not in PRICING_TYPES:
        raise ValueError(f"Unsupported pricing type: {pricing_type}")

    nights = (departure - arrival).days
    days = nights + 1
    guests = int(guests)
    rate = float(element["base_price"])

    if pricing_type == "Per night":
        base_amount = rate * nights
        calculation = f"{nights} night{'s' if nights != 1 else ''} × €{rate:.2f}"
    elif pricing_type == "Per day":
        base_amount = rate * days
        calculation = f"{days} day{'s' if days != 1 else ''} × €{rate:.2f}"
    elif pricing_type == "Per stay":
        base_amount = rate
        calculation = f"1 stay × €{rate:.2f}"
    elif pricing_type == "Per person":
        base_amount = rate * guests
        calculation = f"{guests} guest{'s' if guests != 1 else ''} × €{rate:.2f}"
    elif pricing_type == "Per person per night":
        base_amount = rate * guests * nights
        calculation = (
            f"{guests} guest{'s' if guests != 1 else ''} × "
            f"{nights} night{'s' if nights != 1 else ''} × €{rate:.2f}"
        )
    else:  # Per package
        base_amount = rate
        calculation = f"1 package × €{rate:.2f}"

    base_amount = round(base_amount, 2)
    discount = database.calculate_duration_discount(int(element_id), nights, base_amount)

    return {
        "element_id": int(element["id"]),
        "element_name": str(element["name"]),
        "group_name": str(element["group_name"]),
        "pricing_type": pricing_type,
        "rate": round(rate, 2),
        "arrival_date": arrival.isoformat(),
        "departure_date": departure.isoformat(),
        "nights": nights,
        "days": days,
        "guests": guests,
        "calculation": calculation,
        "base_amount": float(discount["base_amount"]),
        "discount_amount": float(discount["discount_amount"]),
        "discount_rule_id": discount["rule_id"],
        "discount_rule_name": str(discount["rule_name"]),
        "final_amount": float(discount["final_amount"]),
    }
