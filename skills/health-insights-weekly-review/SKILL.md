---
name: health-insights-weekly-review
description: Weekly Apple Health trends - 7-day vs 28-day sleep, resting heart rate, HRV, weight, SpO2 and blood pressure, plus single-metric daily and baseline views.
license: Apache-2.0
---

# Weekly health review

**First, run `health-insights doctor`.** If it exits 2, show its output to the user and stop. Never look for a database or write SQL yourself: the receiver database is the one `doctor` prints (set once with `bridge_db`; see `health-insights-setup`), and `--db` is only an override. Exit 1 means the data is stale or sparse: say so before reporting anything, because missing numbers then do not mean a normal day.

1. Run `health-insights weekly [--date YYYY-MM-DD]`. The database comes from the setup (see `health-insights-setup`); add `--db <path>` only to override it. Without `--date` it uses the current local day in the configured time zone. If it exits with "cannot open the receiver database", follow the setup skill instead of guessing a path.
2. The output is computed, not estimated. Report it faithfully: which metrics moved, by how much, and any "Notable" line. Never invent numbers or causes.
3. When a metric has too little data the report says "not enough data" (or "no data"). Pass that on as is; never read it as zero.
4. Optional detail for one metric: `health-insights daily <type_code>` (per-day values) and `health-insights baseline <type_code>` (30-day and 90-day statistics), for example `resting_heart_rate` or `weight`; both print JSON. `health-insights coverage --md` shows data freshness and gaps.
5. Weight is shown in the configured unit (kg or lb).

Limits: informational only, not medical advice. Do not diagnose or advise on medication. Keep raw values out of shared channels.
