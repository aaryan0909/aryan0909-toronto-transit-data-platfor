# Design notes: Toronto Transit Data Platform

This document is the reasoning behind the code. It doubles as interview prep:
every section is a decision, the alternative that was rejected, and why.

## The thesis: honest numbers

A credible-looking wrong number is worse than no number. Three places in this
project exist purely to defend that:

1. **Bronze never cleans.** If a silver bug is found six months later, bronze
   can be replayed and the corrected number can be diffed against the old one.
2. **Messy values get explicit buckets, never guesses.** The raw `Line` column
   contains `YU/ BD`, `LINE 2 - BLOOR DANFORTH`, and even bus route numbers
   (`95 YORK MILLS`) inside a subway delay file. Those rows map to
   `MULTI_LINE_OR_NETWORK` or `UNKNOWN`, with the raw value preserved in
   `line_raw`. Splitting them proportionally or guessing a line would produce
   a plausible, wrong line-level total.
3. **Data-quality results ship with the data.** The dashboard's second page is
   the run report: thresholds, measured values, pass/fail. A consumer never
   has to take the headline number on faith.

## Why this dataset

TTC Subway Delay Data from Toronto Open Data (CKAN). It is real operational
data with real problems: two file formats (XLSX for 2024, CSV for 2025+),
inconsistent line codes, a separate code reference table that does not cover
every code used (97.6% coverage in the verified run), a double-encoded
UTF-8 reference file, a free-text station field with 1,489 distinct values
for a ~75-station subway, and a large share of zero-delay incident records. That last
point matters analytically: `avg(min_delay)` over all rows would understate
passenger impact, so gold models report average delay *when delayed*
alongside incident counts.

Scope choice: the pipeline ingests the 2024 XLSX and the 2025+ CSV
(71,942 bronze rows in the verified run). Older years are the same schema in
separate XLSX files; adding them is a one-line change to
`SOURCE_FILES` in `config.py`. Limiting scope kept the build verifiable end
to end instead of impressive on paper.

## Architecture choices

**DuckDB as the warehouse.** At ~72k rows, a cloud warehouse adds accounts,
cost and latency without changing a single design decision. DuckDB gives
real SQL, Parquet-native reads and window functions locally. The dbt project
is warehouse-shaped: if this moved to BigQuery or Databricks SQL, the models
change adapter, not logic.

**dbt for silver-to-gold.** Gold transforms are declarative SQL models with
names, descriptions and a DAG, which is how a team maintains them. The Python
pipeline loads the silver table into the same DuckDB file and invokes
`dbt run`; dbt is a pipeline step, not a separate manual ritual.

**Parquet medallion layout.** Bronze is partitioned by year/month. Partitioning
by day would create ~960 tiny partitions for a 1-5 MB dataset: the small-files
problem in miniature. Month partitions keep files meaningful while still
pruning by time. Silver is a single typed Parquet file (plus a rejects file);
gold is one Parquet file per model, exported for the dashboard as JSON.

**Idempotency by rebuild, at this scale.** Ingestion is incremental
(SHA-256 manifest, unchanged files skipped). Transforms are deterministic
full rebuilds, so a re-run cannot double-count. At 100x the data, the right
move is dbt incremental models keyed on `event_date` with a merge strategy,
plus partitioned silver appends. The seam for that change is exactly the
silver-to-gold boundary, which is why it is dbt and not ad-hoc Python.

**Checks as code with thresholds in one module.** The standalone
`honest_checks` package holds the check implementations; the TTC adapter
holds this dataset's thresholds, once. Volume anomaly compares the latest full month against a
trailing 6-month mean (the last month is excluded because it can be partial,
which would false-alarm every run). Freshness is 45 days because the source
publishes monthly; a daily threshold would cry wolf by design.

## Trade-offs I accepted

- **Full-refresh gold** over incremental: simpler and correct at this size;
  documented above where it changes.
- **Static JSON dashboard** over a query API: the dashboard reads one
  exported JSON file. No server, no credentials, deploys to Vercel as static
  output. The cost is freshness bounded by pipeline runs, which the
  data-quality page states on its face.
- **Two source years** over the full 2014-2026 history: see dataset scope
  above.

## What production would add

- An orchestrator (Dagster or Airflow) with retries, backfills and SLAs
  instead of a CLI entry point.
- The GitHub Actions workflow in `docs/github-actions-workflow.yml.txt`
  (the Muse GitHub App cannot write `.github/workflows/`; the file is
  ready to add manually) running tests and the pipeline on a schedule,
  opening an issue when a DQ check fails.
- Alerting on the run report (a failed freshness or volume check pages
  someone; today it is visible on the dashboard and in the report).
- A warehouse with access control and PII review. This dataset has none,
  but vehicle IDs would get a second look before any wider sharing.
- dbt tests (`not_null`, `accepted_values` on `line_canonical`) alongside
  the Python DQ suite, and data contracts with the publisher if this fed
  downstream teams.
