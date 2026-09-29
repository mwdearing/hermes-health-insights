---
name: health-insights-modules
description: Use when the user asks what optional health-insights modules exist, wants to turn one on or off, or asks what a module checks and what data it needs. Modules are opt-in and off by default.
license: Apache-2.0
---

# Optional modules

health-insights has a small set of core checks that always run, and OPTIONAL MODULES for specific situations (for example medication adherence, or monitoring while on a particular kind of medication). Every optional module is OFF until the user turns it on. Never turn one on without being asked.

Commands (all local, they only read and write a small settings file):
- `health-insights modules list` shows every module with `[on ]` or `[off]` and a one-line description. `--json` gives the same as data.
- `health-insights modules info <id> [--db <database>]` explains what it does, what data it works best with, its cautions, and (with `--db`) which of those data types the user's database actually has.
- `health-insights modules enable <id>` / `disable <id>` switch it. The switch is stored in `~/.config/health-insights/modules.yaml`, which the user can also edit by hand (`id: true` or `false`). `HEALTH_INSIGHTS_MODULES=id1,id2` turns modules on for a single run.

How to help
1. Ask what the user wants to track, run `modules list`, and describe the matching modules in plain language.
2. Before enabling, read the module's cautions aloud, run `modules info <id> --db <database>`, and say which needed data is missing (a module with missing data will simply have less to say).
3. Enable only on the user's explicit request, then confirm with `modules list`. To undo, `disable`.
4. Modules change what `health-insights concerns` reports. They never change doses, schedules or medication, and they give no dosing advice. If asked for that, decline and refer to their clinician.
