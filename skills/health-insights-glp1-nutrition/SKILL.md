---
name: health-insights-glp1-nutrition
description: GLP-1 medicine nutrition, protein, hydration and muscle - what research says and what the food log and smart scale can and cannot show.
license: Apache-2.0
---

# Nutrition, muscle and hydration on GLP-1 type medicines

Evidence summary (research notes cite trials and a 2025 joint nutrition advisory; all population level, not personal advice):
- Trials show about 5% to 18% weight loss; roughly a quarter of the weight lost is lean mass on average (range about 15% to 60%). Lean mass is not the same as muscle, and a lower number does not by itself mean lost strength.
- Protein: the general allowance is 0.8 g/kg a day; 1.2 to 1.6 g/kg a day is proposed during active weight loss; using actual weight can overstate need at high body weight. Not for people with kidney disease without their clinician.
- Strength training at least 3 times a week plus about 150 minutes of aerobic activity is the consensus recommendation for protecting muscle.
- Fluids and fibre: constipation and nausea are common; adequate water and fibre help. No evidence-based daily fluid number exists, so do not give one.
- Low intake can leave people short of iron, B12, vitamin D, calcium, magnesium, zinc and thiamine; most gaps are subclinical. Persistent hair shedding, unusual bruising, lasting fatigue or weakness are reasons to see a clinician; blood tests are the clinician's call.
- Rapid weight loss is linked to gallstones; there is no validated kg-per-week threshold.
- After stopping, about two thirds of the lost weight is regained within a year on average; that is a maintenance topic, not an alert.

What the data can show (use `health-insights nutrition --days 28`, `health-insights modules report glp1`):
- Logged protein per kg, average energy, days of very low intake, fibre and micronutrients against the reference table.
- Weight trend, and lean/fat share if a smart scale reports body composition.

What it cannot show:
- Smart-scale (bioimpedance) readings are precise but biased and move with hydration, meals and time of day; compare only readings taken under the same conditions over weeks.
- Food logs are incomplete; low logged protein may be under-logging. Water and micronutrients are often not logged.
- Symptoms such as dehydration, gallbladder trouble or thiamine deficiency show up as symptoms and blood tests first.

Rules for the assistant: present these as information and questions for a dietitian or clinician; do not prescribe diets, supplements or fluid amounts; never give advice about the medicine itself; refer to `health-insights-glp1-warning-signs` for symptoms.
