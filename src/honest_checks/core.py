"""Core check implementations. No imports from any host pipeline."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import pandas as pd


@dataclass
class CheckResult:
    name: str
    layer: str
    passed: bool
    measured: float | str
    threshold: str
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


def load_dataset(path: str | Path, table: str | None = None) -> pd.DataFrame:
    """Load a dataset from .parquet, .csv, or a .duckdb file (+ --table)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".duckdb", ".db"}:
        if not table:
            raise ValueError("A table name is required for DuckDB inputs.")
        import duckdb

        con = duckdb.connect(str(path), read_only=True)
        try:
            return con.execute(f'select * from "{table}"').fetchdf()
        finally:
            con.close()
    raise ValueError(f"Unsupported input type: {suffix} (use .parquet, .csv, or .duckdb)")


def run_checks(df: pd.DataFrame, config: dict, run_date: date | None = None) -> list[CheckResult]:
    """Run every check named in ``config`` against ``df``.

    Config format is documented in this package's README.md. Unknown keys
    are rejected so a typo can never silently disable a check.
    """
    run_date = run_date or date.today()
    layer = str(config.get("layer", "dataset"))
    known = {
        "dataset_name", "layer", "required_columns", "timestamp_column",
        "timestamp_parse_min_rate", "not_blank_max_rates", "non_negative_columns",
        "reference_coverage", "duplicate_max_rate", "mapped_value_coverage",
        "freshness", "volume_anomaly", "long_tail",
    }
    unknown = set(config) - known
    if unknown:
        raise ValueError(f"Unknown config keys: {sorted(unknown)}")

    checks: list[CheckResult] = []
    add = checks.append

    if "required_columns" in config:
        required = list(config["required_columns"])
        present = [c for c in required if c in df.columns]
        add(CheckResult(
            "schema_conformance", layer, len(present) == len(required),
            f"{len(present)}/{len(required)} required columns present",
            "all required columns present", f"columns: {sorted(df.columns)}",
        ))

    if "timestamp_column" in config:
        col = config["timestamp_column"]
        min_rate = float(config.get("timestamp_parse_min_rate", 0.99))
        rate = float(pd.to_datetime(df[col], errors="coerce").notna().mean()) if len(df) else 0.0
        add(CheckResult(
            "timestamp_parse_rate", layer, rate >= min_rate, round(rate, 4),
            f">= {min_rate}", f"share of rows whose {col} parses as a timestamp",
        ))

    for col, max_rate in config.get("not_blank_max_rates", {}).items():
        rate = float((df[col].astype(str).str.strip() == "").mean()) if len(df) else 1.0
        add(CheckResult(
            f"{col}_null_rate", layer, rate <= float(max_rate), round(rate, 4),
            f"<= {max_rate}", f"share of rows with a blank {col}",
        ))

    for col in config.get("non_negative_columns", []):
        negative = int((pd.to_numeric(df[col], errors="coerce") < 0).sum())
        add(CheckResult(
            f"non_negative_{col}", layer, negative == 0, negative, "== 0",
            f"count of rows with a negative {col}",
        ))

    if "reference_coverage" in config:
        spec = config["reference_coverage"]
        col, min_rate = spec["column"], float(spec["min_rate"])
        reference = {str(v).strip().upper() for v in spec["reference_values"]}
        rate = float(df[col].astype(str).str.strip().str.upper().isin(reference).mean()) if len(df) else 0.0
        add(CheckResult(
            spec.get("name", "reference_coverage"), layer, rate >= min_rate, round(rate, 4),
            f">= {min_rate}", f"share of rows whose {col} exists in the reference set ({len(reference)} values)",
        ))

    if "duplicate_max_rate" in config:
        max_rate = float(config["duplicate_max_rate"])
        rate = float(df.duplicated().mean()) if len(df) else 0.0
        add(CheckResult(
            "duplicate_rate", layer, rate <= max_rate, round(rate, 4),
            f"<= {max_rate}", "exact-duplicate row share",
        ))

    if "mapped_value_coverage" in config:
        spec = config["mapped_value_coverage"]
        col = spec["column"]
        unknown_values = {str(v) for v in spec.get("unknown_values", ["UNKNOWN", ""])}
        min_rate = float(spec["min_rate"])
        rate = float((~df[col].astype(str).isin(unknown_values)).mean()) if len(df) else 0.0
        add(CheckResult(
            spec.get("name", "mapped_value_coverage"), layer, rate >= min_rate, round(rate, 4),
            f">= {min_rate}", f"share of rows whose {col} is mapped (not in {sorted(unknown_values)})",
        ))

    if "freshness" in config:
        spec = config["freshness"]
        col, max_age = spec["date_column"], int(spec["max_age_days"])
        max_date = pd.to_datetime(df[col], errors="coerce").max().date() if len(df) else run_date
        age = (run_date - max_date).days
        add(CheckResult(
            "freshness", layer, age <= max_age,
            f"max {col} {max_date.isoformat()}, {age} days before run date",
            f"<= {max_age} days", "a stale max date means ingestion silently stopped",
        ))

    if "volume_anomaly" in config:
        spec = config["volume_anomaly"]
        col = spec["period_column"]
        max_dev = float(spec["max_deviation"])
        min_periods = int(spec.get("min_periods", 7))
        monthly = df.groupby(col).size().sort_index()
        if len(monthly) >= min_periods:
            latest_full = float(monthly.iloc[-2])  # last period may be partial
            trailing_mean = float(monthly.iloc[-8:-2].mean()) if len(monthly) >= 8 else float(monthly.iloc[:-2].mean())
            deviation = abs(latest_full - trailing_mean) / trailing_mean if trailing_mean else 0.0
            add(CheckResult(
                "volume_anomaly", layer, deviation <= max_dev, round(deviation, 4),
                f"<= {max_dev}",
                f"latest full period {int(latest_full)} rows vs trailing mean {trailing_mean:.0f}",
            ))
        else:
            add(CheckResult(
                "volume_anomaly", layer, False, "insufficient history",
                f"<= {max_dev}", f"fewer than {min_periods} periods of data",
            ))

    if "long_tail" in config:
        spec = config["long_tail"]
        col = spec["column"]
        max_count, max_rate = int(spec["max_count"]), float(spec["max_rate"])
        counts = df[col].value_counts()
        rate = float(df[col].isin(counts[counts <= max_count].index).mean()) if len(df) else 1.0
        add(CheckResult(
            spec.get("name", "long_tail_rate"), layer, rate <= max_rate, round(rate, 4),
            f"<= {max_rate}",
            f"share of rows whose {col} value occurs {max_count} times or fewer (free-text long tail)",
        ))
    return checks
