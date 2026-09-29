"""Tests for `nwm_gdrom.rule_catalog`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest

from nwm_gdrom.metadata import load_metadata
from nwm_gdrom.rule_catalog import (
    CatalogVersionMismatchError,
    GDROMCatalog,
    build_catalog,
    load_catalog,
    save_catalog,
)
from nwm_gdrom.rule_parser import ModuleKind

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(scope="module")
def small_catalog(modules_dir: Path, conditions_dir: Path, metadata_csv: Path) -> GDROMCatalog:
    """Build a catalog for a handful of reservoirs that cover all code paths."""
    md = load_metadata(metadata_csv)
    grand_ids = [40, 41, 42, 44, 47]  # dispatchers on 40/41/47; single-module on 42/44
    return build_catalog(
        grand_ids,
        modules_dir,
        conditions_dir,
        metadata=md,
        rule_version="test-2025.04",
        crosswalk_version="test-2025.04",
    )


def test_catalog_shapes_and_counts(small_catalog: GDROMCatalog) -> None:
    cat = small_catalog
    assert cat.n_reservoirs == 5
    expected_modules = {40: 4, 41: 2, 42: 1, 44: 1, 47: 3}
    for i, gid in enumerate(cat.grand_ids):
        assert cat.n_modules(i) == expected_modules[int(gid)]

    assert cat.has_dispatcher(0)  # 40
    assert cat.has_dispatcher(1)  # 41
    assert not cat.has_dispatcher(2)  # 42 (no .txt in module_conditions)
    assert not cat.has_dispatcher(3)  # 44
    assert cat.has_dispatcher(4)  # 47


def test_catalog_csr_invariants(small_catalog: GDROMCatalog) -> None:
    """CSR pointer arrays are monotonic and bracket the flat arrays correctly."""
    cat = small_catalog
    assert cat.reservoir_modules_start.shape == (cat.n_reservoirs + 1,)
    assert cat.reservoir_modules_start[0] == 0
    assert cat.reservoir_modules_start[-1] == cat.modules_kind.shape[0]
    assert np.all(np.diff(cat.reservoir_modules_start) >= 0)

    assert cat.modules_ptr.shape == (cat.modules_kind.shape[0] + 1,)
    assert cat.modules_ptr[0] == 0
    assert cat.modules_ptr[-1] == cat.modules_flat.shape[0]
    assert np.all(np.diff(cat.modules_ptr) > 0)

    assert cat.conditions_branch_start.shape == (cat.n_reservoirs + 1,)
    assert cat.conditions_branch_start[0] == 0
    assert cat.conditions_branch_start[-1] == cat.conditions_ptr.shape[0] - 1
    assert np.all(np.diff(cat.conditions_branch_start) >= 0)


def test_catalog_metadata_enrichment(small_catalog: GDROMCatalog) -> None:
    cat = small_catalog
    assert cat.grand_ids.tolist() == [40, 41, 42, 44, 47]
    assert np.all(cat.storage_cap_m3 > 0)
    assert cat.category_name(0) == "Res_L"  # grand 40 per reservoir_metadata.csv
    assert cat.category_name(1) == "Res_R"  # grand 41


def test_module_kind_distribution(small_catalog: GDROMCatalog) -> None:
    """Our 5-reservoir sample must exercise both module kinds."""
    kinds = {int(k) for k in small_catalog.modules_kind.tolist()}
    assert ModuleKind.EXPR in kinds
    assert ModuleKind.TREE in kinds


def test_expr_module_payload_is_four_floats(small_catalog: GDROMCatalog) -> None:
    """Every EXPR module packs exactly [a_inflow, a_storage, c, clamp_min]."""
    for m_idx, kind in enumerate(small_catalog.modules_kind.tolist()):
        if int(kind) != ModuleKind.EXPR:
            continue
        span = int(small_catalog.modules_ptr[m_idx + 1]) - int(small_catalog.modules_ptr[m_idx])
        assert span == 4, f"EXPR module at index {m_idx} has span {span}, expected 4"


def test_unsorted_grand_ids_raises(modules_dir: Path, conditions_dir: Path) -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        build_catalog(
            [41, 40], modules_dir, conditions_dir, rule_version="t", crosswalk_version="t"
        )


def test_missing_grand_id_raises(modules_dir: Path, conditions_dir: Path) -> None:
    with pytest.raises(FileNotFoundError, match="999999"):
        build_catalog(
            [999999], modules_dir, conditions_dir, rule_version="t", crosswalk_version="t"
        )


def test_empty_version_strings_raise(modules_dir: Path, conditions_dir: Path) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        build_catalog([40], modules_dir, conditions_dir, rule_version="", crosswalk_version="t")
    with pytest.raises(ValueError, match="non-empty"):
        build_catalog([40], modules_dir, conditions_dir, rule_version="t", crosswalk_version="")


def test_roundtrip_npz(small_catalog: GDROMCatalog, tmp_path: Path) -> None:
    out = tmp_path / "catalog.npz"
    save_catalog(small_catalog, out)
    assert out.is_file()

    loaded = load_catalog(out)

    def _eq(a: np.ndarray, b: np.ndarray) -> None:
        assert a.dtype == b.dtype
        np.testing.assert_array_equal(a, b)

    for fld in (
        "grand_ids",
        "state",
        "category",
        "storage_cap_m3",
        "min_storage_m3",
        "ood_inflow_p01_af",
        "ood_inflow_p99_af",
        "reservoir_modules_start",
        "modules_kind",
        "modules_ptr",
        "modules_flat",
        "conditions_branch_start",
        "conditions_ptr",
        "conditions_flat",
    ):
        _eq(getattr(loaded, fld), getattr(small_catalog, fld))
    assert loaded.rule_version == small_catalog.rule_version
    assert loaded.crosswalk_version == small_catalog.crosswalk_version


def test_default_optional_fields(small_catalog: GDROMCatalog) -> None:
    """When OOD/min_storage aren't supplied, defaults are sensible sentinels."""
    cat = small_catalog
    np.testing.assert_array_equal(cat.min_storage_m3, np.zeros(cat.n_reservoirs, np.float32))
    assert np.all(np.isneginf(cat.ood_inflow_p01_af))
    assert np.all(np.isposinf(cat.ood_inflow_p99_af))


def test_optional_fields_round_trip(
    modules_dir: Path,
    conditions_dir: Path,
    metadata_csv: Path,
    tmp_path: Path,
) -> None:
    """min_storage_m3 and OOD thresholds round-trip through save/load."""
    md = load_metadata(metadata_csv)
    grand_ids = [40, 41, 42, 44, 47]
    cat = build_catalog(
        grand_ids,
        modules_dir,
        conditions_dir,
        metadata=md,
        min_storage_m3={40: 1000.0, 41: 50000.0},
        ood_inflow_p01_af={40: -5.0, 41: 0.5},
        ood_inflow_p99_af={40: 800.0, 41: 9000.0},
        rule_version="v1",
        crosswalk_version="x1",
    )
    out = tmp_path / "c.npz"
    save_catalog(cat, out)
    loaded = load_catalog(out)
    np.testing.assert_array_equal(loaded.min_storage_m3, cat.min_storage_m3)
    np.testing.assert_array_equal(loaded.ood_inflow_p01_af, cat.ood_inflow_p01_af)
    np.testing.assert_array_equal(loaded.ood_inflow_p99_af, cat.ood_inflow_p99_af)


def test_load_rejects_version_mismatch(small_catalog: GDROMCatalog, tmp_path: Path) -> None:
    out = tmp_path / "v.npz"
    save_catalog(small_catalog, out)
    # Matching version → ok.
    load_catalog(out, expected_rule_version=small_catalog.rule_version)
    # Mismatched version → raises.
    with pytest.raises(CatalogVersionMismatchError, match="rule_version"):
        load_catalog(out, expected_rule_version="some-other-version")
    with pytest.raises(CatalogVersionMismatchError, match="crosswalk_version"):
        load_catalog(out, expected_crosswalk_version="some-other-version")


def test_load_rejects_missing_fields(tmp_path: Path) -> None:
    """A .npz with the catalog's required fields stripped must fail to load."""
    out = tmp_path / "broken.npz"
    np.savez(
        out,
        grand_ids=np.asarray([40], dtype=np.int64),
        rule_version=np.asarray("v1"),
        crosswalk_version=np.asarray("x1"),
    )
    with pytest.raises(ValueError, match="missing required fields"):
        load_catalog(out)


def test_state_field_populated_from_metadata(small_catalog: GDROMCatalog) -> None:
    """STATE column from reservoir_metadata.csv should land in the catalog."""
    # All five test reservoirs are in Washington (per reservoir_metadata.csv).
    assert all(s == b"WA" for s in small_catalog.state.tolist())
