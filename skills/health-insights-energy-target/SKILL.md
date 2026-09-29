---
name: health-insights-energy-target
description: Use when the user asks what their daily calorie target is or how their intake compares - explains the IOM Estimated Energy Requirement used by health-insights and how to change activity level and goal.
license: Apache-2.0
---

# Energy target

health-insights uses the IOM (2005) Estimated Energy Requirement for adults: sex, age, weight (latest reading within 14 days), height and a physical activity level (PAL band: sedentary, low_active, active, very_active). `goal_kcal` shifts the target (0 maintain, negative deficit, positive surplus).

- Settings live under `profile:` in `~/.config/health-insights/config.yaml`; see the setup skill.
- The report line shows the EER, the band and goal, and a cross-check (Mifflin-St Jeor times a standard factor) only when the two differ by more than 10 percent. Say plainly that these are estimates with real uncertainty.
- Do not choose the activity band or goal for the user; ask them, and remember that activity level is a judgement call.
- Limits: the equation is for adults, not pregnancy, medical conditions or athletes in heavy training. There is no separate variant for high BMI. Do not give weight-loss or medical advice.
