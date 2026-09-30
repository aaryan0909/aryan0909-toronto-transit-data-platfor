"""Tests for the standalone honest_checks package.

These import only honest_checks and pandas: the package must stay usable
with no knowledge of the TTC pipeline (that is the spin-out boundary).
"""
import json

import pandas as pd
import pytest

from honest_checks import load_dataset, run_checks
from honest_checks.__main__ import main as cli_main


def _df() -> pd.DataFrame:
    return pd.DataFrame({
        "event_ts": ["2026-08-01 08:00", "2026-08-02 09:00", "2026-08-03 10:00"],
        "station": ["ALPHA", "BETA", "ALPHA"],
        "amount": [5, 0, 3],
    })


def test_generic_checks_pass():
    results = {c.name: c for c in run_checks(_df(), {
        "layer": "test",
        "required_columns": ["event_ts", "station", "amount"],
        "timestamp_column": "event_ts",
        "not_blank_max_rates": {"station": 0.0},
        "non_negative_columns": ["amount"],
        "duplicate_max_rate": 0.0,
        "freshness": {"date_column": "event_ts", "max_age_days": 3650},
    })}
    assert all(c.passed for c in results.values()), results


def test_unknown_config_key_is_rejected():
    with pytest.raises(ValueError, match="Unknown config keys"):
        run_checks(_df(), {"requried_columns": ["event_ts"]})  # typo must not pass silently


def test_failing_check_reports_measured_value():
    results = run_checks(_df(), {"non_negative_columns": ["amount"]})
    df = _df()
    df.loc[0, "amount"] = -1
    results = run_checks(df, {"non_negative_columns": ["amount"]})
    assert results[0].passed is False
    assert results[0].measured == 1


def test_cli_against_parquet(tmp_path):
    data_path = tmp_path / "data.parquet"
    _df().to_parquet(data_path, index=False)
    config_path = tmp_path / "checks.json"
    config_path.write_text(json.dumps({
        "dataset_name": "fixture",
        "required_columns": ["event_ts", "station"],
        "non_negative_columns": ["amount"],
    }))
    out_path = tmp_path / "report.json"
    assert cli_main(["--input", str(data_path), "--config", str(config_path), "--out", str(out_path)]) == 0
    report = json.loads(out_path.read_text())
    assert report["checks_passed"] == report["checks_total"] == 2
    assert load_dataset(data_path).shape == (3, 3)
