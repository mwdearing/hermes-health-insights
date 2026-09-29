---
name: health-insights-setup
description: Use when the user wants to set up the health-insights CLI - install it, point it at their HealthRelay receiver database, set timezone, units and profile, and try it on demo data first.
license: Apache-2.0
compatibility: Python 3.11+, PyYAML. Reads a HealthRelay / Health Bridge receiver SQLite database.
---

# health-insights setup

The plugin only holds skills. The analysis code is the `health-insights` command line tool; the agent runs it and explains the output.

1. Install: `pipx install git+https://github.com/mwdearing/hermes-health-insights` (or `uv tool install` / a venv). Check with `health-insights --help`.
2. Try it on synthetic data first, no health data involved:
   `health-insights demo --out demo.sqlite` then `health-insights weekly --db demo.sqlite`.
3. Point it at the user's receiver database (the file the HealthRelay receiver writes; see the `healthrelay` plugin's setup skill). Use a COPY or the live file read-only; never write to it.
4. Create `~/.config/health-insights/config.yaml` (all keys optional):
   ```yaml
   timezone: America/New_York      # default: the system timezone
   weight_unit: lb                 # kg (default) or lb; stored data is always kg
   data_dir: ~/.local/share/health-insights
   bridge_db: /path/to/device.sqlite
   profile:                        # only needed for the energy target
     sex: male                     # male | female
     age_years: 40
     pal: low_active               # sedentary | low_active | active | very_active
     goal_kcal: 0                  # 0 = maintain, negative = deficit
   ```
5. Optional integrations are OFF by default: `integrations.medlog` (missed-dose counts from a `medlog` CLI), `notify_command`, and `narration.url` (an OpenAI-compatible LOCAL model for wording; otherwise a fixed template is used).

Privacy: everything runs locally. Do not upload the database, paste raw values into public places, or point `narration.url` at a cloud model unless the user accepts that.
