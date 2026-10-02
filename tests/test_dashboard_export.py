"""Tests for the dashboard JSON export, including the filter breakdowns.

Runs export_dashboard against a tiny in-memory DuckDB warehouse built from
hand-made rows, so the tests never touch the real data or the network.
The export ships as four files (dashboard.json, tables_by_line.json,
daily.json, quarter.json); the fixture merges them the way the dashboard's
JS does.
"""
import json
from datetime import date, datetime, timezone

import duckdb
import pandas as pd
import pytest

from ttc_platform import run_pipeline


def _warehouse() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    silver = pd.DataFrame([
        # date, hour, ym, station, code, desc, delay, line
        (date(2024, 1, 2), 8, "2024-01", "BLOOR STATION", "S1", "DESC ONE", 10, "LINE_1_YONGE_UNIVERSITY"),
        (date(2024, 1, 2), 9, "2024-01", "BLOOR STATION", "S1", "DESC ONE", 0, "LINE_1_YONGE_UNIVERSITY"),
        (date(2024, 2, 3), 8, "2024-02", "YONGE STATION", "S2", "DESC TWO", 5, "LINE_2_BLOOR_DANFORTH"),
        (date(2025, 3, 4), 17, "2025-03", "BLOOR STATION", "S2", "DESC TWO", 7, "LINE_1_YONGE_UNIVERSITY"),
        (date(2025, 3, 4), 17, "2025-03", "", "S2", "DESC TWO", 3, "LINE_2_BLOOR_DANFORTH"),
    ], columns=["event_date", "event_hour", "event_year_month", "station",
                "code_clean", "code_description", "min_delay", "line_canonical"])
    con.register("silver_df", silver)
    con.execute("create table silver_delays as select * from silver_df")
    con.unregister("silver_df")
    # Gold tables the export reads directly (columns as the dbt models make them).
    con.execute("""create table daily_summary as
        select event_date, count(*) incidents, 0 incidents_with_delay,
               sum(min_delay) total_delay_minutes, 0.0 avg_delay_minutes_when_delayed,
               1 stations_affected from silver_delays group by event_date""")
    con.execute("""create table delays_by_line as
        select line_canonical, count(*) incidents, sum(min_delay) total_delay_minutes,
               0.0 avg_delay_minutes_when_delayed, 0.0 pct_incidents_with_delay
        from silver_delays group by line_canonical""")
    con.execute("""create table delays_by_station as
        select station, count(*) incidents, sum(min_delay) total_delay_minutes,
               0.0 avg_delay_minutes_when_delayed from silver_delays
        where station <> '' group by station""")
    con.execute("""create table delays_by_hour as
        select event_hour, count(*) incidents, sum(min_delay) total_delay_minutes
        from silver_delays group by event_hour""")
    con.execute("""create table monthly_trend as
        select event_year_month, count(*) incidents, sum(min_delay) total_delay_minutes,
               0 incidents_with_delay from silver_delays group by event_year_month""")
    con.execute("""create table delays_by_code as
        select code_clean as code, code_description as description, count(*) incidents,
               sum(min_delay) total_delay_minutes from silver_delays group by code_clean, code_description""")
    return con


@pytest.fixture()
def exported(tmp_path, monkeypatch):
    monkeypatch.setattr(run_pipeline, "DASHBOARD_DATA_DIR", tmp_path / "dash")
    monkeypatch.setattr(run_pipeline, "GOLD_DIR", tmp_path / "gold")
    con = _warehouse()
    run_pipeline.export_dashboard(
        con,
        {
            "checks_passed": 0,
            "checks_total": 0,
            "run_started_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    con.close()
    dash = tmp_path / "dash"
    merged = {}
    for name in ["dashboard.json", "tables_by_line.json", "daily.json", "quarter.json"]:
        merged.update(json.loads((dash / name).read_text()))
    return merged


def test_export_includes_daily_and_breakdowns(exported):
    for key in ["daily_summary", "daily_by_line", "daily_fields",
                "totals_by_year_line", "monthly_by_line", "hour_by_year_line",
                "stations_by_year_line", "codes_by_year_line",
                "delays_by_line", "headline", "line_codes"]:
        assert key in exported, key
    assert len(exported["daily_summary"]) == 3  # three distinct dates in the fixture
    assert exported["line_codes"]["L1"] == "LINE_1_YONGE_UNIVERSITY"


def test_totals_by_year_line_reconcile_with_headline(exported):
    rows = exported["totals_by_year_line"]
    assert sum(r["incidents"] for r in rows) == exported["headline"]["incidents"] == 5
    assert sum(r["total_delay_minutes"] for r in rows) == exported["headline"]["total_delay_minutes"] == 25
    by_key = {(r["event_year"], r["line"]): r for r in rows}
    assert by_key[(2024, "L1")]["incidents"] == 2
    assert by_key[(2025, "L2")]["total_delay_minutes"] == 3


def test_breakdown_top_n_and_empty_station_rules(exported):
    stations = exported["stations_by_year_line"]
    assert all(r["station"] != "" for r in stations)  # blank station never ranked
    assert all(r["incidents"] >= 1 for r in stations)
    codes = {(r["event_year"], r["line"], r["code"]) for r in exported["codes_by_year_line"]}
    assert (2024, "L1", "S1") in codes


def test_daily_by_line_sums_to_daily_summary(exported):
    # Daily arrays: [epoch_day, incidents, delay] and [epoch_day, code, delay].
    daily = {day: delay for day, _inc, delay in exported["daily_summary"]}
    by_line: dict[int, int] = {}
    for day, _code, delay in exported["daily_by_line"]:
        by_line[day] = by_line.get(day, 0) + delay
    assert by_line == daily
    # Epoch day for 2024-01-02 is 19724; its all-lines delay is 10 minutes.
    assert daily[19724] == 10


def test_quarter_breakdowns_reconcile_and_decode(exported):
    # Quarter arrays: totals [year, quarter, line, incidents, delay, stations].
    totals = exported["totals_by_quarter_line"]
    assert sum(r[3] for r in totals) == exported["headline"]["incidents"] == 5
    assert sum(r[4] for r in totals) == exported["headline"]["total_delay_minutes"] == 25
    by_key = {(r[0], r[1], r[2]): r for r in totals}
    assert by_key[(2024, 1, "L1")][3] == 2  # both Jan 2024 Line 1 rows are Q1
    assert by_key[(2025, 1, "L2")][4] == 3
    # Station rows decode through the station_names index.
    names = exported["station_names"]
    decoded = {(r[0], r[1], r[2], names[r[3]]) for r in exported["stations_by_quarter_line"]}
    assert (2024, 1, "L1", "BLOOR STATION") in decoded
    # Code rows decode through the description lookup.
    assert exported["code_descriptions"]["S2"] == "DESC TWO"
    code_keys = {(r[0], r[1], r[2], r[3]) for r in exported["codes_by_quarter_line"]}
    assert (2025, 1, "L1", "S2") in code_keys
    # Hourly quarter rows cover the fixture's hours.
    hours = {(r[0], r[1], r[2], r[3]) for r in exported["hour_by_quarter_line"]}
    assert (2025, 1, "L1", 17) in hours


def test_freshness_badge_fields_are_parseable(exported):
    # The header freshness badge renders from these two fields; both must be
    # real parseable ISO values, never placeholders.
    run_started = datetime.fromisoformat(exported["run_report"]["run_started_utc"])
    assert run_started.tzinfo is not None
    max_date = date.fromisoformat(exported["headline"]["max_date"])
    assert max_date <= run_started.date()  # data never newer than the run
