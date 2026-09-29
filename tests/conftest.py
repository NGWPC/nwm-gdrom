"""Shared test fixtures.

The repo ships a small fixture set under `tests/data/` covering a representative
subset of reservoirs (currently 12, drawn from all three GDROM categories and
spanning both module kinds; sufficient for unit + oracle regression tests).
Tests that need the full GDROM v2 corpus should be marked with
`@pytest.mark.fulldata`; they will skip unless the bulk dataset has been
downloaded under `./data/` via the `pixi run download-data` task.

Layout (under `tests/data/`):
  reservoir_metadata.csv
  modules/{grand_id}_{module_id}.txt
  module_conditions/{grand_id}.txt
  timeseries/{grand_id}.csv

Layout (under `./data/`, after `pixi run download-data`):
  rule_files/Operation Rules - GDROMs/modules/...
  rule_files/Operation Rules - GDROMs/module_conditions/...
  time_series/cleaned data for Res-R & Res-L/...
  reservoir_metadata.csv
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = _REPO_ROOT / "tests" / "data"
_FULLDATA = _REPO_ROOT / "data"


def _require_dir(p: Path) -> Path:
    if not p.is_dir():
        pytest.fail(f"required data directory missing: {p}")
    return p


def _require_file(p: Path) -> Path:
    if not p.is_file():
        pytest.fail(f"required data file missing: {p}")
    return p


# ---------------------------------------------------------------------------
# Small in-repo fixtures (always available)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return _REPO_ROOT


@pytest.fixture(scope="session")
def modules_dir() -> Path:
    return _require_dir(_FIXTURES / "modules")


@pytest.fixture(scope="session")
def conditions_dir() -> Path:
    return _require_dir(_FIXTURES / "module_conditions")


@pytest.fixture(scope="session")
def metadata_csv() -> Path:
    return _require_file(_FIXTURES / "reservoir_metadata.csv")


@pytest.fixture(scope="session")
def cleaned_ts_dir() -> Path:
    return _require_dir(_FIXTURES / "timeseries")


# ---------------------------------------------------------------------------
# Full-corpus fixtures (require `pixi run download-data` first)


@pytest.fixture(scope="session")
def full_modules_dir() -> Path:
    p = _FULLDATA / "rule_files" / "Operation Rules - GDROMs" / "modules"
    if not p.is_dir():
        pytest.skip(f"full GDROM v2 dataset not present at {p}; run `pixi run download-data`")
    return p


@pytest.fixture(scope="session")
def full_conditions_dir() -> Path:
    p = _FULLDATA / "rule_files" / "Operation Rules - GDROMs" / "module_conditions"
    if not p.is_dir():
        pytest.skip(f"full GDROM v2 dataset not present at {p}; run `pixi run download-data`")
    return p


@pytest.fixture(scope="session")
def full_timeseries_dir() -> Path:
    p = _FULLDATA / "time_series" / "cleaned data for Res-R & Res-L"
    if not p.is_dir():
        pytest.skip(f"full GDROM v2 dataset not present at {p}; run `pixi run download-data`")
    return p


@pytest.fixture(scope="session")
def full_metadata_csv() -> Path:
    p = _FULLDATA / "reservoir_metadata.csv"
    if not p.is_file():
        pytest.skip(f"full GDROM v2 dataset not present at {p}; run `pixi run download-data`")
    return p
