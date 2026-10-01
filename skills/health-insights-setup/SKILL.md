---
name: health-insights-setup
description: Set up health-insights - install the CLI, point it at your HealthRelay database once, try the demo, set timezone, units and energy profile.
license: Apache-2.0
compatibility: Python 3.11+, PyYAML. Reads a HealthRelay / Health Bridge receiver SQLite database.
---

# health-insights setup

The plugin only holds skills. The analysis code is the `health-insights` command line tool, installed separately; the agent runs it and explains the output. Work through the checklist in order and confirm each expected output before moving on.

1. **Is the tool installed?** Run `health-insights --version`.
   - Expected: `health-insights <version>`.
   - If "command not found": install it from this plugin's own copy, which Hermes already fetched at the installed commit: `pipx install "${HERMES_HOME:-$HOME/.hermes}/plugins/health-insights"` (or `uv tool install` / a venv from the same path), then run `--version` again. Do not install from the repository's latest commit.
2. **Try it on synthetic data** (no health data involved): `health-insights demo --out demo.sqlite`, then `health-insights weekly --db demo.sqlite`.
   - Expected: a "Weekly health ..." report with a line per metric.
3. **Set the database once** (the file the HealthRelay receiver writes; see the `healthrelay` plugin's setup skill). Use a COPY or the live file read-only; never write to it. The tool looks in this order and uses the first that is set:
   1. `--db <path>` on the command
   2. the `HEALTH_INSIGHTS_BRIDGE_DB` environment variable
   3. `bridge_db:` in `~/.config/health-insights/config.yaml`
   4. the first line of `~/.config/healthrelay/db-path`
   Recommended: write the path into `~/.config/healthrelay/db-path` (one line). If the healthrelay plugin is also used, both tools then share one setting.
4. **Confirm without `--db`**: run `health-insights concerns`.
   - Expected: either finding lines such as `👀 [worth watching] title - evidence`, or a line saying nothing is flagged.
   - If it prints `cannot open the receiver database ...` and exits with status 2, the path from step 3 is missing or wrong; fix it and repeat. Do not guess a path.
5. **Optional profile and preferences.** Create `~/.config/health-insights/config.yaml` (every key optional):
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
   Ask the user for the profile values; do not choose them for the user.

## Advanced (optional)
`integrations.*` (for example `integrations.medlog`) and `narration.url` are hooks for personal tooling that is NOT included with this plugin: a separate `medlog` command line tool and a local OpenAI-compatible model for wording (its URL must be a loopback address such as `http://127.0.0.1:8080/...`; anything else is refused). Everything works without them (a fixed template writes summaries). Do not set them up unless the user says they have those tools.

Privacy: everything runs locally. Do not upload the database, paste raw values into public places, or point `narration.url` at a cloud model unless the user accepts that.
