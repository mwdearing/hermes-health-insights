"""Unit display helpers. Stored data stays canonical (kg); display unit comes from settings (kg default)."""

from health_insights import settings

KG_TO_LB = 2.20462


def kg_to_lb(kg):
    return None if kg is None else kg * KG_TO_LB


def weight_unit() -> str:
    return settings.weight_unit()


def to_display_weight(kg):
    """kg in, the configured display unit out (None passes through)."""
    if kg is None:
        return None
    return kg * KG_TO_LB if weight_unit() == "lb" else kg
