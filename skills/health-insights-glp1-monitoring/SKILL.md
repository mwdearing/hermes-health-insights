---
name: health-insights-glp1-monitoring
description: GLP-1 medicine monitoring - opt-in module that checks your own Apple Health data for patterns worth knowing about; explains findings, never advises on medication.
license: Apache-2.0
compatibility: Needs the health-insights CLI and a HealthRelay/Health Bridge receiver database. Best with weight, resting heart rate, food log and blood pressure data.
---

# GLP-1 / incretin monitoring

**First, run `health-insights doctor`.** If it exits 2, show its output to the user and stop. Never look for a database or write SQL yourself: the receiver database is the one `doctor` prints (set once with `bridge_db`; see `health-insights-setup`), and `--db` is only an override. Exit 1 means the data is stale or sparse: say so before reporting anything, because missing numbers then do not mean a normal day.

This is an OPT-IN module. Check `health-insights modules list`; if `glp1` is off, explain what it does (`modules info glp1`) and turn it on only if the user asks (see the `health-insights-modules` skill).

## What it does
`health-insights modules report glp1 [--date YYYY-MM-DD]` (database from the setup; `--db <path>` only to override) prints measurements, "worth a look" findings, data gaps, and the warning-sign lists. The same checks also feed `health-insights concerns`. All comparisons use the user's OWN baselines (mostly the prior 28 days) and need persistence over several days, so one odd reading does not fire anything.

| Finding id | What it looks at | Wording level |
| --- | --- | --- |
| `glp1_rhr_rise` | Resting heart rate 5 or 10+ bpm above the prior 28-day median on 5 of the last 7 days | information / worth discussing |
| `glp1_bp_lower` | Last 5 cuff readings 15+ mmHg below the usual (needs a cuff; the watch does not measure blood pressure) | information |
| `glp1_weight_rate` | Weight down 1.5% per week or more over about 4 weeks (a tunable default, no evidence-based cut-off exists) | information |
| `glp1_protein_low` | Logged protein under 1.2 g/kg a day (allowance 0.8) | information |
| `glp1_energy_low` | 3+ of 7 logged days under 1000 kcal | information |
| `glp1_dehydration_pattern` | Three or more of: fast weight drop, resting heart rate up, lower blood pressure, very low intake | worth discussing; urgent wording for vomiting, fainting |
| `glp1_lean_share` | 40%+ of weight lost is lean mass on a smart scale (scales are noisy) | information |
| `glp1_strength_gap` | Under 2 strength sessions a week while weight drops | information |
| `glp1_glucose_low` | Glucose readings under 70 or 54 mg/dL (needs a sensor or manual entries) | information / worth discussing |

## How to talk about it
1. Lead with what the data shows and how much data there was ("not enough data yet" is a valid answer). Never invent numbers.
2. Say plainly that a pattern does not show what caused it. Illness, sleep, alcohol, heat, dehydration and scale noise explain many of these.
3. Wording ladder: information (no action), worth discussing (mention to the clinician), urgent (only for the symptom lists in `health-insights-glp1-warning-signs`).
4. Small average heart-rate rises and lower blood pressure are listed in prescribing information for these medicines; say that a small change is expected and a large or persistent one is worth mentioning.
5. Investigational agents have no label. Say class-based cautions are being applied and evidence for that specific agent is limited.

## Hard limits
- No dosing or medication advice of any kind: do not suggest changing, skipping, delaying or timing a dose, or stopping a medicine. If asked, say that is for the prescriber, and offer to summarise the data they might want to bring.
- No diagnosis, and never say a medicine caused or is safe or unsafe for the user.
- Health data is sensitive: keep it local, do not paste raw values into public places.
- Tuning thresholds: `~/.config/health-insights/modules.yaml`, `glp1: {enabled: true, options: {weight_pct_per_week: 2.0, ckd: true}}`. Set `ckd: true` if the user has kidney disease (the protein check is then skipped).
