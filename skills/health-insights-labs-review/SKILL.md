---
name: health-insights-labs-review
description: Lab results - import from an Apple Health export or HealthRelay, summarise and trend per LOINC code with the health-insights CLI.
license: Apache-2.0
---

# Lab results review

**First, run `health-insights doctor`.** If it exits 2, show its output to the user and stop. Never look for a database or write SQL yourself: the receiver database is the one `doctor` prints (set once with `bridge_db`; see `health-insights-setup`), and `--db` is only an override. Exit 1 means the data is stale or sparse: say so before reporting anything, because missing numbers then do not mean a normal day.

Lab results live in their own small SQLite file. For `labs` commands `--db` means that labs file, and it defaults to `<data_dir>/labs/labs.sqlite` (usually `~/.local/share/health-insights/labs/labs.sqlite`), so it is normally not needed.

- Import from an Apple Health export: `health-insights labs import export_YYYY-MM-DD.zip` (reads only `clinical-records`; the zip is not unpacked).
- Import results a HealthRelay receiver already holds: `health-insights labs import-bridge` (the receiver database comes from the setup; `--bridge <receiver.sqlite>` overrides it).
- Import a FHIR JSON file: `health-insights labs import-json file.json`.
- Summary: `health-insights labs summary`. One analyte over time: `health-insights labs trend --loinc <code>`. Add `--db <labs.sqlite>` only to use a different labs file.

Imports are idempotent. Flags (low, high, normal, unknown) come from the reference range in each result. Report values with their units and reference ranges, note results with no range as unknown, and never interpret a result clinically; refer questions about meaning to a clinician. Lab data is sensitive: keep it local.
