"""Unit tests for transform logic and DQ checks, run against a tiny fixture.

The fixture is synthetic and exists only to exercise the code paths; every
number in the README and dashboard comes from the real pipeline run.
"""
from pathlib import Path

import pandas as pd
import pytest

from ttc_platform.quality import run_checks
from ttc_platform.transform import build_silver, canonical_line, clean_station, fix_mojibake

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("YU", "LINE_1_YONGE_UNIVERSITY"),
        ("YUS", "LINE_1_YONGE_UNIVERSITY"),
        ("BD", "LINE_2_BLOOR_DANFORTH"),
        ("SHP", "LINE_4_SHEPPARD"),
        ("YU/ BD", "MULTI_LINE_OR_NETWORK"),
        ("", "UNKNOWN"),
        ("95 YORK MILLS", "UNKNOWN"),  # a bus route in the subway file: never guessed
        (None, "UNKNOWN"),
    ],
)
def test_canonical_line(raw, expected):
    assert canonical_line(raw) == expected


def test_fix_mojibake():
    assert fix_mojibake("TRAIN â\x80\x93 MEDICAL") == "TRAIN – MEDICAL"
    assert fix_mojibake("AIR CONDITIONING") == "AIR CONDITIONING"


def test_clean_station():
    assert clean_station("  bloor   station ") == "BLOOR STATION"
    assert clean_station(float("nan")) == ""


def _silver(tmp_path):
    bronze = pd.read_csv(FIXTURES / "bronze_sample.csv", dtype=str, keep_default_na=False)
    bronze["source_file"] = "fixture"
    # build_silver reads the code table from a raw dir; point it at fixtures.
    import shutil
    (tmp_path / "delay_codes.csv").write_bytes((FIXTURES / "delay_codes.csv").read_bytes())
    return build_silver(bronze, raw_dir=tmp_path, silver_dir=tmp_path / "silver")


def test_build_silver_fixture(tmp_path):
    silver = _silver(tmp_path)
    assert len(silver) == 4
    assert silver["line_canonical"].tolist() == [
        "LINE_2_BLOOR_DANFORTH", "LINE_1_YONGE_UNIVERSITY",
        "LINE_2_BLOOR_DANFORTH", "LINE_1_YONGE_UNIVERSITY",
    ]
    assert silver.loc[0, "code_description"] == "UNSANITARY VEHICLE"
    assert silver.loc[2, "event_hour"] == 17
    assert silver["min_delay"].sum() == 20


def test_build_silver_quarantines_negative_delay(tmp_path):
    bronze = pd.read_csv(FIXTURES / "bronze_sample.csv", dtype=str, keep_default_na=False)
    bad = bronze.iloc[[0]].copy()
    bad["min_delay"] = "-4"
    bronze = pd.concat([bronze, bad], ignore_index=True)
    bronze["source_file"] = "fixture"
    silver = build_silver(bronze, raw_dir=FIXTURES, silver_dir=tmp_path / "silver")
    assert len(silver) == 4
    assert (tmp_path / "silver" / "rejects.parquet").exists()


def test_quality_checks_on_fixture(tmp_path):
    silver = _silver(tmp_path)
    bronze = pd.read_csv(FIXTURES / "bronze_sample.csv", dtype=str, keep_default_na=False)
    checks = {c.name: c for c in run_checks(bronze, silver, raw_dir=FIXTURES)}
    assert checks["schema_conformance"].passed
    assert checks["non_negative_min_delay"].passed
    assert checks["timestamp_parse_rate"].passed
    assert checks["code_reference_coverage"].passed
    assert checks["duplicate_rate"].passed
