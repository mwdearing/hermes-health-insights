---
name: health-insights-labs-review
description: Use when the user has lab results from an Apple Health export or HealthRelay and wants them summarised or trended - import, summary and per-LOINC trend via the health-insights CLI.
license: Apache-2.0
---

# Lab results review

Lab results live in their own small SQLite file (default under `data_dir/labs/labs.sqlite`).

- Import from an Apple Health export: `health-insights labs import export_YYYY-MM-DD.zip --db labs.sqlite` (reads only `clinical-records`; the zip is not unpacked).
- Import results a HealthRelay receiver already holds: `health-insights labs import-bridge --bridge <receiver.sqlite> --db labs.sqlite`.
- Import a FHIR JSON file: `health-insights labs import-json file.json --db labs.sqlite`.
- Summary: `health-insights labs summary --db labs.sqlite`. One analyte over time: `health-insights labs trend --db labs.sqlite --loinc <code>`.

Imports are idempotent. Flags (low, high, normal, unknown) come from the reference range in each result. Report values with their units and reference ranges, note results with no range as unknown, and never interpret a result clinically; refer questions about meaning to a clinician. Lab data is sensitive: keep it local.
