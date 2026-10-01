# hermes-health-insights

Deterministic, local-first analysis of your own Apple Health data, packaged as a [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin (skills) plus a small command line tool. The code computes the numbers and findings; your agent explains them.

Works on the SQLite database written by a self-hosted [HealthRelay](https://github.com/mwdearing/health-relay) (or Health Bridge) receiver. Pair it with [hermes-healthrelay](https://github.com/mwdearing/hermes-healthrelay), which gives your agent read-only MCP access to the same data.

> Informational only, not medical advice. The rules are simple thresholds and can be wrong.

Setting up the whole chain (app and receiver, hermes-healthrelay, this plugin, hermes-medlog)? Follow the [Full setup guide](https://github.com/mwdearing/health-relay/blob/main/docs/full-setup.md): one ordered walkthrough with a check after each step.

## What it does
| Command | Purpose |
| --- | --- |
| `health-insights weekly` | 7-day vs 28-day trends: sleep, resting heart rate, HRV, weight, SpO2, blood pressure |
| `health-insights concerns` | Rule-based findings (levels 1 to 3): blood pressure, SpO2/ECG, vitals, labs, workouts, data gaps, nutrition gaps |
| `health-insights nutrition` | Food log vs Dietary Reference Intakes (default table: adult men 31 to 50, see [Limits](#limits); pass `--dri` for other groups), plus an energy line against an IOM energy target |
| `health-insights labs ...` | Import and trend lab results from Apple Health clinical records |
| `health-insights coverage/daily/baseline/anomalies/bp/workouts/ecg` | Building blocks and detail views |
| `health-insights demo` | Write a synthetic database so you can try everything with no real data |

## Install
Two steps: the plugin adds skills to your agent; the analysis tool is installed separately with pipx.

1. Install the tool and try it on synthetic data (no health data involved):
   ```bash
   pipx install git+https://github.com/mwdearing/hermes-health-insights@<sha>   # 40-character sha of the release you want
   health-insights --version
   health-insights demo --out demo.sqlite
   health-insights weekly --db demo.sqlite
   ```
2. Install the plugin (skills for setup, weekly review, concerns, nutrition, energy target, labs and modules):
   ```bash
   hermes plugins install mwdearing/hermes-health-insights --no-enable
   hermes plugins enable health-insights
   ```
   Then ask your agent to follow the `health-insights-setup` skill.

### Upgrade
`hermes plugins update health-insights` refuses installs pinned to a commit. Move both pieces to a new commit (use the 40-character sha of the release you want):
```bash
hermes plugins install mwdearing/hermes-health-insights --force --ref <sha>
pipx install --force git+https://github.com/mwdearing/hermes-health-insights@<sha>
```
Run the first command in an **interactive terminal**: the plugin declares a PyYAML dependency and Hermes asks before installing it. A non-interactive run skips the dependency and refuses to replace an active plugin (`--no-deps` cannot replace an active plugin either). `--force` keeps the plugin enabled or disabled as it was; start a new session afterwards. Check the tool with `health-insights --version`. Hermes installs the plugin into `plugins/health-insights` under its home (`hermes plugins list` shows the name).

### Point it at your database
Set the receiver database once; after that no command needs `--db`. The tool uses the first of these that is set:
1. `--db <path>` on the command
2. the `HEALTH_INSIGHTS_BRIDGE_DB` environment variable
3. `bridge_db:` in `~/.config/health-insights/config.yaml`
4. the first line of `~/.config/healthrelay/db-path` (the same file the [hermes-healthrelay](https://github.com/mwdearing/hermes-healthrelay) plugin reads, so one setting serves both)

Check it with `health-insights concerns`. If the database cannot be opened, the tool prints one line saying so and exits with status 2. The rest of `config.yaml` is optional (timezone, weight unit, data directory, energy profile).

## Uninstall
```bash
hermes plugins uninstall health-insights      # also: hermes plugins list, hermes plugins show health-insights
pipx uninstall health-insights
```
Optionally delete what the tool created: `~/.config/health-insights/` (settings and module switches) and `~/.local/share/health-insights/` (imported lab results and other local data). Your receiver database and `~/.config/healthrelay/db-path` belong to HealthRelay and are not touched.

## Optional modules (all off by default)
Extra checks for specific situations are opt-in modules. See what exists and switch them with:
```bash
health-insights modules list
health-insights modules info <id>
health-insights modules enable <id>     # and: disable <id>
```
Available now: `glp1` (monitoring while on a GLP-1 type medicine, [details](docs/GLP1_MONITORING.md)) and `medication_adherence` (needs the separate `medlog` CLI, which is not included). Details, the settings file and how to add a module: [docs/MODULES.md](docs/MODULES.md). Your agent can do this for you with the `health-insights-modules` skill, and only when you ask.

## Privacy and trust
- Everything runs on your machine. Nothing is uploaded.
- Personal integrations are off by default and are not part of this plugin: missed-dose counts need a separate `medlog` CLI, and there are hooks for a push command and a local model endpoint for wording. Nothing here depends on them; with no model configured a fixed template writes the summary.
- Health data is sensitive. Use a local model, or one you trust with it, and keep raw values out of public channels.
- Enabling any Hermes plugin grants its skills full trust. The plugin contains only skills; the CLI is installed separately.

## Limits
- The bundled DRI table is for adult men aged 31 to 50, and nutrition output names it. It is only a rough guide for anyone else: supply your own table with `--dri` for other groups.
- The energy target uses the standard IOM equation for adults and needs your `profile`; it is an estimate.
- This project is not affiliated with Apple.

## Development
```bash
pip install -e . pytest
pytest
```
Tests use synthetic data only. Licensed Apache-2.0.
