---
name: health-insights-nutrition-review
description: Nutrition vs Dietary Reference Intakes - food-log averages per nutrient, days low, limits exceeded and the energy line versus target.
license: Apache-2.0
---

# Nutrition review

**First, run `health-insights doctor`.** If it exits 2, show its output to the user and stop. Never look for a database or write SQL yourself: the receiver database is the one `doctor` prints (set once with `bridge_db`; see `health-insights-setup`), and `--db` is only an override. Exit 1 means the data is stale or sparse: say so before reporting anything, because missing numbers then do not mean a normal day.

1. Run `health-insights nutrition [--days 28] [--date YYYY-MM-DD] [--json]`. The database comes from the setup (see `health-insights-setup`); add `--db <path>` only to override it.
2. The output names its reference table on the second line. The default is Dietary Reference Intakes for adult men 31 to 50. For anyone else, say so and treat the comparison as a rough guide, or supply a different table with `--dri path.yaml` (same structure as `health_insights/data/dri.yaml`).
3. The food log only contains what the user logs and what their apps write to Apple Health. Days with very low energy are counted as partial. Say when a nutrient is simply not tracked instead of calling it deficient.
4. The energy line needs a `profile` (see the setup skill) and a weight within the last 14 days; otherwise it says why there is no target.
5. Report facts and gaps. The "Consistently below target" line lists nutrients that stay low across the window. Do not recommend supplements or diets; suggest discussing persistent gaps with a clinician or dietitian.

Informational only, not medical advice.
