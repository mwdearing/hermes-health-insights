"""Tests for the weekly health trends report (health_insights/weekly.py).

Fake data only -- no real health values cross into this suite. Each test builds a
tiny SQLite DB in tmp_path and calls health_insights.weekly.report() directly,
asserting on the rendered lines.
"""
import re
import sqlite3
from datetime import date as Date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from health_insights import weekly

CHI = ZoneInfo("America/Chicago")
REF = "2030-06-30"  # no DST change inside the 35-day window
CONFIG = {
    "timezone": "America/Chicago",
    "metrics": {
        "sleep_analysis": {"aggregate": "sleep_hours"},   # as in config/health_monitor.yaml: sleep comes from sleep_sessions
        "resting_heart_rate": {},
        "heart_rate_variability_sdnn": {},
        "weight": {},
        "oxygen_saturation": {},
        "heart_rate": {},
    },
}


def _build_db(path, *, sleep=True, samples=None):
    """Build a minimal DB at ``path`` with the given samples and optional sleep."""
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE samples (sample_id INTEGER PRIMARY KEY, source_id TEXT, "
        "type_code TEXT, client_record_id TEXT, start_time TEXT, end_time TEXT, "
        "value REAL, unit TEXT, metadata_json TEXT)"
    )
    con.execute(
        "CREATE TABLE sleep_sessions (sleep_session_id INTEGER PRIMARY KEY, "
        "source_id INTEGER, client_record_id TEXT, start_time TEXT, end_time TEXT)"
    )
    if samples:
        for tc, d, v, unit in samples:
            ts = f"{d}T09:00:00Z"
            con.execute(
                "INSERT INTO samples (source_id, type_code, client_record_id, "
                "start_time, end_time, value, unit) VALUES ('t',?,?,?,?,?,?)",
                (tc, f"r{len(tc)}", ts, ts, v, unit),
            )
    if sleep:
        # Baseline: 28 days of alternating 7.25 / 6.75 h; recent: 7.0 h.
        ref = datetime.fromisoformat(REF)
        for ago in range(35):
            d = (ref - timedelta(days=ago)).strftime("%Y-%m-%d")
            j = 1 if ago % 2 else -1
            hours = 7.0 if ago <= 6 else 7.0 + 0.25 * j
            start = datetime.combine(
                datetime.fromisoformat(d) - timedelta(days=1),
                datetime.min.time(),
            ).replace(hour=23, tzinfo=CHI)
            end = start + timedelta(hours=hours)
            con.execute(
                "INSERT INTO sleep_sessions (source_id, client_record_id, "
                "start_time, end_time) VALUES (1,?,?,?)",
                (f"s{ago}",
                 start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
            )
    con.commit()
    con.close()
    return str(path)


def _line(text, label):
    for l in text.splitlines():
        if l.startswith(label):
            return l
    return ""


def _full_db(path):
    """All metrics populated like the acceptance fixture."""
    ref = datetime.fromisoformat(REF)
    samples = []
    for ago in range(35):
        d = (ref - timedelta(days=ago)).strftime("%Y-%m-%d")
        recent = ago <= 6
        j = 1 if ago % 2 else -1
        if recent:
            samples.append(("resting_heart_rate", d, 65.0, "bpm"))
            samples.append(("heart_rate_variability_sdnn", d, 40.0, "ms"))
            samples.append(("weight", d, 80.1, "kg"))
            samples.append(("oxygen_saturation", d, 0.96, "%"))
            samples.append(("blood_pressure_systolic", d, 150, "mmHg"))
            samples.append(("blood_pressure_diastolic", d, 95, "mmHg"))
        else:
            samples.append(("resting_heart_rate", d, 60.0 + j, "bpm"))
            samples.append(("heart_rate_variability_sdnn", d, 50.0 + 2 * j, "ms"))
            samples.append(("weight", d, 80.0 + 0.2 * j, "kg"))
            samples.append(("oxygen_saturation", d, 0.97, "%"))
            samples.append(("blood_pressure_systolic", d, 125, "mmHg"))
            samples.append(("blood_pressure_diastolic", d, 80, "mmHg"))
        if ago in (1, 3):
            samples.append(("heart_rate", d, 70.0, "bpm"))
        elif not recent:
            samples.append(("heart_rate", d, 70.0 + j, "bpm"))
    _build_db(path, samples=samples)
    return str(path)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_header(tmp_path):
    """The report starts with the correct weekly header."""
    db = _full_db(tmp_path / "db.sqlite")
    out = weekly.report(db, CONFIG, REF)
    ref = Date.fromisoformat(REF)
    start = ref - timedelta(days=6)
    assert out[0] == f"Weekly health {start:%Y-%m-%d} to {ref:%Y-%m-%d} (Chicago)"


def test_resting_hr_up(tmp_path):
    """Resting HR recent mean is clearly above the baseline -> up, +5."""
    db = _full_db(tmp_path / "db.sqlite")
    line = _line("\n".join(weekly.report(db, CONFIG, REF)), "Resting HR")
    assert re.search(r"7d 65 bpm vs 28d 60", line)
    assert "+5" in line
    assert "up" in line
    assert "7 of 7 days" in line


def test_hrv_down(tmp_path):
    """HRV recent mean is clearly below the baseline -> down, -10."""
    db = _full_db(tmp_path / "db.sqlite")
    line = _line("\n".join(weekly.report(db, CONFIG, REF)), "HRV")
    assert re.search(r"7d 40 ms vs 28d 50", line)
    assert "-10" in line
    assert "down" in line


def test_weight_flat(tmp_path):
    """A tight baseline is called flat; weight is shown in lb only."""
    db = _full_db(tmp_path / "db.sqlite")
    line = _line("\n".join(weekly.report(db, CONFIG, REF)), "Weight")
    assert "lb" in line and "kg" not in line
    assert "flat" in line


def test_spo2_percent(tmp_path):
    """SpO2 stored as a fraction is rendered as a percent (96 vs 97)."""
    db = _full_db(tmp_path / "db.sqlite")
    line = _line("\n".join(weekly.report(db, CONFIG, REF)), "SpO2")
    assert re.search(r"7d 96 % vs 28d 97", line)


def test_insufficient_two_of_seven(tmp_path):
    """A metric with only 2 recent days reports 'insufficient (2 of 7 days)'."""
    db = _full_db(tmp_path / "db.sqlite")
    line = _line("\n".join(weekly.report(db, CONFIG, REF)), "Heart rate")
    assert "insufficient" in line
    assert "2 of 7 days" in line


def test_empty_db(tmp_path):
    """A database with no rows prints a 'no data' line and exits 0."""
    con = sqlite3.connect(str(tmp_path / "empty.sqlite"))
    con.execute(
        "CREATE TABLE samples (sample_id INTEGER PRIMARY KEY, source_id TEXT, "
        "type_code TEXT, client_record_id TEXT, start_time TEXT, end_time TEXT, "
        "value REAL, unit TEXT, metadata_json TEXT)"
    )
    con.execute(
        "CREATE TABLE sleep_sessions (sleep_session_id INTEGER PRIMARY KEY, "
        "source_id INTEGER, client_record_id TEXT, start_time TEXT, end_time TEXT)"
    )
    con.commit()
    con.close()
    out = weekly.report(str(tmp_path / "empty.sqlite"), CONFIG, REF)
    assert "no data" in "\n".join(out).lower()


def test_bp_line(tmp_path):
    """BP compares paired systolic/diastolic readings 150/95 vs 125/80."""
    db = _full_db(tmp_path / "db.sqlite")
    line = _line("\n".join(weekly.report(db, CONFIG, REF)), "BP")
    assert re.search(r"7d 150/95 mmHg vs 28d 125/80", line)


def test_notable_names_up_down(tmp_path):
    """Notable names the up/down metrics and not the flat weight."""
    db = _full_db(tmp_path / "db.sqlite")
    line = _line("\n".join(weekly.report(db, CONFIG, REF)), "Notable")
    assert "Resting HR up" in line


# --------------------------------------------------------------------------- #
# build_card_tiles() -- the trend-card data (2026-09-28)
# --------------------------------------------------------------------------- #

def test_card_tiles_puts_bp_first(tmp_path):
    db = _full_db(tmp_path / "db.sqlite")
    tiles = weekly.build_card_tiles(db, CONFIG, REF)
    assert tiles[0]["label"] == "Blood pressure"
    assert tiles[0]["latest"] == "150/95"


def test_card_tiles_match_the_text_reports_numbers(tmp_path):
    """The tile's latest/note must agree with report()'s own 7d-vs-28d text -- two code paths
    computing the same thing must never silently disagree."""
    db = _full_db(tmp_path / "db.sqlite")
    tiles = weekly.build_card_tiles(db, CONFIG, REF)
    rhr = next(t for t in tiles if t["label"] == "Resting HR")
    assert rhr["latest"] == "65 bpm"
    assert "up" in rhr["note"]
    weight = next(t for t in tiles if t["label"] == "Weight")
    assert "flat" in weight["note"]


def test_card_tiles_carry_a_real_sparkline_with_gaps_as_none(tmp_path):
    db = _full_db(tmp_path / "db.sqlite")
    tiles = weekly.build_card_tiles(db, CONFIG, REF)
    rhr = next(t for t in tiles if t["label"] == "Resting HR")
    assert len(rhr["values"]) == weekly.CARD_WINDOW_DAYS
    assert rhr["values"][-1] is not None  # ref date itself has data in the fixture
    hr = next((t for t in tiles if t["label"] == "Heart rate"), None)
    if hr is not None:
        assert None in hr["values"]  # the fixture only populates heart_rate on ago in (1, 3) among recent days


def test_card_tiles_never_borrow_the_concern_level_color(tmp_path):
    """Every tile uses the neutral trend color, never a level-derived one -- up isn't bad."""
    db = _full_db(tmp_path / "db.sqlite")
    tiles = weekly.build_card_tiles(db, CONFIG, REF)
    assert tiles and all(t["color"] == weekly.TREND_TILE_COLOR for t in tiles)
    assert all("level" not in t for t in tiles)


def test_card_tiles_capped_at_six(tmp_path):
    db = _full_db(tmp_path / "db.sqlite")
    tiles = weekly.build_card_tiles(db, CONFIG, REF)
    assert len(tiles) <= 6  # BP + up to 5 of the 6 METRIC_LABELS metrics, per render_card's own cap


def test_card_tiles_empty_for_an_empty_database(tmp_path):
    con = __import__("sqlite3").connect(tmp_path / "empty.sqlite")
    con.execute("CREATE TABLE samples (sample_id INTEGER PRIMARY KEY, source_id TEXT, type_code TEXT, client_record_id TEXT, start_time TEXT, end_time TEXT, value REAL, unit TEXT, metadata_json TEXT)")
    con.commit()
    con.close()
    assert weekly.build_card_tiles(str(tmp_path / "empty.sqlite"), CONFIG, REF) == []


def test_card_tiles_skip_spo2_when_the_aggregate_is_physiologically_impossible(tmp_path):
    """Regression for a real bug found 2026-09-28: mixed fraction/percent SpO2 samples in the
    same table produced a recent-mean aggregate of 5552.8% on real data. The tile must be
    omitted rather than showing an impossible number, even though report()'s text line still has
    this pre-existing issue (out of scope here; the card gets the guard first)."""
    ref = Date.fromisoformat(REF)
    samples = []
    for ago in range(7):
        d = (ref - timedelta(days=ago)).strftime("%Y-%m-%d")
        # Mixed units on purpose: alternating 0-1 fraction and 0-100 percent rows, same as the
        # real contaminated data -- averaging them directly is what produces the impossible number.
        samples.append(("oxygen_saturation", d, 0.95 if ago % 2 else 98.0, "%"))
    db = _build_db(tmp_path / "db.sqlite", sleep=False, samples=samples)
    tiles = weekly.build_card_tiles(db, CONFIG, REF)
    assert not any(t["label"] == "SpO2" for t in tiles)


def test_card_tiles_keep_a_plausible_spo2_aggregate(tmp_path):
    db = _full_db(tmp_path / "db.sqlite")  # _full_db's SpO2 samples are clean 0-1 fractions
    tiles = weekly.build_card_tiles(db, CONFIG, REF)
    spo2 = next(t for t in tiles if t["label"] == "SpO2")
    assert spo2["latest"] == "96%"


def test_card_tiles_skip_a_metric_with_insufficient_recent_days(tmp_path):
    """Same MIN_RECENT_DAYS gate report() uses for its 'insufficient' text line -- a tile with
    too little data to compare is omitted rather than showing a misleading number."""
    db = _build_db(tmp_path / "db.sqlite", sleep=False, samples=[("resting_heart_rate", REF, 65.0, "bpm")])
    tiles = weekly.build_card_tiles(db, CONFIG, REF)
    assert not any(t["label"] == "Resting HR" for t in tiles)


def test_medical_disclaimer(tmp_path):
    """The report ends with the informational disclaimer."""
    db = _full_db(tmp_path / "db.sqlite")
    text = "\n".join(weekly.report(db, CONFIG, REF))
    assert "not medical advice" in text.lower()


def test_baseline_median_7_hours(tmp_path):
    """Sleep baseline uses wake-up dates and is 7.0 h."""
    db = _build_db(tmp_path / "db.sqlite", sleep=True)
    out = "\n".join(weekly.report(db, CONFIG, REF))
    assert re.search(r"7d 7 h vs 28d 7 h", out)


def test_direction_zero_change_is_flat(tmp_path):
    """Identical recent and baseline values are called flat."""
    ref = datetime.fromisoformat(REF)
    db = _build_db(
        tmp_path / "db.sqlite",
        samples=[("resting_heart_rate", d, 60.0, "bpm")
                 for d in [
                     (ref - timedelta(days=ago)).strftime("%Y-%m-%d")
                     for ago in range(35)]],
    )
    line = _line("\n".join(weekly.report(db, CONFIG, REF)), "Resting HR")
    assert "flat" in line


def test_weight_text_is_pounds_only(tmp_path):
    db = _full_db(tmp_path / "db.sqlite")
    line = _line("\n".join(weekly.report(db, CONFIG, REF)), "Weight")
    assert "lb" in line and "kg" not in line
    assert "176.6 lb" in line  # 80.1 kg recent mean


def test_weight_tile_is_pounds(tmp_path):
    db = _full_db(tmp_path / "db.sqlite")
    tiles = weekly.build_card_tiles(db, CONFIG, REF)
    weight = next(t for t in tiles if t["label"] == "Weight")
    assert weight["latest"] == "176.6 lb"
    assert weight.get("unit") in ("lb", None, "")


def test_implausible_weight_sample_does_not_drag_the_daily_mean(tmp_path):
    ref = datetime.fromisoformat(REF)
    d = ref.strftime("%Y-%m-%d")
    samples = [("weight", d, 95.0, "kg"), ("weight", d, 96.0, "kg"), ("weight", d, 17.4, "kg")]
    path = tmp_path / "db.sqlite"
    _build_db(path, samples=samples)
    cfg = {"timezone": "America/Chicago",
           "metrics": {"weight": {"anomaly": {"min_abs": 30, "max_abs": 400}}}}
    vals = weekly._daily_values(str(path), cfg, "weight", CHI)
    assert vals[d] == pytest.approx(95.5)
