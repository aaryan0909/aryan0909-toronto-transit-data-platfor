"""honest_checks: explicit, config-driven data-quality checks.

Standalone package. It depends only on pandas (+ duckdb for .duckdb inputs)
and knows nothing about any particular pipeline: a dataset is a DataFrame,
expectations are a plain dict (JSON on disk for the CLI), and the output is
a list of CheckResult with the measured value, the threshold, and a
pass/fail. See README.md in this directory for the config format.

Philosophy: a credible-looking wrong number is worse than no number, so a
failed check is reported with its measured value, never hidden.
"""
from .core import CheckResult, load_dataset, run_checks

__all__ = ["CheckResult", "load_dataset", "run_checks"]
__version__ = "0.1.0"
