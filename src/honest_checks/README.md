# honest_checks

Plain data-quality checks for a dataset you can load into a DataFrame.
You say what "good" means in a config file; it reports, per check, the
measured value, the threshold, and pass/fail. Failed checks stay visible
with their numbers. That is the whole idea: a credible-looking wrong
number is worse than no number.

This package is deliberately standalone. It imports nothing from any host
pipeline, so it can be lifted into its own repository unchanged. The
Toronto Transit Data Platform uses it through a thin adapter
(`src/ttc_platform/quality.py`), but nothing here knows that.

## Install

```bash
pip install pandas pyarrow duckdb   # duckdb only needed for .duckdb inputs
# then put this directory's parent (src/) on your PYTHONPATH, or
# pip install the host project, which packages honest_checks alongside it.
```

## Run it

Against any Parquet, CSV, or DuckDB table:

```bash
python -m honest_checks --input data/silver/delays.parquet \
                        --config src/honest_checks/config.example.json \
                        --out report.json
python -m honest_checks --input warehouse.duckdb --table silver_delays \
                        --config checks.json
```

Exit code is 0 when every check passes and 1 otherwise, so it can gate a
CI job. The report is JSON: dataset name, row count, and one entry per
check with `name`, `layer`, `passed`, `measured`, `threshold`, `detail`.

As a library:

```python
from honest_checks import load_dataset, run_checks

df = load_dataset("delays.parquet")
results = run_checks(df, {"required_columns": ["date", "station"],
                          "non_negative_columns": ["min_delay"]})
```

## What it checks

| Config key | Check | Meaning |
|---|---|---|
| `required_columns` | `schema_conformance` | Every required column is present. |
| `timestamp_column` + `timestamp_parse_min_rate` | `timestamp_parse_rate` | Share of values that parse as timestamps. |
| `not_blank_max_rates` | `<column>_null_rate` | Per-column ceiling on blank values. |
| `non_negative_columns` | `non_negative_<column>` | Zero rows below zero. |
| `reference_coverage` | custom `name` | Share of a column's values found in a reference set (e.g. valid codes). |
| `duplicate_max_rate` | `duplicate_rate` | Ceiling on exact-duplicate rows. |
| `mapped_value_coverage` | custom `name` | Share of rows mapped to a real value rather than an explicit unknown bucket. |
| `freshness` | `freshness` | Max date in a date column, in days before the run date. |
| `volume_anomaly` | `volume_anomaly` | Latest full period's row count vs the trailing-period mean (the last, possibly partial, period is excluded). |
| `long_tail` | custom `name` | Share of rows in rare values of a free-text column (values occurring N times or fewer). |

## Config format

JSON, one object. Unknown keys are rejected, so a typo can never silently
disable a check. Full example: `config.example.json` in this directory.
Every key is optional; only the checks you name will run.

```json
{
  "dataset_name": "my_dataset",
  "layer": "silver",
  "required_columns": ["event_ts", "station", "min_delay"],
  "timestamp_column": "event_ts",
  "timestamp_parse_min_rate": 0.99,
  "not_blank_max_rates": {"station": 0.01},
  "non_negative_columns": ["min_delay"],
  "duplicate_max_rate": 0.01,
  "freshness": {"date_column": "event_date", "max_age_days": 45}
}
```

Thresholds are yours to argue with. They live in the config, in the open,
next to the measured values they produced.
