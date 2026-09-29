# hermes-health-insights

Deterministic, local-first analysis of your own Apple Health data, packaged as a [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin (skills) plus a small command line tool. The code computes the numbers and findings; your agent explains them.

Works on the SQLite database written by a self-hosted [HealthRelay](https://github.com/mwdearing/health-relay) (or Health Bridge) receiver. Pair it with [hermes-healthrelay](https://github.com/mwdearing/hermes-healthrelay), which gives your agent read-only MCP access to the same data.

> Informational only, not medical advice. The rules are simple thresholds and can be wrong.

## What it does
| Command | Purpose |
| --- | --- |
| `health-insights weekly` | 7-day vs 28-day trends: sleep, resting heart rate, HRV, weight, SpO2, blood pressure |
| `health-insights concerns` | Rule-based findings (levels 1 to 3): blood pressure, SpO2/ECG, vitals, labs, workouts, data gaps, nutrition gaps |
| `health-insights nutrition` | Food log vs Dietary Reference Intakes, plus an energy line against an IOM energy target |
| `health-insights labs ...` | Import and trend lab results from Apple Health clinical records |
| `health-insights coverage/daily/baseline/anomalies/bp/workouts/ecg` | Building blocks and detail views |
| `health-insights demo` | Write a synthetic database so you can try everything with no real data |

## Install
```bash
pipx install git+https://github.com/mwdearing/hermes-health-insights
health-insights demo --out demo.sqlite
health-insights weekly --db demo.sqlite
```

As a Hermes plugin (skills for setup, weekly review, concerns, nutrition, energy target and labs):
```bash
hermes plugins install mwdearing/hermes-health-insights --no-enable
hermes plugins enable health-insights
```
Then follow the `health-insights-setup` skill. Configuration is one optional file, `~/.config/health-insights/config.yaml` (timezone, weight unit, data directory, receiver database, energy profile).

## Optional modules (all off by default)
Extra checks for specific situations are opt-in modules. See what exists and switch them with:
```bash
health-insights modules list
health-insights modules info <id>
health-insights modules enable <id>     # and: disable <id>
```
Available now: `glp1` (monitoring while on a GLP-1 type medicine, [details](docs/GLP1_MONITORING.md)) and `medication_adherence`. Details, the settings file and how to add a module: [docs/MODULES.md](docs/MODULES.md). Your agent can do this for you with the `health-insights-modules` skill, and only when you ask.

## Privacy and trust
- Everything runs on your machine. Nothing is uploaded.
- Personal integrations are off by default: missed-dose counts from a `medlog` CLI, a push command, and wording by a local model endpoint. With no model configured a fixed template writes the summary.
- Health data is sensitive. Use a local model, or one you trust with it, and keep raw values out of public channels.
- Enabling any Hermes plugin grants its skills full trust. The plugin contains only skills; the CLI is installed separately.

## Limits
- The bundled DRI table is for adult men aged 31 to 50. Supply your own table with `--dri` for other groups.
- The energy target uses the standard IOM equation for adults and needs your `profile`; it is an estimate.
- This project is not affiliated with Apple.

## Development
```bash
pip install -e . pytest
pytest
```
Tests use synthetic data only. Licensed Apache-2.0.
