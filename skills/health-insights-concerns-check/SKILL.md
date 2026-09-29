---
name: health-insights-concerns-check
description: Use to check Apple Health data for things worth attention - out-of-range blood pressure, SpO2/ECG, resting heart rate, HRV, sleep, labs, workouts, data gaps and nutrition gaps - via the health-insights concern rules.
license: Apache-2.0
---

# Health concerns check

1. Run `health-insights concerns --db <bridge.sqlite> [--date YYYY-MM-DD] [--json]`.
2. Findings have a level: 1 worth watching, 2 worth discussing with a clinician, 3 urgent. Lead with the highest level and keep that order.
3. Explain each finding in plain language using only its own evidence text. Do not add causes, diagnoses or treatment.
4. For level 3, say plainly that it warrants prompt medical attention (or emergency care if the user has symptoms) rather than waiting for the next check.
5. If nothing is flagged, say so and mention any data gaps the rules reported.

Rules are deterministic thresholds, not clinical judgement. They can be wrong or miss things. Informational only, not medical advice.
