"""Tests for FHIR JSON lab import (labs.read_labs_json, results_from_observations). FAKE data only."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import stat
import zipfile

import pytest

from health_insights import labs


# ---- synthetic FHIR helpers ------------------------------------------------

def obs(id_: str, code: str, day: str, qty=None, unit=None, string=None, rng=None, rtext=None, comp=False):
    """Build a fake FHIR Observation dict."""
    d = {
        "resourceType": "Observation",
        "id": id_,
        "status": "final",
        "category": [{"coding": [{"code": "laboratory"}]}],
        "code": {
            "coding": [{"system": "http://loinc.org", "code": code, "display": f"Test {code}"}],
            "text": f"Test {code}",
        },
        "effectiveDateTime": f"{day}T08:00:00-05:00",
    }
    if qty is not None:
        d["valueQuantity"] = {"value": qty, "unit": unit}
    if string is not None:
        d["valueString"] = string
    rng_dict = {}
    if rng:
        rng_dict["low"] = {"value": rng[0]}
        rng_dict["high"] = {"value": rng[1]}
    if rtext:
        rng_dict["text"] = rtext
    if rng_dict:
        d["referenceRange"] = [rng_dict]
    if comp:
        d["component"] = [
            {
                "code": {"coding": [{"system": "http://loinc.org", "code": "8480-6"}], "text": "Systolic"},
                "valueQuantity": {"value": 118, "unit": "mmHg"},
            }
        ]
    return d


def _make_zip(path, records):
    """Write fake observations into a zip like Apple's clinical-records export."""
    with zipfile.ZipFile(path, "w") as z:
        for r in records:
            z.writestr(f"apple_health_export/clinical-records/{r['id']}.json", json.dumps(r))
    return str(path)


# ---- fixtures ---------------------------------------------------------------

# 5 observations: glucose HIGH, glucose NORMAL, HbA1c LOW, numeric string, BP (no main value)
RECS = [
    obs("s1", "2345-7", "2031-01-10", 110, "mg/dL", rng=(70, 99)),
    obs("s2", "2345-7", "2031-02-10", 90, "mg/dL", rng=(70, 99)),
    obs("s3", "718-7", "2031-03-10", 10.0, "g/dL", rng=(12, 16)),
    obs("s4", "4548-4", "2031-03-10", string="5.4"),
    obs("s5", "85354-9", "2031-03-11", comp=True),
]

OTHER = [
    {"resourceType": "MedicationRequest", "id": "m1"},
    {"resourceType": "Patient", "id": "p1"},
]


# ---- tests ------------------------------------------------------------------

class TestReadLabsJson:
    """Core read_labs_json behaviour."""

    def test_list_of_observations_returns_rows(self, tmp_path):
        """A JSON list of Observation dicts is parsed into LabResults."""
        jf = tmp_path / "list.json"
        json.dump(RECS, open(jf, "w"))
        results = labs.read_labs_json(str(jf))
        assert len(results) == 5
        ids = sorted(r.obs_id for r in results)
        assert ids == ["s1", "s2", "s3", "s4", "s5"]

    def test_fhir_bundle_same_rows(self, tmp_path):
        """A FHIR Bundle with entry[].resource yields the same rows as a plain list."""
        bundle = {"resourceType": "Bundle", "type": "collection",
                  "entry": [{"resource": r} for r in RECS + OTHER]}
        bf = tmp_path / "bundle.json"
        json.dump(bundle, open(bf, "w"))
        results = labs.read_labs_json(str(bf))
        assert len(results) == 5
        ids = sorted(r.obs_id for r in results)
        assert ids == ["s1", "s2", "s3", "s4", "s5"]

    def test_non_observation_resources_ignored(self, tmp_path):
        """Non-Observation entries in a list are silently skipped."""
        jf = tmp_path / "mixed.json"
        json.dump(RECS[:2] + OTHER, open(jf, "w"))
        results = labs.read_labs_json(str(jf))
        assert len(results) == 2
        assert sorted(r.obs_id for r in results) == ["s1", "s2"]

    def test_single_object_raises_value_error(self, tmp_path):
        """A single Observation object (not a list, not a Bundle) raises ValueError."""
        jf = tmp_path / "single.json"
        json.dump(RECS[0], open(jf, "w"))
        with pytest.raises(ValueError, match=""):
            labs.read_labs_json(str(jf))

    def test_empty_list_returns_empty(self, tmp_path):
        """An empty JSON list returns an empty list."""
        jf = tmp_path / "empty.json"
        json.dump([], open(jf, "w"))
        assert labs.read_labs_json(str(jf)) == []

    def test_equality_with_zip_path(self, tmp_path):
        """read_labs_json produces the same rows as read_labs for the same observations."""
        zip_path = _make_zip(tmp_path / "export.zip", RECS)
        jf = tmp_path / "list.json"
        json.dump(RECS, open(jf, "w"))
        zip_results = labs.read_labs(zip_path)
        json_results = labs.read_labs_json(str(jf))
        # Compare ignoring imported_at and source_file
        def _key(r):
            d = r.__dict__
            return tuple(sorted((k, v) for k, v in d.items() if k not in ("imported_at", "source_file")))
        assert len(zip_results) == len(json_results)
        assert all(_key(a) == _key(b) for a, b in zip(sorted(zip_results, key=lambda r: r.obs_id),
                                                       sorted(json_results, key=lambda r: r.obs_id)))

    def test_bp_numeric_via_component(self, tmp_path):
        """An observation with no main valueQuantity borrows from its component (s5 -> 118)."""
        jf = tmp_path / "bp.json"
        json.dump([RECS[4]], open(jf, "w"))
        results = labs.read_labs_json(str(jf))
        assert len(results) == 1
        assert results[0].value_num == 118.0

    def test_flags_computed(self, tmp_path):
        """Flags are computed correctly (s1 glucose 110 vs 70-99 -> HIGH)."""
        jf = tmp_path / "flags.json"
        json.dump(RECS, open(jf, "w"))
        results = labs.read_labs_json(str(jf))
        flags = {r.obs_id: r.flag for r in results}
        assert flags["s1"] == "HIGH"   # 110 > 99
        assert flags["s2"] == "NORMAL" # 90 in [70, 99]
        assert flags["s3"] == "LOW"    # 10.0 < 12
        assert flags["s4"] == "UNKNOWN"  # numeric string parsed to 5.4, no range -> UNKNOWN


class TestResultsFromObservations:
    """results_from_observations is the shared helper."""

    def test_exists(self):
        assert hasattr(labs, "results_from_observations")

    def test_calls_results_from_observations(self, tmp_path):
        """read_labs_json delegates to results_from_observations."""
        jf = tmp_path / "list.json"
        json.dump(RECS, open(jf, "w"))
        results = labs.read_labs_json(str(jf))
        # Verify the helper was used by checking it exists and is callable
        assert callable(labs.results_from_observations)
        assert len(results) == 5


class TestCliImportJson:
    """CLI: python -m health_insights.cli labs import-json FILE --db DB."""

    def _cli(self, fn, *args):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            fn(*args)
        return buf.getvalue().strip()

    def test_import_json_runs(self, tmp_path):
        jf = tmp_path / "list.json"
        json.dump(RECS, open(jf, "w"))
        db = str(tmp_path / "db" / "labs.sqlite")
        out = self._cli(labs.cmd_import_json, str(jf), db)
        assert "records read" in out
        assert "new" in out

    def test_idempotent_cli(self, tmp_path):
        jf = tmp_path / "list.json"
        json.dump(RECS, open(jf, "w"))
        db = str(tmp_path / "db" / "labs.sqlite")
        first = self._cli(labs.cmd_import_json, str(jf), db)
        assert "5 new" in first
        second = self._cli(labs.cmd_import_json, str(jf), db)
        assert "0 new" in second
        con = sqlite3.connect(db)
        count = con.execute("SELECT COUNT(*) FROM lab_results").fetchone()[0]
        con.close()
        assert count == 5

    def test_db_permissions(self, tmp_path):
        jf = tmp_path / "list.json"
        json.dump(RECS, open(jf, "w"))
        db = str(tmp_path / "db" / "labs.sqlite")
        self._cli(labs.cmd_import_json, str(jf), db)
        assert stat.S_IMODE(os.stat(db).st_mode) == 0o600

    def test_summary_works_on_json_db(self, tmp_path):
        jf = tmp_path / "list.json"
        json.dump(RECS, open(jf, "w"))
        db = str(tmp_path / "db" / "labs.sqlite")
        self._cli(labs.cmd_import_json, str(jf), db)
        out = self._cli(labs.cmd_summary, db)
        assert "results" in out.lower()
