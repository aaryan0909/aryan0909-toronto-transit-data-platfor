# Latest pipeline run

- Started (UTC): 2026-09-30T18:03:23.162778+00:00
- Rows: bronze 71,942 -> silver 71,856
- Data quality: 10/10 checks passed

| Check | Layer | Result | Measured | Threshold |
|---|---|---|---|---|
| schema_conformance | bronze | PASS | 6/6 required columns present | all required columns present |
| timestamp_parse_rate | silver | PASS | 1.0 | >= 0.99 |
| station_null_rate | silver | PASS | 0.0 | <= 0.01 |
| non_negative_min_delay | silver | PASS | 0 | == 0 |
| code_reference_coverage | silver | PASS | 0.9755 | >= 0.9 |
| duplicate_rate | silver | PASS | 0.0 | <= 0.01 |
| canonical_line_rate | silver | PASS | 0.9965 | >= 0.9 |
| freshness | silver | PASS | max event_date 2026-08-31, 30 days before run date | <= 45 days |
| volume_anomaly | silver | PASS | 0.0112 | <= 0.6 |
| station_long_tail_rate | silver | PASS | 0.0252 | <= 0.1 |
