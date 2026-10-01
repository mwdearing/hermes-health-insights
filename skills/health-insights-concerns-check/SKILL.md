---
name: health-insights-concerns-check
description: Health concerns check - rule-based findings (levels 1 to 3) for blood pressure, SpO2/ECG, vitals, sleep, labs, workouts, data gaps and nutrition gaps.
license: Apache-2.0
---

# Health concerns check

**First, run `health-insights doctor`.** If it exits 2, show its output to the user and stop. Never look for a database or write SQL yourself: the receiver database is the one `doctor` prints (set once with `bridge_db`; see `health-insights-setup`), and `--db` is only an override. Exit 1 means the data is stale or sparse: say so before reporting anything, because missing numbers then do not mean a normal day.

1. Run `health-insights concerns --json [--date YYYY-MM-DD]`. The database comes from the setup (see `health-insights-setup`); add `--db <path>` only to override it. The plain-text form (`👀 [worth watching] title - evidence`) carries the same information, but JSON is exact.
2. Each finding has `level`, `title`, `evidence` and `advice`. Levels: 1 worth watching, 2 worth discussing with a clinician, 3 urgent. Lead with the highest level and keep that order.
3. Explain each finding in plain language using only its `evidence` and `advice` text. Do not add causes, diagnoses or treatment.
4. For level 3, say plainly that it warrants prompt medical attention (or emergency care if the user has symptoms) rather than waiting for the next check.
5. Findings with ids `concerns_unavailable` or `store_stale` mean the check could not run fully or the newest data is old. Say that first: the absence of other findings then does NOT mean things look fine.
6. If nothing is flagged, say so and mention any data gaps the rules reported. If the command exits with an error (status 2), tell the user the database is not set up and follow `health-insights-setup`.

Rules are deterministic thresholds, not clinical judgement. They can be wrong or miss things. Informational only, not medical advice.
