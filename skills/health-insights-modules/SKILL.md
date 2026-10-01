---
name: health-insights-modules
description: Optional modules - list what exists, explain what one checks and needs, and turn it on or off (all off by default; only when asked).
license: Apache-2.0
---

# Optional modules

**First, run `health-insights doctor`.** If it exits 2, show its output to the user and stop. Never look for a database or write SQL yourself: the receiver database is the one `doctor` prints (set once with `bridge_db`; see `health-insights-setup`), and `--db` is only an override. Exit 1 means the data is stale or sparse: say so before reporting anything, because missing numbers then do not mean a normal day.

health-insights has a small set of core checks that always run, and OPTIONAL MODULES for specific situations (for example medication adherence, or monitoring while on a particular kind of medication). `medication_adherence` needs the separate `medlog` command line tool, which is not included; do not offer to enable it unless the user says they have that tool. Every optional module is OFF until the user turns it on. Never turn one on without being asked.

Commands (all local, they only read and write a small settings file):
- `health-insights modules list` shows every module with `[on ]` or `[off]` and a one-line description. `--json` gives the same as data.
- `health-insights modules info <id> [--db <database>]` (`--db` is optional once the database is set up) explains what it does, what data it works best with, its cautions, and (with `--db`) which of those data types the user's database actually has.
- `health-insights modules enable <id>` / `disable <id>` switch it. The switch is stored in `~/.config/health-insights/modules.yaml`, which the user can also edit by hand (`id: true` or `false`). `HEALTH_INSIGHTS_MODULES=id1,id2` turns modules on for a single run.

How to help
1. Ask what the user wants to track, run `modules list`, and describe the matching modules in plain language.
2. Before enabling, read the module's cautions aloud, run `modules info <id>`, and say which needed data is missing (a module with missing data will simply have less to say).
3. Enable only on the user's explicit request, then confirm with `modules list`. To undo, `disable`.
4. Modules change what `health-insights concerns` reports. They never change doses, schedules or medication, and they give no dosing advice. If asked for that, decline and refer to their clinician.
