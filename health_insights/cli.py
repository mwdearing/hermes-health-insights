"""
Command-line interface for the health_insights library.

Usage:
    python -m health_insights.cli coverage <db_path> [--window 30]
    python -m health_insights.cli daily    <db_path> <type_code> [--window 30]
    python -m health_insights.cli baseline <db_path> <type_code> [--window 30]
    python -m health_insights.cli bp <db_path> --date YYYY-MM-DD

`coverage` prints the full coverage/freshness report (JSON + Markdown).
`daily` prints the daily aggregate table for one type.
`baseline` prints the 30/90-day baseline for one type.

All output is local; no values are sent anywhere.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import analysis
from health_insights import settings


def _parse_args(argv):
    parser = argparse.ArgumentParser(prog="health_insights", description="Health monitoring analysis (stdlib).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_cov = sub.add_parser("coverage", help="Full coverage/freshness report")
    p_cov.add_argument("db_path", help="Path to the bridge SQLite snapshot")
    p_cov.add_argument("--window", type=int, default=analysis.DEFAULT_METRIC_WINDOW_DAYS)
    p_cov.add_argument("--baseline", type=int, default=analysis.DEFAULT_BASELINE_WINDOW_DAYS)
    p_cov.add_argument("--json", action="store_true", help="Print JSON document")
    p_cov.add_argument("--md", action="store_true", help="Print Markdown report")
    p_cov.add_argument("--out", help="Write JSON to file")

    p_daily = sub.add_parser("daily", help="Daily aggregates for one type")
    p_daily.add_argument("db_path")
    p_daily.add_argument("type_code")
    p_daily.add_argument("--window", type=int, default=analysis.DEFAULT_METRIC_WINDOW_DAYS)

    p_base = sub.add_parser("baseline", help="30/90-day baseline for one type")
    p_base.add_argument("db_path")
    p_base.add_argument("type_code")
    p_base.add_argument("--window", type=int, default=analysis.DEFAULT_METRIC_WINDOW_DAYS)
    p_base.add_argument("--baseline", type=int, default=analysis.DEFAULT_BASELINE_WINDOW_DAYS)

    p_anom = sub.add_parser("anomalies", help="Anomaly flags for one local day")
    p_anom.add_argument("db_path")
    p_anom.add_argument("--date", help="Local day (YYYY-MM-DD); defaults to yesterday Chicago")
    p_anom.add_argument("--config", help="Path to health_monitor.yaml")

    p_bp = sub.add_parser("bp", help="Blood pressure bands and flags for one local day")
    p_bp.add_argument("db_path")
    p_bp.add_argument("--date", help="Local day (YYYY-MM-DD); defaults to Chicago today")

    p_demo = sub.add_parser("demo", help="Write a synthetic demo database (no real data) to try the commands")
    p_demo.add_argument("--out", required=True, help="Path of the SQLite file to create")
    p_demo.add_argument("--days", type=int, default=60)

    p_mod = sub.add_parser("modules", help="Optional modules you can turn on or off (all off by default)")
    mod_sub = p_mod.add_subparsers(dest="modules_command", required=True)
    p_ml = mod_sub.add_parser("list", help="Show every module and whether it is on")
    p_ml.add_argument("--json", action="store_true")
    for name, text in (("enable", "Turn a module on"), ("disable", "Turn a module off")):
        p_mx = mod_sub.add_parser(name, help=text)
        p_mx.add_argument("module_id")
    p_mr = mod_sub.add_parser("report", help="A readable report from a module that has one (for example glp1)")
    p_mr.add_argument("module_id")
    p_mr.add_argument("--db", required=True, help="Path to the bridge SQLite snapshot")
    p_mr.add_argument("--date", help="Local day (YYYY-MM-DD); defaults to yesterday")
    p_mi = mod_sub.add_parser("info", help="What a module does, what data it needs, and its cautions")
    p_mi.add_argument("module_id")
    p_mi.add_argument("--db", help="Also check which of its data types exist in this database")

    p_wk = sub.add_parser("weekly", help="Weekly health trends: 7d vs 28d per metric + BP")
    p_wk.add_argument("--db", required=True, help="Path to the bridge SQLite snapshot")
    p_wk.add_argument("--date", help="Local day (YYYY-MM-DD); defaults to Chicago today")
    p_wk.add_argument("--config", help="Path to health_monitor.yaml")

    # labs: nested subcommand group for the clinical-records importer.
    p_labs = sub.add_parser("labs", help="Lab results importer/summary (clinical-records export)")
    labs_sub = p_labs.add_subparsers(dest="labs_command", required=True)

    p_imp = labs_sub.add_parser("import", help="Import a clinical-records export")
    p_imp.add_argument("zip_path", help="Path to export_YYYY-MM-DD.zip")
    p_imp.add_argument("--db", required=True, help="SQLite DB path")

    p_sum = labs_sub.add_parser("summary", help="Summary counts from a DB")
    p_sum.add_argument("--db", required=True, help="SQLite DB path")

    p_trd = labs_sub.add_parser("trend", help="Trend for one LOINC code")
    p_trd.add_argument("--db", required=True, help="SQLite DB path")
    p_trd.add_argument("--loinc", required=True, help="LOINC code")

    p_dig = labs_sub.add_parser("digest-line", help="New-results digest line for --date")
    p_dig.add_argument("--db", required=True, help="SQLite DB path")
    p_dig.add_argument("--date", help="Local day (YYYY-MM-DD)")

    p_json = labs_sub.add_parser("import-json", help="Import lab results from a FHIR JSON file")
    p_json.add_argument("json_path", help="Path to JSON file (list of Observations or FHIR Bundle)")
    p_json.add_argument("--db", required=True, help="SQLite DB path")

    p_labs_bridge = labs_sub.add_parser("import-bridge", help="Import lab results from the bridge DB")
    p_labs_bridge.add_argument("--bridge", required=True, help="Path to the bridge SQLite DB")
    p_labs_bridge.add_argument("--db", required=True, help="SQLite DB path")

    # ecg: Apple Watch ECG importer/summary/digest-line (electrocardiograms/ export members).
    p_ecg = sub.add_parser("ecg", help="Apple Watch ECG importer/summary (electrocardiograms export)")
    ecg_sub = p_ecg.add_subparsers(dest="ecg_command", required=True)

    p_imp = ecg_sub.add_parser("import", help="Import an electrocardiograms export")
    p_imp.add_argument("--zip", required=True, help="Path to export_YYYY-MM-DD.zip")
    p_imp.add_argument("--db", required=True, help="SQLite DB path")

    p_json = ecg_sub.add_parser("import-json", help="Import ECG recordings from JSON")
    p_json.add_argument("json_path", help="Path to JSON file")
    p_json.add_argument("--db", required=True, help="SQLite DB path")

    p_sum = ecg_sub.add_parser("summary", help="Summary counts from a DB")
    p_sum.add_argument("--db", required=True, help="SQLite DB path")

    p_dig = ecg_sub.add_parser("digest-line", help="New-results digest line for --date")
    p_dig.add_argument("--db", required=True, help="SQLite DB path")
    p_dig.add_argument("--date", help="Local day (YYYY-MM-DD)")

    p_bridge = ecg_sub.add_parser("import-bridge", help="Import ECG recordings from the bridge DB")
    p_bridge.add_argument("--bridge", required=True, help="Path to the bridge SQLite DB")
    p_bridge.add_argument("--db", required=True, help="SQLite DB path")

    # nutrition: food-log intake vs DRI
    p_nut = sub.add_parser("nutrition", help="Nutrition report: daily intake vs DRI")
    p_nut.add_argument("--db", required=True, help="Path to the merged history SQLite")
    p_nut.add_argument("--date", help="Local day (YYYY-MM-DD); defaults to Chicago today")
    p_nut.add_argument("--days", type=int, default=7, help="Window size in days")
    p_nut.add_argument("--dri", default=str(settings.package_data("dri.yaml")), help="Path to a DRI table (default: the shipped adult-male 31-50 table)")
    p_nut.add_argument("--labs", help="Path to labs.sqlite for cross-check")
    p_nut.add_argument("--json", action="store_true", help="Print JSON document")

    # concerns: health concern findings
    p_con = sub.add_parser("concerns", help="Health concern findings")
    p_con.add_argument("--db", required=True, help="Path to the bridge SQLite snapshot")
    p_con.add_argument("--date", help="Local day (YYYY-MM-DD); defaults to Chicago today")
    p_con.add_argument("--json", action="store_true", help="Print JSON list of findings")

    # workouts: weekly summary and daily digest lines
    p_wrt = sub.add_parser("workouts", help="Workout summary and digest lines")
    wrt_sub = p_wrt.add_subparsers(dest="workouts_command", required=True)

    p_wl = wrt_sub.add_parser("weekly-line", help="Weekly workout summary line for --date")
    p_wl.add_argument("--db", required=True, help="SQLite DB path")
    p_wl.add_argument("--date", required=True, help="Reference day (YYYY-MM-DD)")

    p_dl = wrt_sub.add_parser("digest-line", help="Digest line for --date")
    p_dl.add_argument("--db", required=True, help="SQLite DB path")
    p_dl.add_argument("--date", required=True, help="Day (YYYY-MM-DD)")

    return parser.parse_args(argv)


def _load_report(db_path, window, baseline):
    return analysis.build_report(db_path, window_days=window, baseline_days=baseline)


def cmd_coverage(args) -> int:
    report = _load_report(args.db_path, args.window, args.baseline)
    if args.json or (not args.md):
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
            print(f"Wrote JSON report to {args.out}")
        else:
            print(json.dumps(report, indent=2))
    if args.md:
        print()
        print(analysis.markdown_report(report))
    return 0


def cmd_daily(args) -> int:
    report = _load_report(args.db_path, args.window, args.baseline)
    metric = report["metrics"].get(args.type_code)
    if not metric:
        print(f"No data for type '{args.type_code}'", file=sys.stderr)
        return 1
    print(json.dumps(metric["daily"], indent=2))
    return 0


def cmd_baseline(args) -> int:
    report = _load_report(args.db_path, args.window, args.baseline)
    metric = report["metrics"].get(args.type_code)
    if not metric:
        print(f"No data for type '{args.type_code}'", file=sys.stderr)
        return 1
    print(json.dumps(metric["baseline"], indent=2))
    return 0


def _default_date() -> str:
    """Yesterday in America/Chicago (the digest/anomaly day)."""
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    return (datetime.now(settings.timezone()) - timedelta(days=1)).strftime("%Y-%m-%d")


def cmd_anomalies(args) -> int:
    """Print anomaly flags for one local day (counts/bounds only)."""
    import yaml
    from . import anomalies

    date = args.date or _default_date()
    if args.config:
        with open(args.config, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    else:
        config = {
            "timezone": settings.timezone_name(),
            "metrics": {
                "resting_heart_rate": {"baseline_window_days": 30, "anomaly": {"robust_sigma": 3.0, "min_abs": 45, "max_abs": 120}, "trend": {"slope_per_day_alert": 0.5}},
                "heart_rate": {"baseline_window_days": 30, "anomaly": {"robust_sigma": 3.0, "min_abs": 30, "max_abs": 220}, "trend": {"slope_per_day_alert": 0.5}},
                "heart_rate_variability_sdnn": {"baseline_window_days": 30, "anomaly": {"robust_sigma": 3.0, "min_abs": 5, "max_abs": 250}, "trend": {"slope_per_day_alert": 2.0}},
                "weight": {"baseline_window_days": 30, "anomaly": {"robust_sigma": 3.0, "min_abs": 30, "max_abs": 400, "max_change_over_days": 2, "window_days": 3}, "trend": {"slope_per_day_alert": 0.2}},
                "sleep_analysis": {"baseline_window_days": 30, "anomaly": {"robust_sigma": 3.0, "min_abs": 0, "max_abs": 24}, "trend": {"slope_per_day_alert": 1.0}},
            },
        }

    result = anomalies.detect(args.db_path, config, date)
    flags: list[str] = []
    insufficient: list[str] = []
    for tc in sorted(result):
        entry = result[tc]
        if entry["status"] == "flagged":
            flags.append(f"{tc}:{entry['reason']}")
        elif entry["status"] == "insufficient":
            insufficient.append(tc)

    for line in flags:
        print(line)
    if flags and insufficient:
        print()
    if insufficient:
        print(f"insufficient history: {', '.join(insufficient)}")
    if not flags and not insufficient:
        print("no flags")
    return 0


def cmd_bp(args) -> int:
    """Print BP latest/7d-avg/flags for one Chicago day."""
    from . import bp

    date = args.date or _default_date()
    for line in bp.report(args.db_path, date):
        print(line)
    return 0


def cmd_weekly(args) -> int:
    """Print the weekly health trends report (7d vs 28d per metric + BP)."""
    import yaml
    from . import weekly

    date = args.date or _default_date()
    if args.config:
        with open(args.config, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    else:
        # Default: load the shipped health_monitor.yaml from the package data.
        import os
        default_cfg = str(settings.package_data("health_monitor.yaml"))
        if os.path.exists(default_cfg):
            with open(default_cfg, encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        else:
            config = {"timezone": settings.timezone_name(), "metrics": {}}
    for line in weekly.report(args.db, config, date):
        print(line)
    return 0


# ---- labs: clinical-records importer --------------------------------------

def _labs_dispatch(args) -> int:
    """Route `labs <subcommand>` to the labs module handlers."""
    from . import labs

    sub = getattr(args, "labs_command")
    if sub == "import":
        return labs.cmd_import(args.zip_path, args.db)
    if sub == "summary":
        return labs.cmd_summary(args.db)
    if sub == "trend":
        return labs.cmd_trend(args.db, args.loinc)
    if sub == "digest-line":
        return labs.cmd_digest_line(args.db, args.date)
    if sub == "import-json":
        return labs.cmd_import_json(args.json_path, args.db)
    if sub == "import-bridge":
        return labs.cmd_import_bridge(args.bridge, args.db)
    return 1


def _ecg_dispatch(args) -> int:
    """Route `ecg <subcommand>` to the ecg module handlers."""
    from . import ecg

    sub = getattr(args, "ecg_command")
    if sub == "import":
        return ecg.cmd_import(args.zip, args.db)
    if sub == "import-json":
        return ecg.cmd_import_json(args.json_path, args.db)
    if sub == "summary":
        return ecg.cmd_summary(args.db)
    if sub == "digest-line":
        return ecg.cmd_digest_line(args.db, args.date)
    if sub == "import-bridge":
        return ecg.cmd_import_bridge(args.bridge, args.db)
    return 1


def _nutrition_dispatch(args) -> int:
    """Route `nutrition` to the nutrition module."""
    from . import nutrition

    date = args.date or _default_date()
    if args.json:
        j = nutrition.report_json(
            args.db, args.dri, date, days=args.days, labs_db=args.labs
        )
        print(json.dumps(j, indent=2))
    else:
        for line in nutrition.report(
            args.db, args.dri, date, days=args.days, labs_db=args.labs
        ):
            print(line)
    return 0


def _workouts_dispatch(args) -> int:
    """Route `workouts <subcommand>` to the workouts module."""
    from . import workouts

    sub = getattr(args, "workouts_command")
    if sub == "weekly-line":
        line = workouts.weekly_line(args.db, args.date)
        if line:
            print(line)
        return 0
    if sub == "digest-line":
        line = workouts.digest_line(args.db, args.date)
        if line:
            print(line)
        return 0
    return 1


def _modules_dispatch(args) -> int:
    from . import modules

    cmd = args.modules_command
    if cmd == "list":
        rows = [{"id": m.id, "title": m.title, "category": m.category, "summary": m.summary,
                 "enabled": modules.is_enabled(m.id)} for m in sorted(modules.REGISTRY.values(), key=lambda m: m.id)]
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
            return 0
        width = max((len(r["id"]) for r in rows), default=2)
        for r in rows:
            print(f"[{'on ' if r['enabled'] else 'off'}] {r['id']:<{width}}  {r['title']}: {r['summary']}")
        print(f"\nTurn one on:  health-insights modules enable <id>     Details: health-insights modules info <id>")
        print(f"Switches are stored in {settings.modules_file()}")
        return 0
    try:
        module = modules.get(args.module_id)
    except KeyError as exc:
        print(exc.args[0], file=sys.stderr)
        return 2
    if cmd in ("enable", "disable"):
        path = modules.set_enabled(module.id, cmd == "enable")
        print(f"{module.title} is now {'ON' if cmd == 'enable' else 'OFF'} (saved in {path}).")
        if cmd == "enable":
            for caution in module.cautions:
                print(f"  Note: {caution}")
        return 0
    if cmd == "report":
        if module.report is None:
            print(f"{module.title} has no report; it only adds checks to `concerns`.", file=sys.stderr)
            return 2
        if not modules.is_enabled(module.id):
            print(f"{module.title} is off. Turn it on first: health-insights modules enable {module.id}", file=sys.stderr)
            return 2
        print("\n".join(module.report(args.db, args.date or _default_date())))
        return 0
    print(f"{module.title} ({module.id}) - {'ON' if modules.is_enabled(module.id) else 'off'}")
    print(f"  {module.summary}")
    print(f"  Category: {module.category}")
    if module.data_needs:
        print(f"  Works best with: {', '.join(module.data_needs)}")
    if module.skills:
        print(f"  Agent skills: {', '.join(module.skills)}")
    for caution in module.cautions:
        print(f"  Caution: {caution}")
    if module.red_flags:
        print("  Act on these whatever the data says (urgent or emergency care):")
        for flag in module.red_flags:
            print(f"    - {flag}")
    if getattr(args, "db", None):
        info = modules.readiness(module.id, args.db)
        print(f"  In {args.db}: have {', '.join(info['present']) or 'none'}; missing {', '.join(info['missing']) or 'none'}")
    return 0


def _concerns_dispatch(args) -> int:
    """Route `concerns` to the concerns module."""
    from . import concerns

    date = args.date or _default_date()
    results = concerns.evaluate(args.db, date)

    if args.json:
        print(json.dumps([f.to_dict() for f in results], indent=2, ensure_ascii=False))
    else:
        if not results:
            print("✅ No concerns found")
        else:
            for f in results:
                icon = concerns.LEVEL_ICONS.get(f.level, "?")
                print(f"{icon} {f.title} — {f.evidence}")
    return 0


def main(argv=None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "demo":
        from . import demo
        demo.build(args.out, days=args.days)
        print(f"Wrote synthetic demo database to {args.out}. Try: health-insights weekly --db {args.out}")
        return 0
    if args.command == "modules":
        return _modules_dispatch(args)
    if args.command == "coverage":
        return cmd_coverage(args)
    if args.command == "daily":
        return cmd_daily(args)
    if args.command == "baseline":
        return cmd_baseline(args)
    if args.command == "anomalies":
        return cmd_anomalies(args)
    if args.command == "bp":
        return cmd_bp(args)
    if args.command == "weekly":
        return cmd_weekly(args)
    if args.command == "labs":
        return _labs_dispatch(args)
    if args.command == "ecg":
        return _ecg_dispatch(args)
    if args.command == "nutrition":
        return _nutrition_dispatch(args)
    if args.command == "workouts":
        return _workouts_dispatch(args)
    if args.command == "concerns":
        return _concerns_dispatch(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
