"""Tests for `nwm_gdrom.metadata`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import pytest

from nwm_gdrom.metadata import ACRE_FT_TO_M3, load_metadata

if TYPE_CHECKING:
    from pathlib import Path


def test_load_metadata_basic(metadata_csv: Path) -> None:
    df = load_metadata(metadata_csv)
    assert df.index.name == "GRAND_ID"
    assert df.index.is_monotonic_increasing
    assert not df.index.duplicated().any()
    assert 40 in df.index
    assert df.loc[40, "CATEGORY"] == "Res_L"
    assert df.loc[41, "CATEGORY"] == "Res_R"
    # STORAGE_CAP is in acre-feet; just sanity-check positivity.
    assert df.loc[40, "STORAGE_CAP"] > 0


def test_acre_ft_to_m3_constant() -> None:
    # 1 acre-ft ≈ 1233.48 m³, the conversion build_catalog applies to STORAGE_CAP.
    assert ACRE_FT_TO_M3 == pytest.approx(1233.48)


def test_load_rejects_bad_category(tmp_path: Path) -> None:
    csv = tmp_path / "bad.csv"
    csv.write_text("GRAND_ID,CATEGORY,STORAGE_CAP\n40,Res_X,100.0\n")
    with pytest.raises(ValueError, match="unknown CATEGORY"):
        load_metadata(csv)


def test_load_rejects_duplicate_grand_id(tmp_path: Path) -> None:
    csv = tmp_path / "dup.csv"
    csv.write_text("GRAND_ID,CATEGORY,STORAGE_CAP\n40,Res_R,1.0\n40,Res_L,2.0\n")
    with pytest.raises(ValueError, match="duplicate"):
        load_metadata(csv)


def test_load_rejects_missing_required(tmp_path: Path) -> None:
    csv = tmp_path / "missing.csv"
    csv.write_text("GRAND_ID,CATEGORY\n40,Res_R\n")
    with pytest.raises(ValueError, match="STORAGE_CAP"):
        load_metadata(csv)


def test_load_handles_missing_module_number(tmp_path: Path) -> None:
    csv = tmp_path / "no_module.csv"
    csv.write_text("GRAND_ID,CATEGORY,STORAGE_CAP\n40,Res_R,100.0\n")
    df = load_metadata(csv)
    assert "MODULE_NUMBER" not in df.columns or pd.isna(df.loc[40, "MODULE_NUMBER"])
