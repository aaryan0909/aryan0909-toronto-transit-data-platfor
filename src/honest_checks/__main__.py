"""CLI: python -m honest_checks --input data.parquet --config checks.json [--out report.json]

Exits 1 when any check fails, so it can gate a CI job or a pipeline run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core import load_dataset, run_checks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="honest_checks", description="Config-driven data-quality checks.")
    parser.add_argument("--input", required=True, help="Dataset file: .parquet, .csv, or .duckdb")
    parser.add_argument("--table", help="Table name (required for .duckdb inputs)")
    parser.add_argument("--config", required=True, help="JSON config file (see README.md)")
    parser.add_argument("--out", help="Write the JSON report here (default: stdout)")
    args = parser.parse_args(argv)

    config = json.loads(Path(args.config).read_text())
    df = load_dataset(args.input, table=args.table)
    results = run_checks(df, config)
    report = {
        "dataset": config.get("dataset_name", args.input),
        "rows": int(len(df)),
        "checks_passed": sum(1 for c in results if c.passed),
        "checks_total": len(results),
        "checks": [c.to_dict() for c in results],
    }
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(text)
    else:
        print(text)
    return 0 if report["checks_passed"] == report["checks_total"] else 1


if __name__ == "__main__":
    sys.exit(main())
