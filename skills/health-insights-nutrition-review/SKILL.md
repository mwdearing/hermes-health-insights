---
name: health-insights-nutrition-review
description: Use to compare the Apple Health food log with Dietary Reference Intakes - averages per nutrient, days low, limits exceeded, and the energy line versus target.
license: Apache-2.0
---

# Nutrition review

1. Run `health-insights nutrition --db <db> [--days 28] [--date YYYY-MM-DD] [--json]`.
2. The bundled DRI table is for adult men aged 31 to 50. For anyone else, tell the user the comparison is only a rough guide, or supply a different table with `--dri path.yaml` (same structure as `health_insights/data/dri.yaml`).
3. The food log only contains what the user logs and what their apps write to Apple Health. Days with very low energy are counted as partial. Say when a nutrient is simply not tracked instead of calling it deficient.
4. The energy line needs a `profile` (see the setup skill) and a weight within the last 14 days; otherwise it says why there is no target.
5. Report facts and gaps. Do not recommend supplements or diets; suggest discussing persistent gaps with a clinician or dietitian.

Informational only, not medical advice.
