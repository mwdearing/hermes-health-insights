"""
health_insights: stdlib-only health monitoring library.

Reads a bridge SQLite snapshot (or a merged history DB), computes daily
aggregates, 30/90-day baselines (median + MAD), trend slopes, coverage and
freshness, and emits stable JSON (schema v1) plus a short Markdown report.

Not medical advice. Aggregates only.
"""

__version__ = "1.37.0"
