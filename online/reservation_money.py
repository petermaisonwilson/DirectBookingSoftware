"""Numeric validity shared by both database engines; no pricing policy lives here."""
import math


def finite_amount(value, label='amount'):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f'Enter a valid {label}.') from exc
    if not math.isfinite(number):
        raise ValueError(f'Enter a finite {label}.')
    return number
