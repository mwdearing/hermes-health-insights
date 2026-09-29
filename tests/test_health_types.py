"""Tests for health_insights.health_types."""

import json
import os
import sys

import pytest

FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "bridge_type_catalog.json"
)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from health_insights.health_types import (
    TYPE_MAP,
    TYPE_META,
    NON_HK_TYPES,
    describe_type,
    healthkit_identifier,
    metric_name,
    unmapped_types,
)


def _load_fixture():
    with open(FIXTURE_PATH) as f:
        return json.load(f)


# -- fixture coverage --------------------------------------------------------

def test_every_fixture_type_in_type_map():
    """Every type_code in the fixture appears in TYPE_MAP."""
    catalog = _load_fixture()
    for item in catalog["types"]:
        assert item["type_code"] in TYPE_MAP


def test_every_requested_type_in_type_map():
    """sleep_analysis and workout (in requested_read_types but not in types) are mapped."""
    assert "sleep_analysis" in TYPE_MAP
    assert "workout" in TYPE_MAP


def test_identifiers_match_fixture():
    """HealthKit identifiers equal the fixture's healthkit_identifier for every type."""
    catalog = _load_fixture()
    for item in catalog["types"]:
        tc = item["type_code"]
        assert TYPE_MAP[tc][1] == item.get("healthkit_identifier"), tc


def test_type_meta_equals_fixture():
    """TYPE_META values match unit, aggregation, category from the fixture."""
    catalog = _load_fixture()
    for item in catalog["types"]:
        tc = item["type_code"]
        assert tc in TYPE_META
        assert TYPE_META[tc]["unit"] == item["unit"]
        assert TYPE_META[tc]["aggregation"] == item["aggregation"]
        assert TYPE_META[tc]["category"] == item["category"]


# -- walking_steadiness special case -----------------------------------------

def test_walking_steadiness_identifier():
    """walking_steadiness uses AppleWalkingSteadiness, not WalkingSteepiness."""
    assert TYPE_MAP["walking_steadiness"][1] == "HKQuantityTypeIdentifierAppleWalkingSteadiness"


# -- describe_type -----------------------------------------------------------

def test_describe_type_known():
    """describe_type returns correct fields for a known type."""
    info = describe_type("weight")
    assert info["known"] is True
    assert info["name"] == "Weight"
    assert info["healthkit"] == "HKQuantityTypeIdentifierBodyMass"
    assert info["unit"] == "kg"
    assert info["aggregation"] == "latest"
    assert info["category"] == "body"
    assert info["type_code"] == "weight"


def test_describe_type_unknown():
    """describe_type returns safe defaults for an unknown type and never raises."""
    info = describe_type("totally_fake_type")
    assert info["known"] is False
    assert info["name"] == "totally_fake_type"
    assert info["healthkit"] is None
    assert info["unit"] is None
    assert info["category"] == "other"
    assert info["aggregation"] == "min_max_average"
    assert info["type_code"] == "totally_fake_type"


# -- unmapped_types ----------------------------------------------------------

def test_unmapped_types_dedupe_and_sort():
    """unmapped_types returns sorted unique codes not in TYPE_MAP."""
    result = unmapped_types(["zzz", "aaa", "zzz", "weight"])
    assert result == ["aaa", "zzz"]


def test_unmapped_types_empty():
    """If all codes are mapped, return empty list."""
    result = unmapped_types(["weight", "steps"])
    assert result == []


# -- NON_HK_TYPES ------------------------------------------------------------

def test_non_hk_types():
    """NON_HK_TYPES equals the set of codes whose identifier is None."""
    expected = {tc for tc, (_name, hk) in TYPE_MAP.items() if hk is None}
    assert NON_HK_TYPES == expected


# -- metric_name / healthkit_identifier --------------------------------------

def test_metric_name_default():
    """metric_name returns the type_code itself for unknown types."""
    assert metric_name("nonexistent") == "nonexistent"


def test_healthkit_identifier_default():
    """healthkit_identifier returns None for unknown types."""
    assert healthkit_identifier("nonexistent") is None
