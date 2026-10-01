"""NA-03: unknown is not zero; supplement-only and zero-energy days are valid logged days.

Synthetic fixtures only. A day is LOGGED when it has any dietary_* sample; a nutrient with no row on a
logged day is UNKNOWN for that day (never summed as zero); partial days stay partial; the number of
adequately logged days is explicit; report() and report_json() agree.
"""
import sqlite3
from datetime import date as Date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from health_insights import nutrition
from tests.test_nutrition import DRI_SMALL, _line

CHI = ZoneInfo("America/Chicago")
REF = Date(2030, 6, 30)


def _db(tmp_path, days):
    """days: {days_ago: {type_code: value}} -> one sample per entry at noon Chicago."""
    path = str(tmp_path / "db.sqlite")
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
    n = 0
    for ago, vals in days.items():
        d = REF - timedelta(days=ago)
        ts = datetime.fromisoformat(f"{d.isoformat()}T12:00:00").replace(tzinfo=CHI).astimezone(
            timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for tc, v in vals.items():
            n += 1
            con.execute(
                "INSERT INTO samples (source_id, type_code, client_record_id, start_time, end_time, value, unit) "
                "VALUES ('t',?,?,?,?,?,'u')", (tc, f"r{n}", ts, ts, v))
    con.commit()
    con.close()
    return path


FULL = {"dietary_energy_consumed": 2100.0, "dietary_fiber": 20.0, "dietary_potassium": 100.0}


def _both(db):
    return (nutrition.report(db, DRI_SMALL, REF.isoformat(), days=7),
            nutrition.report_json(db, DRI_SMALL, REF.isoformat(), days=7))


def test_missing_nutrient_is_unknown_not_a_zero_in_the_average(tmp_path):
    # fiber logged on 4 of 6 days (20 g each); the other 2 logged days have no fiber row
    days = {i: dict(FULL) for i in range(4)}
    days.update({4: {"dietary_energy_consumed": 2000.0}, 5: {"dietary_energy_consumed": 2000.0}})
    lines, j = _both(_db(tmp_path, days))
    assert j["days_logged"] == 6
    fib = j["nutrients"]["fiber"]
    assert fib["avg"] == 20.0, fib          # not 20*4/6 = 13.33
    assert fib["days_known"] == 4
    assert "avg 20 g" in _line("\n".join(lines), "Fiber")


def test_nutrient_never_recorded_is_unknown_in_json_and_absent_in_text(tmp_path):
    days = {i: dict(FULL) for i in range(5)}
    lines, j = _both(_db(tmp_path, days))
    ca = j["nutrients"]["calcium"]
    assert ca["avg"] is None and ca["pct"] is None and ca["status"] == "unknown" and ca["days_known"] == 0
    assert _line("\n".join(lines), "Calcium") == ""


def test_known_zero_is_zero_not_unknown(tmp_path):
    days = {i: {**FULL, "dietary_sodium": 0.0} for i in range(5)}
    lines, j = _both(_db(tmp_path, days))
    na = j["nutrients"]["sodium"]
    assert na["avg"] == 0 and na["days_known"] == 5 and na["status"] == "ok"
    assert "avg 0 mg" in _line("\n".join(lines), "Sodium")   # text/JSON parity


def test_supplement_only_day_is_a_logged_day_and_partial(tmp_path):
    days = {i: dict(FULL) for i in range(4)}
    days[4] = {"dietary_magnesium": 200.0}   # no energy row at all
    lines, j = _both(_db(tmp_path, days))
    assert j["days_logged"] == 5
    assert j["days_partial"] == 1 and j["days_adequate"] == 4
    assert "5 of 7 days logged" in lines[0] and "1 partial" in lines[0]
    assert j["nutrients"]["magnesium"]["days_known"] == 1
    # energy averages over the days that have an energy value, not over the supplement-only day
    assert j["energy"]["avg_kcal"] == 2100.0 and j["energy"]["days_known"] == 4
    assert "avg 2100 kcal" in _line("\n".join(lines), "Energy")


def test_zero_energy_day_is_logged_and_counts_as_zero_energy(tmp_path):
    days = {i: dict(FULL) for i in range(4)}
    days[4] = {"dietary_energy_consumed": 0.0, "dietary_fiber": 5.0}
    lines, j = _both(_db(tmp_path, days))
    assert j["days_logged"] == 5
    assert j["days_partial"] == 1 and j["days_adequate"] == 4
    assert j["energy"]["days_known"] == 5
    assert abs(j["energy"]["avg_kcal"] - 8400.0 / 5) < 1e-6


def test_logged_days_gate_counts_supplement_only_days(tmp_path):
    days = {i: dict(FULL) for i in range(2)}
    days.update({2: {"dietary_magnesium": 1.0}, 3: {"dietary_energy_consumed": 0.0}})
    lines, j = _both(_db(tmp_path, days))
    assert j["days_logged"] == 4
    assert "not enough" not in "\n".join(lines).lower()


def test_all_days_without_energy_gives_unknown_energy_not_zero(tmp_path):
    days = {i: {"dietary_magnesium": 100.0} for i in range(5)}
    lines, j = _both(_db(tmp_path, days))
    assert j["days_logged"] == 5 and j["days_adequate"] == 0
    assert j["energy"]["avg_kcal"] is None and j["energy"]["days_known"] == 0
    assert "unknown" in _line("\n".join(lines), "Energy").lower()


def test_text_and_json_parity_on_days_and_values(tmp_path):
    days = {i: dict(FULL) for i in range(4)}
    days[4] = {"dietary_magnesium": 200.0}
    days[5] = {"dietary_energy_consumed": 800.0, "dietary_fiber": 10.0}
    lines, j = _both(_db(tmp_path, days))
    assert f"{j['days_logged']} of 7 days logged" in lines[0]
    assert f"{j['days_partial']} partial" in lines[0]
    for key, info in j["nutrients"].items():
        label = nutrition._load_dri(DRI_SMALL)["nutrients"][key]["label"]
        text = _line("\n".join(lines), label + ":")
        if info["avg"] is None:
            assert text == "", (key, text)
    fib = _line("\n".join(lines), "Fiber")
    assert f"avg {j['nutrients']['fiber']['avg']:.0f} g" in fib
    assert "known on 5 of 6" in fib   # coverage shown in text
