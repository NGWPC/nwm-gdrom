"""Compute per-reservoir summary statistics from cleaned daily CSVs.

The GDROM v2 release ships a `cleaned data for Res-R & Res-L/{grand_id}.csv`
directory holding one daily file per reservoir with columns:
`Date, Storage, Inflow, Release, DOY, PDSI`.

This module produces the OOD (out-of-distribution) inflow thresholds that the
T-Route evaluator uses to fall back to level-pool physics when an inflow lies
outside the regime the rules were trained on (technical report §7.7, §13.2).

Res_M reservoirs do not have their own training CSVs; their rules were
copy-transferred from an analogous Res_R reservoir during training, so OOD
thresholds for them are the responsibility of the downstream bundle build to
inject (typically inheriting from the analog). Files this helper cannot find
are reported but not synthesized; defaults from `build_catalog` (-inf, +inf)
take effect when an entry is omitted from the output.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from collections.abc import Iterable


def compute_ood_thresholds(
    ts_dir: Path | str,
    grand_ids: Iterable[int],
    *,
    low_percentile: float = 0.01,
    high_percentile: float = 0.99,
) -> pd.DataFrame:
    """Compute per-reservoir inflow percentiles from cleaned daily CSVs.

    For each GRanD identifier in ``grand_ids``, reads
    ``{ts_dir}/{grand_id}.csv``, extracts the ``Inflow`` column, drops NaN
    values, and computes the lower and upper percentiles. Files that do not
    exist or contain no usable inflow values are silently omitted from the
    result; the catalog defaults (negative and positive infinity, which
    disable the out-of-distribution trigger) then take effect for those
    reservoirs.

    Parameters
    ----------
    ts_dir : pathlib.Path
        Directory containing the cleaned daily CSVs. Each file must be
        named ``{grand_id}.csv`` and must contain an ``Inflow`` column in
        acre-feet per day.
    grand_ids : Iterable[int]
        GRanD identifiers to process. Order is irrelevant; the result is
        sorted by ascending identifier.
    low_percentile : float, optional
        Lower percentile as a fraction in [0, 1). Default 0.01.
    high_percentile : float, optional
        Upper percentile as a fraction in (low_percentile, 1]. Default 0.99.

    Returns
    -------
    pandas.DataFrame
        DataFrame indexed by ``GRAND_ID`` (``int64``) with columns
        ``p_low`` and ``p_high`` (``float64``), one row per reservoir whose
        CSV was found and yielded at least one finite inflow value. The
        DataFrame is sorted by ascending GRanD identifier.

    Raises
    ------
    NotADirectoryError
        If ``ts_dir`` exists but is not a directory.
    ValueError
        If the percentiles do not satisfy
        ``0 <= low_percentile < high_percentile <= 1``.

    Examples
    --------
    >>> from pathlib import Path
    >>> df = compute_ood_thresholds(
    ...     Path("tests/data/timeseries"), [40, 41], low_percentile=0.05, high_percentile=0.95
    ... )
    >>> list(df.columns)
    ['p_low', 'p_high']
    """
    ts_dir = Path(ts_dir)
    if ts_dir.exists() and not ts_dir.is_dir():
        msg = f"ts_dir is not a directory: {ts_dir}"
        raise NotADirectoryError(msg)
    if not (0.0 <= low_percentile < high_percentile <= 1.0):
        msg = (
            f"percentiles must satisfy 0 <= low < high <= 1; "
            f"got low={low_percentile}, high={high_percentile}"
        )
        raise ValueError(msg)

    rows: list[tuple[int, float, float]] = []
    for gid in grand_ids:
        csv_path = ts_dir / f"{gid}.csv"
        if not csv_path.is_file():
            continue
        # Read only the Inflow column; the others aren't needed here and
        # skipping them halves I/O and parsing cost on the full corpus.
        inflow = pd.read_csv(csv_path, usecols=["Inflow"])["Inflow"].dropna()
        if inflow.empty:
            continue
        p_lo, p_hi = inflow.quantile([low_percentile, high_percentile]).to_numpy()
        rows.append((int(gid), float(p_lo), float(p_hi)))

    if not rows:
        empty = pd.DataFrame(columns=["p_low", "p_high"], dtype="float64")
        empty.index = pd.Index([], dtype="int64", name="GRAND_ID")
        return empty

    df = pd.DataFrame.from_records(rows, columns=["GRAND_ID", "p_low", "p_high"]).set_index(
        "GRAND_ID"
    )
    return df.sort_index()
