"""Tests for `nwm_gdrom.timeseries.compute_ood_thresholds`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import pytest

from nwm_gdrom.timeseries import compute_ood_thresholds

if TYPE_CHECKING:
    from pathlib import Path


def test_compute_ood_thresholds_real_data(cleaned_ts_dir: Path) -> None:
    """Compute P01/P99 from the cleaned daily CSVs for known Res_R reservoirs."""
    df = compute_ood_thresholds(cleaned_ts_dir, [40, 41, 42, 44, 47])
    assert list(df.index) == [40, 41, 42, 44, 47]
    assert "p_low" in df.columns
    assert "p_high" in df.columns
    # P_low must always be <= P_high.
    assert (df["p_low"] <= df["p_high"]).all()


def test_compute_ood_thresholds_skips_missing_csvs(cleaned_ts_dir: Path) -> None:
    """A grand_id with no CSV is silently dropped from the result."""
    df = compute_ood_thresholds(cleaned_ts_dir, [40, 999999, 41])
    assert 999999 not in df.index
    assert {40, 41} <= set(df.index)


def test_compute_ood_thresholds_custom_percentiles(cleaned_ts_dir: Path) -> None:
    df_default = compute_ood_thresholds(cleaned_ts_dir, [41])
    df_tighter = compute_ood_thresholds(
        cleaned_ts_dir, [41], low_percentile=0.05, high_percentile=0.95
    )
    # Tighter quantile bounds → narrower range.
    assert df_tighter.loc[41, "p_low"] >= df_default.loc[41, "p_low"]
    assert df_tighter.loc[41, "p_high"] <= df_default.loc[41, "p_high"]


def test_compute_ood_thresholds_validates_percentiles(cleaned_ts_dir: Path) -> None:
    with pytest.raises(ValueError, match="percentiles"):
        compute_ood_thresholds(cleaned_ts_dir, [40], low_percentile=0.5, high_percentile=0.5)
    with pytest.raises(ValueError, match="percentiles"):
        compute_ood_thresholds(cleaned_ts_dir, [40], low_percentile=-0.1, high_percentile=0.99)


def test_compute_ood_thresholds_empty_input(cleaned_ts_dir: Path) -> None:
    df = compute_ood_thresholds(cleaned_ts_dir, [])
    assert df.empty
    assert list(df.columns) == ["p_low", "p_high"]


def test_compute_ood_thresholds_all_missing(tmp_path: Path) -> None:
    df = compute_ood_thresholds(tmp_path, [40, 41])
    assert isinstance(df, pd.DataFrame)
    assert df.empty
