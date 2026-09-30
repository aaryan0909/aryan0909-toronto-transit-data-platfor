"""Pipeline orchestration and dashboard export.

Run:  python -m ttc_platform.run_pipeline
Steps: ingest -> bronze -> silver -> load DuckDB -> dbt gold -> DQ checks
-> run report (JSON + Markdown) -> dashboard JSON export.

Idempotency: ingest skips unchanged files (SHA-256 manifest), bronze
rewrites its partitions, silver and gold are full rebuilds of deterministic
transforms over the same inputs, so re-running produces the same outputs.
Full rebuild is the honest choice at this scale (~72k rows); the design doc
explains where incremental models would slot in at production scale.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from . import ingest, quality, transform
from .config import (
    DASHBOARD_DATA_DIR,
    DATA_DIR,
    GOLD_DIR,
    REPORT_DIR,
    REPO_ROOT,
    WAREHOUSE_PATH,
)


def _run_dbt() -> str:
    import os
    import shutil

    dbt_dir = REPO_ROOT / "dbt"
    dbt_bin = shutil.which("dbt") or str(Path(sys.executable).parent / "dbt")
    env = {**os.environ, "TTC_DUCKDB_PATH": str(WAREHOUSE_PATH)}
    proc = subprocess.run(
        [dbt_bin, "run", "--project-dir", str(dbt_dir), "--profiles-dir", str(dbt_dir)],
        capture_output=True, text=True, env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"dbt run failed:\n{proc.stdout}\n{proc.stderr}")
    return "success"


def export_dashboard(con: duckdb.DuckDBPyConnection, run_report: dict) -> None:
    DASHBOARD_DATA_DIR.mkdir(parents=True, exist_ok=True)
    GOLD_DIR.mkdir(parents=True, exist_ok=True)
    tables = ["daily_summary", "delays_by_line", "delays_by_station",
              "delays_by_hour", "monthly_trend", "delays_by_code"]
    payload: dict = {"run_report": run_report}
    for table in tables:
        rows = con.execute(f"select * from main.{table}").fetchdf()
        rows.to_parquet(GOLD_DIR / f"{table}.parquet", index=False)
        if table == "daily_summary":
            continue  # gold-only table; the dashboard renders monthly trend instead
        if table == "delays_by_station":
            rows = rows.head(50)  # dashboard shows a leaderboard, not 1,489 free-text values
        payload[table] = json.loads(rows.to_json(orient="records", date_format="iso"))
    summary = con.execute(
        """
        select count(*) incidents, sum(min_delay) total_delay_minutes,
               min(event_date)::varchar min_date, max(event_date)::varchar max_date,
               count(distinct station) stations, count(distinct line_canonical) lines
        from main.silver_delays
        """
    ).fetchone()
    payload["headline"] = {
        "incidents": int(summary[0]),
        "total_delay_minutes": int(summary[1]),
        "min_date": summary[2],
        "max_date": summary[3],
        "stations": int(summary[4]),
        "lines": int(summary[5]),
    }
    (DASHBOARD_DATA_DIR / "dashboard.json").write_text(json.dumps(payload))


def main() -> dict:
    started = datetime.now(timezone.utc)
    manifest = ingest.download_all()
    bronze = transform.build_bronze()
    silver = transform.build_silver(bronze)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WAREHOUSE_PATH))
    con.execute("drop table if exists silver_delays")
    con.register("silver_df", silver)
    con.execute("create table silver_delays as select * from silver_df")
    con.unregister("silver_df")
    con.close()  # DuckDB is single-writer: release the file before dbt opens it.
    dbt_status = _run_dbt()
    con = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)

    checks = quality.run_checks(bronze, silver, run_date=started.date())
    run_report = {
        "run_started_utc": started.isoformat(),
        "run_finished_utc": datetime.now(timezone.utc).isoformat(),
        "source_package": "ttc-subway-delay-data (Toronto Open Data / CKAN)",
        "manifest_files": manifest["files"],
        "row_counts": {
            "bronze": int(len(bronze)),
            "silver": int(len(silver)),
            "silver_rejects_or_duplicates_removed": int(len(bronze) - len(silver)),
        },
        "dbt": dbt_status,
        "checks": quality.checks_as_dicts(checks),
        "checks_passed": sum(1 for c in checks if c.passed),
        "checks_total": len(checks),
    }

    export_dashboard(con, run_report)
    con.close()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "latest_run.json").write_text(json.dumps(run_report, indent=2))
    lines = [
        "# Latest pipeline run", "",
        f"- Started (UTC): {run_report['run_started_utc']}",
        f"- Rows: bronze {len(bronze):,} -> silver {len(silver):,}",
        f"- Data quality: {run_report['checks_passed']}/{run_report['checks_total']} checks passed", "",
        "| Check | Layer | Result | Measured | Threshold |",
        "|---|---|---|---|---|",
    ]
    for c in run_report["checks"]:
        lines.append(f"| {c['name']} | {c['layer']} | {'PASS' if c['passed'] else 'FAIL'} | {c['measured']} | {c['threshold']} |")
    (REPORT_DIR / "latest_run.md").write_text("\n".join(lines) + "\n")
    # A copy at the repo root of docs/ makes the report visible on GitHub.
    docs_report = REPO_ROOT / "docs" / "latest_run.md"
    docs_report.write_text("\n".join(lines) + "\n")
    print(json.dumps(run_report, indent=2))
    return run_report


if __name__ == "__main__":
    main()
