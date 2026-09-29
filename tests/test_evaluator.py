"""Tests for `nwm_gdrom.evaluator` on synthetic and small real catalogs."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from nwm_gdrom.evaluator import (
    evaluate_module,
    find_reservoir_index,
    simulate_release,
    walk_condition_tree,
)
from nwm_gdrom.metadata import load_metadata
from nwm_gdrom.rule_catalog import build_catalog

if TYPE_CHECKING:
    from pathlib import Path

    from nwm_gdrom.rule_catalog import GDROMCatalog


@pytest.fixture(scope="module")
def catalog(modules_dir: Path, conditions_dir: Path, metadata_csv: Path) -> GDROMCatalog:
    md = load_metadata(metadata_csv)
    gids = [40, 41, 42, 44, 47]
    return build_catalog(
        gids,
        modules_dir,
        conditions_dir,
        metadata=md,
        rule_version="test",
        crosswalk_version="test",
    )


def test_find_reservoir_index(catalog: GDROMCatalog) -> None:
    assert find_reservoir_index(catalog, 40) == 0
    assert find_reservoir_index(catalog, 47) == 4
    with pytest.raises(KeyError):
        find_reservoir_index(catalog, 99999)


def test_single_module_reservoir_is_deterministic(catalog: GDROMCatalog) -> None:
    """Res 42 has a single module; releases are a pure function of inputs only."""
    idx = find_reservoir_index(catalog, 42)
    inputs = (100.0, 1000.0, 0.0, 1.0)
    r1 = simulate_release(catalog, idx, *inputs)
    r2 = simulate_release(catalog, idx, *inputs)
    assert r1 is not None
    assert r1 == r2


def test_expr_module_constant_release(catalog: GDROMCatalog, modules_dir: Path) -> None:
    """Module 40_3 is `Release: 95.8320` (EXPR CONSTANT) per §6.1."""
    # Index of 40_3 = reservoir_modules_start[res_idx_for_40] + 3
    res_idx = find_reservoir_index(catalog, 40)
    abs_idx_mod3 = int(catalog.reservoir_modules_start[res_idx]) + 3
    assert int(catalog.modules_kind[abs_idx_mod3]) == 0  # EXPR kind
    # Different inputs should all produce the same constant value.
    from nwm_gdrom.evaluator import evaluate_module

    v1 = evaluate_module(catalog, abs_idx_mod3, inflow=0.5, storage=100.0)
    v2 = evaluate_module(catalog, abs_idx_mod3, inflow=99999.0, storage=999999.0)
    assert v1 == pytest.approx(95.8320)
    assert v1 == pytest.approx(v2)


def test_tree_module_dispatch_first_match_wins(catalog: GDROMCatalog) -> None:
    """Res 41 module 0 selects between inflow <= 9225.8 and above (see §6.1)."""
    idx = find_reservoir_index(catalog, 41)
    abs_idx = int(catalog.reservoir_modules_start[idx]) + 0
    # Inflow <= 9225.8 → Release: 4333.5
    assert evaluate_module(catalog, abs_idx, inflow=100.0, storage=0.0) == pytest.approx(4333.5)
    # Inflow > 9225.8 AND Storage <= 956320.7 → Release: 2716.9
    assert evaluate_module(catalog, abs_idx, inflow=10000.0, storage=500000.0) == pytest.approx(
        2716.9
    )
    # Inflow > 9225.8 AND Storage > 956320.7 → Release: 5666.7
    assert evaluate_module(catalog, abs_idx, inflow=10000.0, storage=2_000_000.0) == pytest.approx(
        5666.7
    )


def test_single_module_reservoir_bypasses_dispatcher(catalog: GDROMCatalog) -> None:
    """Res 42 has no CART — dispatch must always pick module 0."""
    idx = find_reservoir_index(catalog, 42)
    assert not catalog.has_dispatcher(idx)
    assert walk_condition_tree(catalog, idx, 0.0, 0.0, 0.0, 1.0) is None
    # simulate_release routes around the (missing) dispatcher.
    assert simulate_release(catalog, idx, 0.0, 0.0, 0.0, 1.0) is not None


def test_empty_predicate_branch_never_matches(
    modules_dir: Path, conditions_dir: Path, tmp_path: Path
) -> None:
    """Synthetic dispatcher with `if () then module: 0` must never fire (Python `if ():`)."""
    # Create a synthetic 2-module reservoir whose dispatcher's only branch is
    # empty-predicate. Walk should return None, simulate_release returns None.
    syn_modules = tmp_path / "modules"
    syn_modules.mkdir()
    (syn_modules / "1.txt").write_text("")  # placeholder so glob finds it
    (syn_modules / "1_0.txt").write_text("Release: 1.0\n")
    (syn_modules / "1_1.txt").write_text("Release: 2.0\n")
    syn_cond = tmp_path / "conditions"
    syn_cond.mkdir()
    (syn_cond / "1.txt").write_text("if () then module: 0\n")

    cat = build_catalog([1], syn_modules, syn_cond, rule_version="test", crosswalk_version="test")
    idx = find_reservoir_index(cat, 1)
    assert cat.has_dispatcher(idx)
    # Dispatcher's only branch never matches → simulate_release returns None
    # (mirrors rule2model.py: ct_func returns None, downstream lookup is None).
    assert simulate_release(cat, idx, 0.0, 0.0, 0.0, 1.0) is None


def test_max_clamp_terminal_in_tree(tmp_path: Path) -> None:
    """A TREE branch with `max(linear, 0)` must clamp negative outputs to zero."""
    syn_modules = tmp_path / "modules"
    syn_modules.mkdir()
    body = (
        "if (Storage <= 100.0) then Release: max(1.0 * Storage + -200.0, 0)\n"
        "if (Storage > 100.0) then Release: 50.0\n"
    )
    (syn_modules / "5_0.txt").write_text(body)
    syn_cond = tmp_path / "conditions"
    syn_cond.mkdir()
    cat = build_catalog([5], syn_modules, syn_cond, rule_version="test", crosswalk_version="test")
    idx = find_reservoir_index(cat, 5)
    # Storage=50 → 1*50 - 200 = -150 → clamped to 0.
    assert simulate_release(cat, idx, 0.0, 50.0, 0.0, 1.0) == pytest.approx(0.0)
    # Storage=80 → 1*80 - 200 = -120 → still clamped.
    assert simulate_release(cat, idx, 0.0, 80.0, 0.0, 1.0) == pytest.approx(0.0)
    # Storage=150 → second branch: constant 50.0.
    assert simulate_release(cat, idx, 0.0, 150.0, 0.0, 1.0) == pytest.approx(50.0)


def test_nan_release_propagates(tmp_path: Path) -> None:
    """`Release: nan` must yield NaN at evaluation (matches rule2model.py)."""
    import math

    syn_modules = tmp_path / "modules"
    syn_modules.mkdir()
    (syn_modules / "9_0.txt").write_text("Release: nan\n")
    syn_cond = tmp_path / "conditions"
    syn_cond.mkdir()
    cat = build_catalog([9], syn_modules, syn_cond, rule_version="test", crosswalk_version="test")
    idx = find_reservoir_index(cat, 9)
    out = simulate_release(cat, idx, 100.0, 1000.0, 0.0, 1.0)
    assert out is not None
    assert math.isnan(out)


def test_pdsi_boundary_threshold_pins_float64_decision(catalog: GDROMCatalog) -> None:
    """Pin: thresholds compared at float64 precision, never float32-rounded.

    Res 40, doy=213, storage=238991, inflow=0.837, pdsi=-2.670: this exact row
    drove the float32→float64 catalog change. The dispatcher must select
    module 3 (Release: 95.8320) here. Float32 rounding of `-2.67` would flip
    the `pdsi <= -2.67` predicate and route to module 0 (Release: 35.0377).
    """
    idx = find_reservoir_index(catalog, 40)
    release = simulate_release(
        catalog, idx, inflow=0.837, storage=238991.158, pdsi=-2.670, doy=213.0
    )
    assert release == pytest.approx(95.8320)


def test_cart_walks_all_branches(catalog: GDROMCatalog) -> None:
    """Sweep a grid of (PDSI, DOY) inputs through Res 41 and assert we hit ≥2 modules."""
    idx = find_reservoir_index(catalog, 41)
    hit: set[int] = set()
    for pdsi in (-4.0, -2.0, 0.0, 2.0, 4.0):
        for doy in (10, 60, 100, 150, 200, 250, 320, 360):
            mid = walk_condition_tree(catalog, idx, 5000.0, 500000.0, pdsi, float(doy))
            if mid is not None:
                hit.add(mid)
    assert hit >= {0, 1}, f"expected CART to dispatch to multiple modules, saw {hit}"
