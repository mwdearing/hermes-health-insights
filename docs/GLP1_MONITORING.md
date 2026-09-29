# GLP-1 / incretin monitoring module (`glp1`)

Opt-in. Turn it on with `health-insights modules enable glp1`, read it with `health-insights modules report glp1 --db <database>`. The same checks feed `health-insights concerns`. Informational only, not medical advice: the module cannot tell what is causing a change and never gives advice about a medication.

## What it checks
| Finding | Rule (your own data) | Why | Level |
| --- | --- | --- | --- |
| `glp1_rhr_rise` | Daily resting heart rate 5 or 10+ bpm above your prior 28-day median on 5 of the last 7 days | Prescribing information lists a mean rise of 1 to 4 bpm and larger maximum changes in a minority (Wegovy label; Zepbound label 1 to 3 bpm) | info / discuss |
| `glp1_bp_lower` | Last 5 cuff readings average 15+ mmHg below your usual systolic | Meta-analyses show small average falls; labels list hypotension and syncope | info |
| `glp1_weight_rate` | 7-day mean weight down 1.5%+ per week versus days 28 to 34 ago | Rapid loss goes with gallstones and lean-mass loss; no evidence-based cut-off exists, so this is a tunable default | info |
| `glp1_protein_low` | Logged protein under 1.2 g/kg a day over 5+ logged days (skipped with `ckd: true`) | 2025 joint nutrition advisory (PMID 40445127); ADA review | info |
| `glp1_energy_low` | 3+ of 7 logged days under 1000 kcal | Very low intake risks protein and micronutrient shortfall (PMID 40507203); may be under-logging | info |
| `glp1_dehydration_pattern` | 3+ of: weight down 2% in 3 days, resting heart rate up 5+, lower blood pressure, very low intake | Labels warn of kidney injury from vomiting, diarrhea and volume depletion | discuss |
| `glp1_lean_share` | 40%+ of the weight lost over 4 weeks is lean mass on a smart scale (and more than 1.5 kg) | Trials average about 25% (PMID 39996356, 41877354); bioimpedance is biased (PMID 39691170) | info |
| `glp1_strength_gap` | Under 2 strength workouts a week while weight falls 2%+ | Consensus: strength training at least 3 times a week (PMID 40445127) | info |
| `glp1_glucose_low` | Glucose under 70 mg/dL (3+ readings, or any under 54) in 7 days | Consensus definitions for hypoglycemia levels (PMID 31177185); labels | info / discuss |

Also printed by the report, whatever the data says: warning signs needing urgent care (pancreatitis, gallbladder disease, dehydration, allergic reaction, low blood sugar, sudden vision change, suicidal thoughts with the US 988 line, chest pain or fainting) and things to raise promptly with a clinician (neck lump or hoarseness, planned pregnancy, upcoming procedures under sedation, fast loss with low intake, low mood).

## How to read it
- Every rule compares you with YOUR baseline and needs several days of data. The report lists what it did not have enough data for.
- Apple Watch does not measure blood pressure or glucose; those need a cuff or a linked sensor. Watch heart rate is well validated on average but individual readings vary by about 10 bpm; sleep stages and energy estimates are poor.
- Food logs are incomplete; smart scales move with hydration and meals. Treat single readings as noise and trends over weeks as the signal.
- Investigational agents have no label; class cautions are applied and evidence for the specific agent is limited.

## Options
In `~/.config/health-insights/modules.yaml`:
```yaml
glp1:
  enabled: true
  options:
    weight_pct_per_week: 2.0    # default 1.5
    rhr_discuss_bpm: 10         # default 10; rhr_info_bpm default 5
    protein_target_g_per_kg: 1.2
    energy_floor_kcal: 1000
    ckd: true                   # skip the protein check
```
All defaults are in `health_insights/concern_rules/glp1_rules.py` (`DEFAULTS`). They are heuristics, not clinical cut-offs.

## Limits and what is not covered
- Not covered: dose-timing analysis, kidney and liver lab schedules (the labs skill flags out-of-range results only), retinopathy screening, and anything that needs a clinician. No guideline-backed lab schedule was verified in the research behind this module.
- Sources: FDA labels (Wegovy, Zepbound, Saxenda, Ozempic), the ADA obesity pharmacotherapy article (PMC12815056), the 2025 joint nutrition advisory (PMID 40445127), the FDA 2026-01-13 notice on suicidal ideation labeling, SURMOUNT-1 DXA (PMID 39996356), and meta-analyses cited inline. Investigational triple agonists: phase 2 and 3 trial reports only.
- Wording is linted in the test suite: findings must not contain dosing or medication-change advice.
