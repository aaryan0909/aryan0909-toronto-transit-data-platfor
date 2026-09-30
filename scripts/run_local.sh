#!/usr/bin/env bash
# One-command local run: install, test, pipeline.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest -q
python -m ttc_platform.run_pipeline
