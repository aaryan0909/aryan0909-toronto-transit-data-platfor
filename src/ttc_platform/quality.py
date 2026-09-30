"""TTC adapter over the standalone ``honest_checks`` package.

All check logic lives in ``src/honest_checks`` (no pipeline imports there;
it can be lifted into its own repo unchanged). This module only translates
TTC-specific expectations into an honest_checks config and preserves the
pipeline's run-report shape.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
from pathlib import Path

import pandas as pd

from honest_checks import CheckResult, run_checks as _run_checks

from .config import RAW_DIR

# Thresholds live here, once, so a reader can argue with them in one place.
FRESHNESS_MAX_AGE_DAYS = 45
TIMESTAMP_PARSE_MIN_RATE = 0.99
STATION_NULL_MAX_RATE = 0.01
CODE_REFERENCE_MIN_RATE = 0.90
DUPLICATE_MAX_RATE = 0.01
CANONICAL_LINE_MIN_RATE = 0.90
VOLUME_ANOMALY_MAX_DEVIATION = 0.60  # latest full month vs trailing 6-month mean
STATION_LONG_TAIL_MAX_RATE = 0.10


def _reference_codes(raw_dir: Path) -> list[str]:
    codes = pd.read_csv(raw_dir / "delay_codes.csv", dtype=str, keep_default_na=False)
    codes.columns = [c.strip().lower() for c in codes.columns]
    return sorted(codes["code"].str.strip().str.upper().unique().tolist())


def run_checks(bronze: pd.DataFrame, silver: pd.DataFrame, run_date: date | None = None,
               raw_dir: Path = RAW_DIR) -> list[CheckResult]:
    run_date = run_date or date.today()
    bronze_checks = _run_checks(
        bronze,
        {"layer": "bronze",
         "required_columns": ["date", "time", "station", "code", "min_delay", "line"]},
        run_date=run_date,
    )
    silver_checks = _run_checks(
        silver,
        {
            "layer": "silver",
            "timestamp_column": "event_ts",
            "timestamp_parse_min_rate": TIMESTAMP_PARSE_MIN_RATE,
            "not_blank_max_rates": {"station": STATION_NULL_MAX_RATE},
            "non_negative_columns": ["min_delay"],
            "reference_coverage": {
                "name": "code_reference_coverage",
                "column": "code_clean",
                "min_rate": CODE_REFERENCE_MIN_RATE,
                "reference_values": _reference_codes(raw_dir),
            },
            "duplicate_max_rate": DUPLICATE_MAX_RATE,
            "mapped_value_coverage": {
                "name": "canonical_line_rate",
                "column": "line_canonical",
                "unknown_values": ["UNKNOWN"],
                "min_rate": CANONICAL_LINE_MIN_RATE,
            },
            "freshness": {"date_column": "event_date", "max_age_days": FRESHNESS_MAX_AGE_DAYS},
            "volume_anomaly": {
                "period_column": "event_year_month",
                "max_deviation": VOLUME_ANOMALY_MAX_DEVIATION,
                "min_periods": 7,
            },
            "long_tail": {
                "name": "station_long_tail_rate",
                "column": "station",
                "max_count": 5,
                "max_rate": STATION_LONG_TAIL_MAX_RATE,
            },
        },
        run_date=run_date,
    )
    return bronze_checks + silver_checks


def checks_as_dicts(checks: list[CheckResult]) -> list[dict]:
    return [asdict(c) for c in checks]
