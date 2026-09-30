"""Central configuration: paths and source definitions.

Everything the pipeline needs to know about *where* data lives is here, so
ingest / transform / quality never hard-code a path or a URL.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
BRONZE_DIR = DATA_DIR / "bronze"
SILVER_DIR = DATA_DIR / "silver"
GOLD_DIR = DATA_DIR / "gold"
REPORT_DIR = DATA_DIR / "reports"
WAREHOUSE_PATH = DATA_DIR / "warehouse.duckdb"
DASHBOARD_DATA_DIR = REPO_ROOT / "dashboard" / "public" / "data"

CKAN_BASE = "https://ckan0.cf.opendata.inter.prod-toronto.ca"
PACKAGE_ID = "ttc-subway-delay-data"


@dataclass(frozen=True)
class SourceFile:
    """One downloadable resource from the CKAN package."""

    logical_name: str  # stable name used for the raw file on disk
    resource_name_substring: str  # matched against the CKAN resource name
    filename: str


# We intentionally ingest the two most recent, complete slices plus the
# delay-code reference table. Older years (2014-2023) are published only as
# separate XLSX files with the same schema and can be added to this list
# without any code change.
SOURCE_FILES: tuple[SourceFile, ...] = (
    SourceFile("delays_2024", "ttc-subway-delay-data-2024", "delays_2024.xlsx"),
    SourceFile("delays_since_2025", "TTC Subway Delay Data since 2025.csv", "delays_since_2025.csv"),
    SourceFile("delay_codes", "Code Descriptions.csv", "delay_codes.csv"),
)

REQUIRED_COLUMNS: tuple[str, ...] = (
    "date",
    "time",
    "day",
    "station",
    "code",
    "min_delay",
    "min_gap",
    "bound",
    "line",
    "vehicle",
)
