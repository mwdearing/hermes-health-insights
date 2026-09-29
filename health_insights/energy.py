"""IOM (2005) Estimated Energy Requirement for adults. Pure functions, no I/O."""
from __future__ import annotations

_PAL = {
    "male": {"sedentary": 1.0, "low_active": 1.11, "active": 1.25, "very_active": 1.48},
    "female": {"sedentary": 1.0, "low_active": 1.12, "active": 1.27, "very_active": 1.45},
}


def pal_value(sex: str, band: str) -> float:
    try:
        return _PAL[sex][band]
    except KeyError:
        raise ValueError(f"unknown sex/band: {sex!r}/{band!r}") from None


def eer_kcal(sex: str, age_years: float, weight_kg: float, height_m: float, pal: float) -> float:
    if weight_kg <= 0 or height_m <= 0:
        raise ValueError("weight and height must be positive")
    if sex == "female":
        return 354 - 6.91 * age_years + pal * (9.36 * weight_kg + 726 * height_m)
    return 662 - 9.53 * age_years + pal * (15.91 * weight_kg + 539.6 * height_m)
