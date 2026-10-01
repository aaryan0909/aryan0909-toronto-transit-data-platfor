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
import pandas as pd

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


# Short codes for canonical lines, used in the by-line dashboard exports so
# the JSON stays small enough for the file-API upload caps. dashboard.json
# carries the reverse map ("line_codes") so the files stay self-describing.
LINE_SHORT_CODES = {
    "LINE_1_YONGE_UNIVERSITY": "L1",
    "LINE_2_BLOOR_DANFORTH": "L2",
    "LINE_4_SHEPPARD": "L4",
    "LINE_3_SCARBOROUGH_RT": "L3",
    "MULTI_LINE_OR_NETWORK": "ML",
    "UNKNOWN": "UNK",
}


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
            continue  # exported below, alongside the filterable breakdowns
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

    # --- Filterable breakdowns -------------------------------------------
    # The dashboard's line/year filters need aggregates at those grains,
    # computed here from silver (never in the browser from raw rows).
    # The export is split across four files, each small enough to ship
    # through the GitHub/Vercel file APIs, which cap a single call:
    #   dashboard.json      headline, gold tables, totals + hourly breakdown
    #   tables_by_line.json monthly / station / code breakdowns by line
    #   daily.json          daily series as compact arrays (see below)
    #   quarter.json        the four breakdowns at quarter grain, as arrays
    # By-line rows carry a short line code in `line`; `line_codes` in
    # dashboard.json maps it back to the canonical name, so the files stay
    # self-describing.
    payload["line_codes"] = {v: k for k, v in LINE_SHORT_CODES.items()}

    def run_query(sql: str) -> list[dict]:
        rows = con.execute(sql).fetchdf()
        records = json.loads(rows.to_json(orient="records", date_format="iso"))
        for r in records:
            if "line_canonical" in r:
                r["line"] = LINE_SHORT_CODES[r.pop("line_canonical")]
        return records

    # One row per (year, line): drives the headline cards under filters.
    payload["totals_by_year_line"] = run_query("""
        select extract(year from event_date)::int as event_year,
               line_canonical, count(*) as incidents,
               sum(min_delay) as total_delay_minutes,
               count(distinct station) as stations
        from main.silver_delays
        group by event_year, line_canonical
        order by event_year, line_canonical
    """)
    payload["hour_by_year_line"] = run_query("""
        select extract(year from event_date)::int as event_year,
               line_canonical, event_hour, count(*) as incidents,
               sum(min_delay) as total_delay_minutes
        from main.silver_delays
        group by event_year, line_canonical, event_hour
        order by event_year, line_canonical, event_hour
    """)
    tables_payload = {
        "monthly_by_line": run_query("""
            select event_year_month, line_canonical, count(*) as incidents,
                   sum(min_delay) as total_delay_minutes
            from main.silver_delays
            group by event_year_month, line_canonical
            order by event_year_month, line_canonical
        """),
        # Top 15 stations per (year, line); the all-years/all-lines view
        # keeps using delays_by_station above.
        "stations_by_year_line": run_query("""
            select event_year, line_canonical, station, incidents,
                   total_delay_minutes, avg_delay_minutes_when_delayed
            from (
                select *, row_number() over (
                    partition by event_year, line_canonical
                    order by total_delay_minutes desc) as rn
                from (
                    select extract(year from event_date)::int as event_year,
                           line_canonical, station, count(*) as incidents,
                           sum(min_delay) as total_delay_minutes,
                           round(avg(case when min_delay > 0 then min_delay end), 2)
                               as avg_delay_minutes_when_delayed
                    from main.silver_delays
                    where station <> ''
                    group by event_year, line_canonical, station
                )
            )
            where rn <= 15
            order by event_year, line_canonical, total_delay_minutes desc
        """),
        # Top 12 delay codes per (year, line).
        "codes_by_year_line": run_query("""
            select event_year, line_canonical, code, description, incidents,
                   total_delay_minutes
            from (
                select *, row_number() over (
                    partition by event_year, line_canonical
                    order by total_delay_minutes desc) as rn
                from (
                    select extract(year from event_date)::int as event_year,
                           line_canonical, code_clean as code,
                           coalesce(any_value(code_description),
                                    'Not in reference table') as description,
                           count(*) as incidents,
                           sum(min_delay) as total_delay_minutes
                    from main.silver_delays
                    group by event_year, line_canonical, code_clean
                )
            )
            where rn <= 12
            order by event_year, line_canonical, total_delay_minutes desc
        """),
    }
    (DASHBOARD_DATA_DIR / "dashboard.json").write_text(json.dumps(payload))
    (DASHBOARD_DATA_DIR / "tables_by_line.json").write_text(json.dumps(tables_payload))

    # Daily series as compact arrays, documented by daily_fields in the
    # file itself: dates are epoch days (days since 1970-01-01) and lines
    # use the same short codes. A per-day, per-line object format would
    # repeat the full line name ~3,900 times and blow the upload cap.
    daily = con.execute("select * from main.daily_summary").fetchdf()
    epoch = pd.Timestamp("1970-01-01")
    daily_rows = [
        [int((pd.Timestamp(d) - epoch).days), int(i), int(v)]
        for d, i, v in zip(daily["event_date"], daily["incidents"],
                           daily["total_delay_minutes"])
    ]
    by_line = con.execute("""
        select event_date, line_canonical, sum(min_delay) as total_delay_minutes
        from main.silver_delays
        group by event_date, line_canonical
        order by event_date, line_canonical
    """).fetchdf()
    by_line_rows = [
        [int((pd.Timestamp(d) - epoch).days), LINE_SHORT_CODES[line], int(v)]
        for d, line, v in zip(by_line["event_date"], by_line["line_canonical"],
                              by_line["total_delay_minutes"])
    ]
    daily_payload = {
        "daily_fields": {
            "daily_summary": ["epoch_day", "incidents", "total_delay_minutes"],
            "daily_by_line": ["epoch_day", "line_code", "total_delay_minutes"],
            "line_codes": "see line_codes in dashboard.json",
        },
        "daily_summary": daily_rows,
        "daily_by_line": by_line_rows,
    }
    (DASHBOARD_DATA_DIR / "daily.json").write_text(json.dumps(daily_payload))

    # --- Quarter-grain breakdowns -----------------------------------------
    # Same four breakdowns at (year, quarter, line) grain so the period
    # filter can offer quarters. Object format measured 89-126KB per table
    # here (station names and code descriptions repeat across quarters), so
    # quarter.json ships arrays plus two lookups: station_names (rows carry
    # an index) and code_descriptions (rows carry the code only).
    q_totals = con.execute("""
        select extract(year from event_date)::int as event_year,
               extract(quarter from event_date)::int as event_quarter,
               line_canonical, count(*) as incidents,
               sum(min_delay) as total_delay_minutes,
               count(distinct station) as stations
        from main.silver_delays
        group by event_year, event_quarter, line_canonical
        order by event_year, event_quarter, line_canonical
    """).fetchdf()
    q_hour = con.execute("""
        select extract(year from event_date)::int as event_year,
               extract(quarter from event_date)::int as event_quarter,
               line_canonical, event_hour, count(*) as incidents,
               sum(min_delay) as total_delay_minutes
        from main.silver_delays
        group by event_year, event_quarter, line_canonical, event_hour
        order by event_year, event_quarter, line_canonical, event_hour
    """).fetchdf()
    q_stations = con.execute("""
        select event_year, event_quarter, line_canonical, station, incidents,
               total_delay_minutes, avg_delay_minutes_when_delayed
        from (
            select *, row_number() over (
                partition by event_year, event_quarter, line_canonical
                order by total_delay_minutes desc) as rn
            from (
                select extract(year from event_date)::int as event_year,
                       extract(quarter from event_date)::int as event_quarter,
                       line_canonical, station, count(*) as incidents,
                       sum(min_delay) as total_delay_minutes,
                       round(avg(case when min_delay > 0 then min_delay end), 2)
                           as avg_delay_minutes_when_delayed
                from main.silver_delays
                where station <> ''
                group by event_year, event_quarter, line_canonical, station
            )
        )
        where rn <= 15
        order by event_year, event_quarter, line_canonical, total_delay_minutes desc
    """).fetchdf()
    q_codes = con.execute("""
        select event_year, event_quarter, line_canonical, code, description,
               incidents, total_delay_minutes
        from (
            select *, row_number() over (
                partition by event_year, event_quarter, line_canonical
                order by total_delay_minutes desc) as rn
            from (
                select extract(year from event_date)::int as event_year,
                       extract(quarter from event_date)::int as event_quarter,
                       line_canonical, code_clean as code,
                       coalesce(any_value(code_description),
                                'Not in reference table') as description,
                       count(*) as incidents,
                       sum(min_delay) as total_delay_minutes
                from main.silver_delays
                group by event_year, event_quarter, line_canonical, code_clean
            )
        )
        where rn <= 12
        order by event_year, event_quarter, line_canonical, total_delay_minutes desc
    """).fetchdf()
    station_names = list(dict.fromkeys(q_stations["station"]))
    station_index = {name: i for i, name in enumerate(station_names)}
    code_descriptions = dict(zip(q_codes["code"], q_codes["description"]))

    def num(v):
        return None if v is None or (isinstance(v, float) and pd.isna(v)) else (
            int(v) if float(v).is_integer() else float(v))

    quarter_payload = {
        "quarter_fields": {
            "totals_by_quarter_line": ["event_year", "event_quarter", "line_code",
                                       "incidents", "total_delay_minutes", "stations"],
            "hour_by_quarter_line": ["event_year", "event_quarter", "line_code",
                                     "event_hour", "incidents", "total_delay_minutes"],
            "stations_by_quarter_line": ["event_year", "event_quarter", "line_code",
                                         "station_index", "incidents", "total_delay_minutes",
                                         "avg_delay_minutes_when_delayed"],
            "codes_by_quarter_line": ["event_year", "event_quarter", "line_code",
                                      "code", "incidents", "total_delay_minutes"],
            "station_names": "index lookup for station_index",
            "code_descriptions": "description lookup for codes_by_quarter_line codes",
            "line_codes": "see line_codes in dashboard.json",
        },
        "station_names": station_names,
        "code_descriptions": code_descriptions,
        "totals_by_quarter_line": [
            [int(y), int(q), LINE_SHORT_CODES[line], int(i), int(d), int(s)]
            for y, q, line, i, d, s in zip(
                q_totals["event_year"], q_totals["event_quarter"],
                q_totals["line_canonical"], q_totals["incidents"],
                q_totals["total_delay_minutes"], q_totals["stations"])
        ],
        "hour_by_quarter_line": [
            [int(y), int(q), LINE_SHORT_CODES[line], int(hr), int(i), int(d)]
            for y, q, line, hr, i, d in zip(
                q_hour["event_year"], q_hour["event_quarter"],
                q_hour["line_canonical"], q_hour["event_hour"],
                q_hour["incidents"], q_hour["total_delay_minutes"])
        ],
        "stations_by_quarter_line": [
            [int(y), int(q), LINE_SHORT_CODES[line], station_index[st],
             int(i), int(d), num(avg)]
            for y, q, line, st, i, d, avg in zip(
                q_stations["event_year"], q_stations["event_quarter"],
                q_stations["line_canonical"], q_stations["station"],
                q_stations["incidents"], q_stations["total_delay_minutes"],
                q_stations["avg_delay_minutes_when_delayed"])
        ],
        "codes_by_quarter_line": [
            [int(y), int(q), LINE_SHORT_CODES[line], code, int(i), int(d)]
            for y, q, line, code, i, d in zip(
                q_codes["event_year"], q_codes["event_quarter"],
                q_codes["line_canonical"], q_codes["code"],
                q_codes["incidents"], q_codes["total_delay_minutes"])
        ],
    }
    (DASHBOARD_DATA_DIR / "quarter.json").write_text(json.dumps(quarter_payload))


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
