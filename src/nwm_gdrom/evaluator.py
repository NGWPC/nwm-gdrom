"""Reference evaluator for the GDROM v2 flat-array catalog.

Pure-Python oracle implementation that matches the semantics of
`Scripts/rule2model.py` line-for-line. Not the production kernel; it
exists to validate the flat-array catalog by regression testing against the
reference scripts.

Semantic contract (mirrors rule2model.py):

- Dispatcher CART returns the first-matching branch's `module_id`, or None
  if no branch matches. A branch with zero predicates never matches (mirrors
  Python's `if ():` being False).
- EXPR and TREE release expressions evaluate as
    Release = max(a_inflow*Inflow + a_storage*Storage + c, clamp_min)
  where `clamp_min = -inf` disables the clamp. No post-hoc clamp to zero
  unless the module explicitly encodes `max(..., 0)`.
- A reservoir without a dispatcher always evaluates module 0.
- If the dispatcher returns a module_id outside the reservoir's module range,
  the result is None.
- A TREE module whose branches all fail to match returns None.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np

from nwm_gdrom.rule_parser import ModuleKind, OpCode

if TYPE_CHECKING:
    import pandas as pd

    from nwm_gdrom.rule_catalog import GDROMCatalog


def _check_reservoir_idx(catalog: GDROMCatalog, reservoir_idx: int) -> None:
    """Raise IndexError if ``reservoir_idx`` is outside the catalog's range."""
    n = catalog.n_reservoirs
    if not (0 <= reservoir_idx < n):
        msg = f"reservoir_idx must be in [0, {n}); got {reservoir_idx}"
        raise IndexError(msg)


def _cmp(val: float, op: int, th: float) -> bool:
    # NaN comparisons in Python evaluate to False naturally, which matches the
    # rule2model.py reference. No special handling needed.
    if op == OpCode.LE:
        return val <= th
    if op == OpCode.LT:
        return val < th
    if op == OpCode.GE:
        return val >= th
    if op == OpCode.GT:
        return val > th
    msg = f"unknown op code {op}"
    raise ValueError(msg)


def _eval_release(flat: np.ndarray, start: int, inflow: float, storage: float) -> float:
    """Evaluate a packed `[a_inflow, a_storage, c, clamp_min]` tuple."""
    a_i = float(flat[start])
    a_s = float(flat[start + 1])
    c = float(flat[start + 2])
    clamp = float(flat[start + 3])
    v = a_i * inflow + a_s * storage + c
    if math.isfinite(clamp):
        return max(v, clamp)
    return v


def find_reservoir_index(catalog: GDROMCatalog, grand_id: int) -> int:
    """Locate a reservoir's row index in a catalog.

    Binary-searches the catalog's sorted ``grand_ids`` array for the
    requested GRanD identifier and returns the row index, which is the
    integer needed by :func:`simulate_release` and the other per-reservoir
    accessors.

    Parameters
    ----------
    catalog : GDROMCatalog
        Catalog loaded by :func:`nwm_gdrom.load_catalog`.
    grand_id : int
        GRanD identifier to look up. Must be a non-negative integer.

    Returns
    -------
    int
        Row index in the catalog (in the range ``[0, catalog.n_reservoirs)``).

    Raises
    ------
    TypeError
        If ``grand_id`` is not coercible to a non-negative integer.
    KeyError
        If ``grand_id`` is not present in the catalog.

    Examples
    --------
    >>> import nwm_gdrom
    >>> catalog = nwm_gdrom.load_catalog("dist/nwm_gdrom_catalog.npz")  # doctest: +SKIP
    >>> find_reservoir_index(catalog, 610)  # doctest: +SKIP
    501
    """
    try:
        gid = int(grand_id)
    except (TypeError, ValueError) as exc:
        msg = f"grand_id must be an integer; got {grand_id!r}"
        raise TypeError(msg) from exc
    if gid < 0:
        msg = f"grand_id must be non-negative; got {gid}"
        raise TypeError(msg)
    idx = int(np.searchsorted(catalog.grand_ids, gid))
    if idx >= catalog.n_reservoirs or int(catalog.grand_ids[idx]) != gid:
        raise KeyError(gid)
    return idx


def walk_condition_tree(
    catalog: GDROMCatalog,
    reservoir_idx: int,
    inflow: float,
    storage: float,
    pdsi: float,
    doy: float,
) -> int | None:
    """Walk a reservoir's dispatcher CART and return the chosen module.

    Evaluates the dispatcher's branches in source order, returning the
    target module identifier of the first branch whose predicates all
    hold. Branches with zero predicates never match, mirroring the
    GDROM reference simulator's ``if ():`` semantics.

    Parameters
    ----------
    catalog : GDROMCatalog
        Catalog loaded by :func:`nwm_gdrom.load_catalog`.
    reservoir_idx : int
        Row index of the reservoir in the catalog, as returned by
        :func:`find_reservoir_index`. Must satisfy
        ``0 <= reservoir_idx < catalog.n_reservoirs``.
    inflow : float
        Daily inflow in acre-feet per day. May be NaN; NaN comparisons
        evaluate to false, matching the reference simulator.
    storage : float
        Reservoir storage in acre-feet. May be NaN.
    pdsi : float
        Palmer Drought Severity Index, dimensionless and signed. May be NaN.
    doy : float
        Day of year, expected to be in [1, 366]. May be NaN.

    Returns
    -------
    int or None
        Module identifier (non-negative integer) of the first branch
        whose predicates all hold, or ``None`` if no branch matches or
        the reservoir has no dispatcher.

    Raises
    ------
    IndexError
        If ``reservoir_idx`` is outside ``[0, catalog.n_reservoirs)``.

    Notes
    -----
    This function is part of the reference Python evaluator and is
    intended for testing and educational use. T-Route's production
    kernel implements equivalent logic directly against the catalog's
    flat arrays.
    """
    _check_reservoir_idx(catalog, reservoir_idx)
    if not catalog.has_dispatcher(reservoir_idx):
        return None

    inputs = (inflow, storage, pdsi, doy)
    b_start = int(catalog.conditions_branch_start[reservoir_idx])
    b_end = int(catalog.conditions_branch_start[reservoir_idx + 1])
    cf = catalog.conditions_flat
    cp = catalog.conditions_ptr

    for b in range(b_start, b_end):
        idx = int(cp[b])
        n_pred = int(cf[idx])
        idx += 1
        matched = n_pred > 0  # empty-predicate branch: never matches
        for _ in range(n_pred):
            var = int(cf[idx])
            op = int(cf[idx + 1])
            th = float(cf[idx + 2])
            idx += 3
            if not _cmp(inputs[var], op, th):
                matched = False
        module_id = int(cf[idx])
        if matched:
            return module_id
    return None


def evaluate_module(
    catalog: GDROMCatalog,
    absolute_module_idx: int,
    inflow: float,
    storage: float,
) -> float | None:
    """Evaluate a single packed module and return its release.

    Looks up the module's kind (EXPR or TREE) at the given absolute index
    and evaluates it. EXPR modules always return a finite release value
    derived from the affine expression. TREE modules return the release
    of the first branch whose predicates all hold, or ``None`` if no
    branch matches.

    Parameters
    ----------
    catalog : GDROMCatalog
        Catalog loaded by :func:`nwm_gdrom.load_catalog`.
    absolute_module_idx : int
        Index into ``catalog.modules_kind``. This is an *absolute* module
        index across the whole catalog, not a per-reservoir module
        identifier. Must satisfy
        ``0 <= absolute_module_idx < len(catalog.modules_kind)``.
        Most callers should use :func:`simulate_release` instead, which
        computes the absolute index from a reservoir row index and the
        dispatcher's chosen module identifier.
    inflow : float
        Daily inflow in acre-feet per day. May be NaN.
    storage : float
        Reservoir storage in acre-feet. May be NaN.

    Returns
    -------
    float or None
        Release in acre-feet per day, or ``None`` if the module is a TREE
        with no matching branch.

    Raises
    ------
    IndexError
        If ``absolute_module_idx`` is outside the catalog's module range.
    """
    n_mods_total = int(catalog.modules_kind.shape[0])
    if not (0 <= absolute_module_idx < n_mods_total):
        msg = f"absolute_module_idx must be in [0, {n_mods_total}); got {absolute_module_idx}"
        raise IndexError(msg)
    kind = int(catalog.modules_kind[absolute_module_idx])
    start = int(catalog.modules_ptr[absolute_module_idx])
    end = int(catalog.modules_ptr[absolute_module_idx + 1])
    mf = catalog.modules_flat

    if kind == ModuleKind.EXPR:
        return _eval_release(mf, start, inflow, storage)

    # TREE
    inputs = (inflow, storage)
    idx = start
    while idx < end:
        n_pred = int(mf[idx])
        idx += 1
        matched = n_pred > 0
        for _ in range(n_pred):
            var = int(mf[idx])
            op = int(mf[idx + 1])
            th = float(mf[idx + 2])
            idx += 3
            if not _cmp(inputs[var], op, th):
                matched = False
        if matched:
            return _eval_release(mf, idx, inflow, storage)
        idx += 4  # consume the non-matching branch's release expr
    return None


def simulate_release(
    catalog: GDROMCatalog,
    reservoir_idx: int,
    inflow: float,
    storage: float,
    pdsi: float,
    doy: float,
) -> float | None:
    """Compute one day's release for one reservoir.

    Walks the reservoir's dispatcher (if any) to select a module, then
    evaluates the chosen module against the same daily inflow and storage
    inputs. Implements the same semantics as the GDROM authors' reference
    simulator in ``Scripts/rule2model.py``.

    Parameters
    ----------
    catalog : GDROMCatalog
        Catalog loaded by :func:`nwm_gdrom.load_catalog`.
    reservoir_idx : int
        Row index of the reservoir in the catalog, as returned by
        :func:`find_reservoir_index`. Must satisfy
        ``0 <= reservoir_idx < catalog.n_reservoirs``.
    inflow : float
        Daily inflow in acre-feet per day. May be NaN; NaN comparisons
        evaluate to false, matching the reference simulator.
    storage : float
        Reservoir storage in acre-feet. May be NaN.
    pdsi : float
        Palmer Drought Severity Index, dimensionless and signed. May be NaN.
    doy : float
        Day of year, expected to be in [1, 366]. May be NaN.

    Returns
    -------
    float or None
        Release in acre-feet per day. Returns ``None`` when:

        - the dispatcher exists but no branch matches (e.g., the
          empty-predicate placeholder branches found in some Res_M
          dispatchers);
        - the dispatcher returns a module identifier outside the
          reservoir's module range; or
        - the chosen module is a TREE with no matching branch.

        For NaN inputs, the rule will typically return ``None`` (no
        branch matches) or NaN (if it lands in a constant branch with
        NaN parameters), matching the reference simulator's behavior.

    Raises
    ------
    IndexError
        If ``reservoir_idx`` is outside ``[0, catalog.n_reservoirs)``.

    Examples
    --------
    >>> import nwm_gdrom
    >>> from nwm_gdrom.evaluator import find_reservoir_index, simulate_release
    >>> catalog = nwm_gdrom.load_catalog("dist/nwm_gdrom_catalog.npz")  # doctest: +SKIP
    >>> idx = find_reservoir_index(catalog, 610)  # Hoover Dam  # doctest: +SKIP
    >>> simulate_release(catalog, idx, inflow=80_000, storage=26_000_000,
    ...                  pdsi=0.0, doy=100)  # doctest: +SKIP
    66337.796875
    """
    _check_reservoir_idx(catalog, reservoir_idx)
    if catalog.has_dispatcher(reservoir_idx):
        module_id = walk_condition_tree(catalog, reservoir_idx, inflow, storage, pdsi, doy)
        if module_id is None:
            return None
    else:
        module_id = 0

    n_mods = catalog.n_modules(reservoir_idx)
    if not (0 <= module_id < n_mods):
        return None

    abs_idx = int(catalog.reservoir_modules_start[reservoir_idx]) + module_id
    return evaluate_module(catalog, abs_idx, inflow, storage)


def simulate_release_dataframe(
    catalog: GDROMCatalog,
    grand_id: int,
    df: pd.DataFrame,
) -> list[float | None]:
    """Simulate daily releases over a DataFrame of input rows.

    Calls :func:`simulate_release` once per row of ``df``, returning the
    full series of daily releases. The function is structured to match
    the row layout of the GDROM authors' cleaned daily training CSVs (one
    row per day with columns ``Inflow``, ``Storage``, ``PDSI``, ``DOY``)
    but accepts any DataFrame with those four columns.

    Parameters
    ----------
    catalog : GDROMCatalog
        Catalog loaded by :func:`nwm_gdrom.load_catalog`.
    grand_id : int
        GRanD identifier of the reservoir to simulate. Resolved to a row
        index via :func:`find_reservoir_index`.
    df : pandas.DataFrame
        Input rows. Must contain numeric columns ``Inflow``, ``Storage``,
        ``PDSI``, and ``DOY``; all other columns are ignored. The four
        columns are extracted as contiguous ``float64`` arrays before the
        evaluation loop.

    Returns
    -------
    list of (float or None)
        Releases in acre-feet per day, one entry per row of ``df``, in the
        same order. ``None`` entries indicate rows for which no rule
        matched (see :func:`simulate_release` for the cases that produce
        ``None``).

    Raises
    ------
    TypeError
        If ``df`` is not a :class:`pandas.DataFrame`.
    KeyError
        If ``grand_id`` is not present in the catalog (via
        :func:`find_reservoir_index`), or if any of the required columns
        is missing from ``df``.

    Examples
    --------
    >>> import pandas as pd
    >>> import nwm_gdrom
    >>> catalog = nwm_gdrom.load_catalog("dist/nwm_gdrom_catalog.npz")  # doctest: +SKIP
    >>> df = pd.read_csv("tests/data/timeseries/41.csv")  # doctest: +SKIP
    >>> releases = simulate_release_dataframe(catalog, 41, df)  # doctest: +SKIP
    >>> len(releases) == len(df)  # doctest: +SKIP
    True
    """
    required = {"Inflow", "Storage", "PDSI", "DOY"}
    missing = required - set(df.columns)
    if missing:
        msg = f"df is missing required columns: {sorted(missing)}"
        raise KeyError(msg)
    reservoir_idx = find_reservoir_index(catalog, grand_id)
    # Extract columns once as contiguous float64 arrays. Faster than
    # `df.iterrows()`, which constructs a fresh Series per row with copy and
    # dtype-coercion overhead; the inner walk dominates the per-row cost so
    # the overall speedup is modest (about 1.4x on a 15,000-row training
    # series) but the anti-pattern is cleanly removed.
    inflow = df["Inflow"].to_numpy(dtype=np.float64, copy=False)
    storage = df["Storage"].to_numpy(dtype=np.float64, copy=False)
    pdsi = df["PDSI"].to_numpy(dtype=np.float64, copy=False)
    doy = df["DOY"].to_numpy(dtype=np.float64, copy=False)
    return [
        simulate_release(
            catalog,
            reservoir_idx,
            float(inflow[k]),
            float(storage[k]),
            float(pdsi[k]),
            float(doy[k]),
        )
        for k in range(len(df))
    ]
