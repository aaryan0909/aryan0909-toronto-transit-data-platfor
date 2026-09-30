"""Bronze and silver transforms.

Bronze: the source files, faithfully unified (same columns, same values)
and written as Parquet partitioned by year/month derived from the event
date. No cleaning happens here on purpose: bronze must be re-playable.

Silver: one row per delay incident, cleaned and typed:
  * station names trimmed / upper-cased / whitespace-collapsed
  * raw ``line`` values mapped to a canonical line, with the raw value
    preserved in ``line_raw`` (the source contains values such as
    ``YU/ BD``, ``LINE 2 - BLOOR DANFORTH`` and even bus route numbers --
    silently dropping or guessing them would break the honest-numbers rule)
  * ``date`` + ``time`` parsed into a single ``event_ts``
  * delay-code description joined from the reference table
  * exact duplicates removed; rows with an unparseable date or a negative
    delay are quarantined to a rejects file, never silently vanished
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .config import BRONZE_DIR, RAW_DIR, REQUIRED_COLUMNS, SILVER_DIR

_WS = re.compile(r"\s+")

_SINGLE_LINE_MAP = {
    "YU": "LINE_1_YONGE_UNIVERSITY",
    "YUS": "LINE_1_YONGE_UNIVERSITY",
    "BD": "LINE_2_BLOOR_DANFORTH",
    "SHP": "LINE_4_SHEPPARD",
    "SRT": "LINE_3_SCARBOROUGH_RT",
}


def normalize_column_name(name: str) -> str:
    return _WS.sub("_", str(name).strip().lower())


def clean_station(value: object) -> str:
    return _WS.sub(" ", str(value).strip().upper()) if pd.notna(value) else ""


def fix_mojibake(value: object) -> str:
    """Repair text that was UTF-8, mis-decoded as cp1252, and re-encoded.

    The published code reference file is double-encoded (an en dash arrives
    as the three characters 'â€"'). Reversing the mis-decode restores the
    intended text; anything that does not round-trip is left untouched.
    """
    if not isinstance(value, str):
        return value
    try:
        raw = bytes(
            ord(ch) if ord(ch) < 256 else ch.encode("cp1252")[0] for ch in value
        )
        return raw.decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def canonical_line(raw: object) -> str:
    """Map a messy raw line value to a canonical line or an explicit bucket."""
    if pd.isna(raw):
        return "UNKNOWN"
    value = _WS.sub("", str(raw).strip().upper())
    if not value:
        return "UNKNOWN"
    if value in _SINGLE_LINE_MAP:
        return _SINGLE_LINE_MAP[value]
    if "/" in value or "-" in value or "LINES" in value:
        return "MULTI_LINE_OR_NETWORK"
    if "LINE1" in value:
        return "LINE_1_YONGE_UNIVERSITY"
    if "LINE2" in value:
        return "LINE_2_BLOOR_DANFORTH"
    return "UNKNOWN"


def _read_source(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".xlsx":
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.columns = [normalize_column_name(c) for c in df.columns]
    if "_id" in df.columns:
        df = df.drop(columns=["_id"])
    return df


def build_bronze(raw_dir: Path = RAW_DIR, bronze_dir: Path = BRONZE_DIR) -> pd.DataFrame:
    frames = []
    for name in ("delays_2024.xlsx", "delays_since_2025.csv"):
        df = _read_source(raw_dir / name)
        df["source_file"] = name
        frames.append(df)
    bronze = pd.concat(frames, ignore_index=True)
    missing = [c for c in REQUIRED_COLUMNS if c not in bronze.columns]
    if missing:
        raise ValueError(f"Bronze schema is missing required columns: {missing}")

    # Unify types across formats: the XLSX publishes dates/numbers natively,
    # the CSV publishes text. Bronze stores the raw values as text exactly as
    # published; typing is silver's job.
    for col in REQUIRED_COLUMNS:
        bronze[col] = bronze[col].map(
            lambda v: v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else (str(v) if pd.notna(v) else "")
        )

    parsed_dates = pd.to_datetime(bronze["date"], errors="coerce")
    bronze["partition_year"] = parsed_dates.dt.year.fillna(0).astype(int)
    bronze["partition_month"] = parsed_dates.dt.month.fillna(0).astype(int)

    bronze_dir.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(bronze, preserve_index=False)
    pq.write_to_dataset(
        table,
        root_path=bronze_dir,
        partition_cols=["partition_year", "partition_month"],
        existing_data_behavior="delete_matching",
    )
    return bronze


def build_silver(bronze: pd.DataFrame, raw_dir: Path = RAW_DIR, silver_dir: Path = SILVER_DIR) -> pd.DataFrame:
    df = bronze.copy()
    df["event_date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["event_ts"] = pd.to_datetime(
        df["date"].astype(str).str.slice(0, 10) + " " + df["time"].astype(str).str.slice(0, 5),
        errors="coerce",
    )
    df["station_clean"] = df["station"].map(clean_station)
    df["code_clean"] = df["code"].astype(str).str.strip().str.upper()
    df["line_raw"] = df["line"].astype(str)
    df["line_canonical"] = df["line"].map(canonical_line)
    df["min_delay"] = pd.to_numeric(df["min_delay"], errors="coerce")
    df["min_gap"] = pd.to_numeric(df["min_gap"], errors="coerce")
    df["bound_clean"] = df["bound"].astype(str).str.strip().str.upper()
    df["vehicle_id"] = df["vehicle"].astype(str).str.strip()

    codes = pd.read_csv(raw_dir / "delay_codes.csv", dtype=str, keep_default_na=False)
    codes.columns = [normalize_column_name(c) for c in codes.columns]
    codes = codes.rename(columns={"code": "code_clean", "description": "code_description"})
    if "_id" in codes.columns:
        codes = codes.drop(columns=["_id"])
    codes["code_clean"] = codes["code_clean"].str.strip().str.upper()
    codes["code_description"] = codes["code_description"].map(fix_mojibake)
    df = df.merge(codes[["code_clean", "code_description"]], on="code_clean", how="left")

    reject_mask = df["event_date"].isna() | df["min_delay"].isna() | (df["min_delay"] < 0)
    silver_dir.mkdir(parents=True, exist_ok=True)
    rejects = df[reject_mask]
    if len(rejects):
        rejects.to_parquet(silver_dir / "rejects.parquet", index=False)

    silver = df[~reject_mask].copy()
    silver = silver.drop_duplicates(
        subset=["event_ts", "station_clean", "code_clean", "min_delay", "vehicle_id", "line_raw"]
    )
    silver["event_hour"] = silver["event_ts"].dt.hour
    silver["event_year_month"] = silver["event_ts"].dt.strftime("%Y-%m")
    silver = silver[
        [
            "event_ts", "event_date", "event_hour", "event_year_month", "day",
            "station_clean", "code_clean", "code_description", "min_delay",
            "min_gap", "bound_clean", "line_raw", "line_canonical",
            "vehicle_id", "source_file",
        ]
    ].rename(columns={"day": "day_of_week", "station_clean": "station", "bound_clean": "bound"})
    silver.to_parquet(silver_dir / "delays.parquet", index=False)
    return silver
