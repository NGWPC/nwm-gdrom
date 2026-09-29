"""Regression test: our flat-array evaluator vs. the reference `rule2model.py`.

`rule2model.py` in `Scripts/` is the GDROM research team's reference
simulator — it builds Python functions on the fly via `exec()` and runs them
row-by-row against the cleaned daily CSVs. We reimplement the same semantics
on top of our packed float32 catalog; this test asserts the two agree.

We inline the oracle's `generate_ct_function` and `generate_module_functions`
logic so that importing `rule2model.py` wholesale (with its `#%%` run block)
does not execute side-effect code during test collection.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

import pandas as pd
import pytest

from nwm_gdrom.evaluator import simulate_release_dataframe
from nwm_gdrom.metadata import load_metadata
from nwm_gdrom.rule_catalog import GDROMCatalog, build_catalog

if TYPE_CHECKING:
    from pathlib import Path


# CSV-based regression: covers Res_R (41, 42, 44, 47) and Res_L (40). Res_M
# reservoirs have no training CSV in the GDROM v2 release (their rules were
# transferred from an analogous Res_R during training, without local fitting),
# so they are covered by the synthetic-grid regression below.
ORACLE_GRAND_IDS = [40, 41, 42, 44, 47]

# Synthetic-grid regression: covers all three GDROM categories. Adds a Res_M
# reservoir (1010) on top of the CSV set. Res_M coverage is necessary to
# substantiate the delivery report's "spans all three GDROM categories" claim,
# since real Res_M training rows do not exist.
SYNTHETIC_GRID_GRAND_IDS = [40, 41, 42, 44, 47, 1010]
ABS_TOL_ACRE_FT_PER_DAY = 1e-6


def _build_ct_function(grand_id: int, ct_path: Path) -> Callable[..., int | None] | None:
    """Build rule2model.py-style CT_<grand_id>(inflow, pdsi, doy, storage)."""
    ns: dict[str, object] = {}
    func_lines = [f"def CT_{grand_id}(inflow, pdsi, doy, storage):"]
    for raw in ct_path.read_text().splitlines():
        line = raw.strip()
        if not line.startswith("if"):
            continue
        line = (
            line.replace("Inflow", "inflow")
            .replace("PDSI", "pdsi")
            .replace("DOY", "doy")
            .replace("Storage", "storage")
        )
        m = re.search(r"then module:\s*(\d+)", line)
        if not m:
            continue
        condition = line.split("then")[0].strip()
        func_lines.append(f"    {condition}:")
        func_lines.append(f"        return {m.group(1)}")
    func_lines.append("    return None")
    exec("\n".join(func_lines), ns)
    return ns[f"CT_{grand_id}"]  # type: ignore[return-value]


def _build_module_function(
    grand_id: int, module_id: int, mf_path: Path
) -> Callable[..., float | None]:
    """Build rule2model.py-style module_<grand_id>_<module_id>(inflow, storage)."""
    func_name = f"module_{grand_id}_{module_id}"
    func_lines = [f"def {func_name}(inflow, storage):"]
    for raw in mf_path.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("Release ="):
            expr = (
                line.split("=", 1)[1]
                .strip()
                .replace("Inflow", "inflow")
                .replace("Storage", "storage")
            )
            func_lines.append(f"    return {expr}")
        elif "then Release:" in line:
            condition_part, value_part = line.split("then Release:")
            condition = (
                condition_part.strip()[3:]
                .strip()
                .replace("Inflow", "inflow")
                .replace("Storage", "storage")
            )
            func_lines.append(f"    if {condition}:")
            func_lines.append(f"        return {value_part.strip()}")
        elif line.startswith("Release:"):
            func_lines.append(f"    return {line.split(':', 1)[1].strip()}")
    local_ns: dict[str, object] = {}
    exec("\n".join(func_lines), local_ns)
    return local_ns[func_name]  # type: ignore[return-value]


def _build_oracle_functions(
    grand_id: int, modules_dir: Path, conditions_dir: Path
) -> tuple[Callable[..., int | None] | None, dict[int, Callable[..., float | None]]]:
    """Inline reproduction of rule2model.py's oracle function builders.

    Returns (ct_func, {module_id: module_func}). `ct_func` is None when the
    reservoir has no `module_conditions/{grand_id}.txt` file.
    """
    ct_path = conditions_dir / f"{grand_id}.txt"
    ct_func = _build_ct_function(grand_id, ct_path) if ct_path.exists() else None
    module_funcs = {
        int(mf.stem.split("_", 1)[1]): _build_module_function(
            grand_id, int(mf.stem.split("_", 1)[1]), mf
        )
        for mf in sorted(modules_dir.glob(f"{grand_id}_*.txt"))
    }
    return ct_func, module_funcs


def _oracle_simulate(
    grand_id: int, df: pd.DataFrame, modules_dir: Path, conditions_dir: Path
) -> list[float | None]:
    ct_func, module_funcs = _build_oracle_functions(grand_id, modules_dir, conditions_dir)
    fallback = module_funcs.get(0)
    out: list[float | None] = []
    for _, row in df.iterrows():
        inflow, storage, doy, pdsi = (
            float(row["Inflow"]),
            float(row["Storage"]),
            float(row["DOY"]),
            float(row["PDSI"]),
        )
        if ct_func is not None:
            mid = ct_func(inflow, pdsi, doy, storage)
            func = module_funcs.get(mid) if mid is not None else None
        else:
            func = fallback
        out.append(func(inflow, storage) if func is not None else None)
    return out


@pytest.fixture(scope="module")
def catalog(modules_dir: Path, conditions_dir: Path, metadata_csv: Path) -> GDROMCatalog:
    md = load_metadata(metadata_csv)
    return build_catalog(
        ORACLE_GRAND_IDS,
        modules_dir,
        conditions_dir,
        metadata=md,
        rule_version="test",
        crosswalk_version="test",
    )


@pytest.mark.parametrize("grand_id", ORACLE_GRAND_IDS)
def test_oracle_agrees_with_evaluator(
    grand_id: int,
    catalog: GDROMCatalog,
    modules_dir: Path,
    conditions_dir: Path,
    cleaned_ts_dir: Path,
) -> None:
    """Daily-by-daily cross-check against rule2model.py on real cleaned CSVs.

    With the catalog packed in float64 throughout (§2.4 of the delivery
    report), agreement is effectively bit-exact between our evaluator and
    the reference oracle. We allow a tolerance of one-millionth of an
    acre-foot per day (1e-6) to absorb any residual IEEE-754 rounding noise
    that could differ between addition orders. Any mismatch beyond that
    indicates a logic divergence and is reported with row-level context.
    """
    csv_path = cleaned_ts_dir / f"{grand_id}.csv"
    if not csv_path.is_file():
        pytest.skip(f"no cleaned CSV for grand_id {grand_id}")

    df = pd.read_csv(csv_path, parse_dates=["Date"])
    oracle = _oracle_simulate(grand_id, df, modules_dir, conditions_dir)
    ours = simulate_release_dataframe(catalog, grand_id, df)

    assert len(oracle) == len(ours)
    mismatches: list[str] = []
    abs_tol = 1e-6
    for i, (o, x) in enumerate(zip(oracle, ours, strict=True)):
        if o is None and x is None:
            continue
        if o is None or x is None:
            mismatches.append(f"row {i}: oracle={o}, ours={x}")
            continue
        if abs(float(o) - float(x)) > abs_tol:
            mismatches.append(
                f"row {i}: inflow={df.iloc[i]['Inflow']:.3f} "
                f"storage={df.iloc[i]['Storage']:.3f} "
                f"pdsi={df.iloc[i]['PDSI']:.3f} doy={df.iloc[i]['DOY']} "
                f"oracle={o:.6f} ours={x:.6f} diff={float(o) - float(x):+.4e}"
            )

    if mismatches:
        preview = "\n".join(mismatches[:10])
        total = len(mismatches)
        msg = f"{total}/{len(oracle)} rows disagree (tol={abs_tol}); first 10:\n{preview}"
        pytest.fail(msg)


def _make_synthetic_grid() -> pd.DataFrame:
    """Designed input grid spanning the GDROM input domain.

    Inflow and Storage span four decades each; PDSI spans the IEEE-physical
    range [-5, +5]; DOY samples a year at five points. Total: 4 x 4 x 5 x 5 =
    400 rows per reservoir. The grid is dense enough to exercise typical
    dispatcher branches and the rule-evaluation arithmetic while small enough
    to keep the suite fast.
    """
    rows: list[dict[str, object]] = [
        {
            "Date": pd.NaT,
            "Inflow": inflow,
            "Storage": storage,
            "DOY": doy,
            "PDSI": pdsi,
            "Release": math.nan,
        }
        for inflow in (0.0, 10.0, 1_000.0, 100_000.0)
        for storage in (0.0, 1_000.0, 100_000.0, 1_000_000.0)
        for pdsi in (-5.0, -2.0, 0.0, 2.0, 5.0)
        for doy in (1.0, 90.0, 180.0, 270.0, 365.0)
    ]
    return pd.DataFrame(rows)


def _values_agree(o: float | None, x: float | None) -> bool:
    """Pairwise agreement comparator that treats matching None / matching NaN as equal."""
    if o is None and x is None:
        return True
    if o is None or x is None:
        return False
    if math.isnan(o) and math.isnan(x):
        return True
    if math.isnan(o) or math.isnan(x):
        return False
    return abs(float(o) - float(x)) <= ABS_TOL_ACRE_FT_PER_DAY


@pytest.fixture(scope="module")
def synthetic_grid_catalog(
    modules_dir: Path, conditions_dir: Path, metadata_csv: Path
) -> GDROMCatalog:
    md = load_metadata(metadata_csv)
    return build_catalog(
        SYNTHETIC_GRID_GRAND_IDS,
        modules_dir,
        conditions_dir,
        metadata=md,
        rule_version="test",
        crosswalk_version="test",
    )


@pytest.mark.parametrize("grand_id", SYNTHETIC_GRID_GRAND_IDS)
def test_oracle_agrees_on_synthetic_grid(
    grand_id: int,
    synthetic_grid_catalog: GDROMCatalog,
    modules_dir: Path,
    conditions_dir: Path,
) -> None:
    """Synthetic-grid cross-check that covers all three GDROM categories.

    Necessary in addition to the CSV-based test because Res_M reservoirs have
    no training time series. The grid (4 x 4 x 5 x 5 = 400 rows) is replayed
    through the GDROM authors' reference simulator and through our evaluator;
    we assert agreement at every row, within 1e-6 acre-feet per day, with
    matching None values and matching NaN values both counted as agreement.
    """
    df = _make_synthetic_grid()
    oracle = _oracle_simulate(grand_id, df, modules_dir, conditions_dir)
    ours = simulate_release_dataframe(synthetic_grid_catalog, grand_id, df)

    assert len(oracle) == len(ours)
    mismatches: list[str] = []
    for i, (o, x) in enumerate(zip(oracle, ours, strict=True)):
        if _values_agree(o, x):
            continue
        row = df.iloc[i]
        mismatches.append(
            f"row {i}: inflow={row['Inflow']} storage={row['Storage']} "
            f"pdsi={row['PDSI']} doy={row['DOY']} "
            f"oracle={o} ours={x}"
        )
    if mismatches:
        preview = "\n".join(mismatches[:10])
        msg = (
            f"GRAND_ID {grand_id}: {len(mismatches)}/{len(oracle)} rows disagree "
            f"(tol={ABS_TOL_ACRE_FT_PER_DAY}); first 10:\n{preview}"
        )
        pytest.fail(msg)
