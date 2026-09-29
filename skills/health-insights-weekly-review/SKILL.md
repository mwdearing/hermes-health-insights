---
name: health-insights-weekly-review
description: Use for a weekly Apple Health trends summary - 7-day vs 28-day per metric (sleep, resting heart rate, HRV, weight, SpO2, blood pressure) using the health-insights CLI.
license: Apache-2.0
---

# Weekly health review

1. Run `health-insights weekly --db <bridge.sqlite> [--date YYYY-MM-DD]`. Without `--date` it uses the current local day.
2. The output is computed, not estimated. Report it faithfully: which metrics moved, by how much, and any "Notable" line. Never invent numbers or causes.
3. Say when a metric has too little data ("insufficient", "no data") instead of guessing.
4. Optional detail: `health-insights daily <db> <type_code>` and `health-insights baseline <db> <type_code>` for one metric; `health-insights coverage <db> --md` for freshness and gaps.
5. Weight is shown in the configured unit (kg or lb).

Limits: informational only, not medical advice. Do not diagnose or advise on medication. Keep raw values out of shared channels.
