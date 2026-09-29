"""
Statistics helpers (stdlib only: mean, median, MAD, slope).

Baselines use median + MAD (mean absolute deviation) instead of mean +
standard deviation, so a few wild readings do not dominate the baseline.
The median absolute deviation is a robust spread estimate; the "robust z-score"
uses 1.4826 * MAD as a MAD-based standard-deviation analog.
"""

from __future__ import annotations

import statistics
from typing import Sequence


def mean(values: Sequence[float]) -> float:
    """Arithmetic mean. Returns 0.0 for an empty sequence."""
    if not values:
        return 0.0
    return statistics.fmean(values)


def median(values: Sequence[float]) -> float:
    """Median. Returns 0.0 for an empty sequence."""
    if not values:
        return 0.0
    return statistics.median(values)


def mad(values: Sequence[float]) -> float:
    """Mean absolute deviation of the median (robust spread). Returns 0.0 empty."""
    if not values:
        return 0.0
    med = median(values)
    return statistics.fmean(abs(v - med) for v in values)


def robust_std(values: Sequence[float]) -> float:
    """MAD-based standard-deviation analog: 1.4826 * MAD (consistent for normal data)."""
    return 1.4826 * mad(values)


def robust_z(value: float, values: Sequence[float]) -> float:
    """
    Robust z-score of `value` against `values`.

    (value - median) / (1.4826 * MAD). Returns 0.0 when the spread is
    degenerate (all values identical, MAD == 0).
    """
    if not values:
        return 0.0
    std = robust_std(values)
    if std == 0.0:
        return 0.0
    return (value - median(values)) / std


def slope(x: Sequence[float], y: Sequence[float]) -> float:
    """
    Least-squares slope of y over x. Returns 0.0 for fewer than 2 points
    or zero variance in x.
    """
    n = min(len(x), len(y))
    if n < 2:
        return 0.0
    xs = x[:n]
    ys = y[:n]
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(xs, ys))
    sxx = sum((xi - mx) ** 2 for xi in xs)
    if sxx == 0.0:
        return 0.0
    return sxy / sxx
