"""Flat-array CSR catalog for GDROM v2 rules across N reservoirs.

The catalog packs per-reservoir metadata, the module rules, and the
dispatcher CARTs for every reservoir into a handful of numpy arrays that are
cache-friendly for evaluation and cheap to serialize with `numpy.savez`.

Layout mirrors Section 6 of the GDROM v2 technical report, adapted for the
two-kind module taxonomy (EXPR / TREE) and the 4-float `ReleaseExpr`
terminal (see `rule_parser.ReleaseExpr`).

Packed encodings in `modules_flat` (float64) per module slice:

- EXPR (kind=0): `[a_inflow, a_storage, c, clamp_min]`
- TREE (kind=1): sequence of branches; each branch has layout
  `[n_pred, var0, op0, th0, ..., a_inflow, a_storage, c, clamp_min]`

Packed encoding in `conditions_flat` (float64) per dispatcher branch:
  `[n_pred, var0, op0, th0, ..., module_id]`

**Deviation from §6 of the report:** the report specifies `float32` for the
flat arrays. We use `float64` because thresholds like `-2.67` round-trip
differently in float32 (`-2.67000007629...`) vs. float64 (`-2.669999...`),
which flips edge-of-boundary comparisons against the `rule2model.py` oracle
(whose inputs are float64). Measured full-CONUS resident size is ~24 MB, so
the 2x cost over float32 is negligible.

Ints stored in float64 slots are round-trip exact for the value ranges used
here (var/op ∈ 0..3, n_pred < 256, module_id < 1e6).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from nwm_gdrom.metadata import ACRE_FT_TO_M3
from nwm_gdrom.rule_parser import (
    ModuleBranch,
    ModuleDescriptor,
    ModuleKind,
    ReleaseExpr,
    parse_module,
    parse_module_condition,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from numpy.typing import NDArray

    from nwm_gdrom.rule_parser import ConditionBranch


_CATEGORY_CODES = {"Res_R": 0, "Res_L": 1, "Res_M": 2}
_CATEGORY_NAMES = {v: k for k, v in _CATEGORY_CODES.items()}

# Sentinel: state field is fixed-width 2-char ASCII; "  " (two spaces) means
# "unknown / not provided". Using a fixed width is what np.savez round-trips
# faithfully without pickle.
_STATE_DTYPE = np.dtype("S2")
_STATE_UNKNOWN = b"  "


@dataclass(slots=True)
class GDROMCatalog:
    """Flat-array catalog of GDROM rules for N reservoirs.

    Per-reservoir fields (all length N, sorted by ascending `grand_ids`):

    | field             | dtype   | description                                       |
    |-------------------|---------|---------------------------------------------------|
    | grand_ids         | int64   | GRanD identifier (≥10000 for non-GRanD).          |
    | state             | S2      | 2-letter state code for PDSI mapping; `  ` empty. |
    | category          | int8    | 0=Res_R, 1=Res_L, 2=Res_M.                        |
    | storage_cap_m3    | float32 | Capacity in m³ (acre-ft x 1233.48 at build time). |
    | min_storage_m3    | float32 | Dead-pool / minimum operating storage in m³.      |
    | ood_inflow_p01_af | float32 | Lower OOD threshold, acre-ft/day; -inf disables.  |
    | ood_inflow_p99_af | float32 | Upper OOD threshold, acre-ft/day; +inf disables.  |

    Modules (flat-packed, CSR-indexed):

    | field                   | dtype           | description                              |
    |-------------------------|-----------------|------------------------------------------|
    | reservoir_modules_start | int32[N+1]      | First module index per reservoir.        |
    | modules_kind            | int8[M_total]   | See `ModuleKind` (EXPR / TREE).          |
    | modules_ptr             | int32[M_total+1]| Offsets into `modules_flat`.             |
    | modules_flat            | float64[...]    | Packed params; see module docstring.     |

    Dispatcher conditions (flat-packed, CSR-indexed):

    | field                   | dtype          | description                                |
    |-------------------------|----------------|--------------------------------------------|
    | conditions_branch_start | int32[N+1]     | First branch per reservoir; empty range = no dispatcher. |
    | conditions_ptr          | int32[B+1]     | Offsets into `conditions_flat`.            |
    | conditions_flat         | float64[...]   | Packed branches; see module docstring.     |

    Versioning: `rule_version` and `crosswalk_version` strings, validated on
    `load_catalog` against an optional expected value.
    """

    grand_ids: NDArray[np.int64]
    state: NDArray[np.bytes_]
    category: NDArray[np.int8]
    storage_cap_m3: NDArray[np.float32]
    min_storage_m3: NDArray[np.float32]
    ood_inflow_p01_af: NDArray[np.float32]
    ood_inflow_p99_af: NDArray[np.float32]

    reservoir_modules_start: NDArray[np.int32]
    modules_kind: NDArray[np.int8]
    modules_ptr: NDArray[np.int32]
    modules_flat: NDArray[np.float64]

    conditions_branch_start: NDArray[np.int32]
    conditions_ptr: NDArray[np.int32]
    conditions_flat: NDArray[np.float64]

    rule_version: str
    crosswalk_version: str

    @property
    def n_reservoirs(self) -> int:
        """Number of reservoirs in the catalog.

        Returns
        -------
        int
            Length of the ``grand_ids`` array.
        """
        return int(self.grand_ids.shape[0])

    def _check_idx(self, reservoir_idx: int) -> None:
        """Validate that ``reservoir_idx`` is in range, raising IndexError if not."""
        n = self.n_reservoirs
        if not (0 <= reservoir_idx < n):
            msg = f"reservoir_idx must be in [0, {n}); got {reservoir_idx}"
            raise IndexError(msg)

    def n_modules(self, reservoir_idx: int) -> int:
        """Return the number of modules for a reservoir.

        Parameters
        ----------
        reservoir_idx : int
            Row index in ``[0, n_reservoirs)``. Use
            :func:`nwm_gdrom.evaluator.find_reservoir_index` to resolve a
            GRanD identifier to its row index.

        Returns
        -------
        int
            Number of modules attached to this reservoir (always at least
            one).

        Raises
        ------
        IndexError
            If ``reservoir_idx`` is outside ``[0, n_reservoirs)``.
        """
        self._check_idx(reservoir_idx)
        return int(
            self.reservoir_modules_start[reservoir_idx + 1]
            - self.reservoir_modules_start[reservoir_idx]
        )

    def has_dispatcher(self, reservoir_idx: int) -> bool:
        """Return whether a reservoir has a dispatcher tree.

        Reservoirs with a single module have no dispatcher; reservoirs with
        multiple modules have a dispatcher CART that selects among them on
        each daily evaluation.

        Parameters
        ----------
        reservoir_idx : int
            Row index in ``[0, n_reservoirs)``.

        Returns
        -------
        bool
            ``True`` if the reservoir's dispatcher branch range is
            non-empty, ``False`` otherwise.

        Raises
        ------
        IndexError
            If ``reservoir_idx`` is outside ``[0, n_reservoirs)``.
        """
        self._check_idx(reservoir_idx)
        return (
            self.conditions_branch_start[reservoir_idx + 1]
            > self.conditions_branch_start[reservoir_idx]
        )

    def category_name(self, reservoir_idx: int) -> str:
        """Return the human-readable GDROM category for a reservoir.

        Parameters
        ----------
        reservoir_idx : int
            Row index in ``[0, n_reservoirs)``.

        Returns
        -------
        str
            One of ``"Res_R"``, ``"Res_L"``, or ``"Res_M"``.

        Raises
        ------
        IndexError
            If ``reservoir_idx`` is outside ``[0, n_reservoirs)``.
        """
        self._check_idx(reservoir_idx)
        return _CATEGORY_NAMES[int(self.category[reservoir_idx])]


def _pack_predicates(branch: ModuleBranch | ConditionBranch, out: list[float]) -> None:
    """Append `[n_pred, var0, op0, th0, ...]` for `branch`'s predicates."""
    out.append(float(len(branch.predicates)))
    for p in branch.predicates:
        out.append(float(p.var_code))
        out.append(float(p.op_code))
        out.append(float(p.threshold))


def _pack_release_expr(expr: ReleaseExpr, out: list[float]) -> None:
    """Append the 4-float `[a_inflow, a_storage, c, clamp_min]` tuple."""
    out.append(float(expr.a_inflow))
    out.append(float(expr.a_storage))
    out.append(float(expr.c))
    out.append(float(expr.clamp_min))


def _pack_module(mod: ModuleDescriptor) -> list[float]:
    """Return the packed float params for one ModuleDescriptor."""
    packed: list[float] = []
    if mod.kind == ModuleKind.EXPR:
        assert mod.expr is not None
        _pack_release_expr(mod.expr, packed)
        return packed
    for br in mod.tree_branches:
        _pack_predicates(br, packed)
        _pack_release_expr(br.release, packed)
    return packed


def _series_to_array(
    series: pd.Series | Mapping[int, float] | None,
    grand_ids: NDArray[np.int64],
    *,
    default: float,
    dtype: np.dtype,
) -> NDArray[np.floating]:
    """Reindex a per-reservoir series/dict to `grand_ids` order, defaulting missing."""
    out = np.full(grand_ids.shape, default, dtype=dtype)
    if series is None:
        return out
    if isinstance(series, pd.Series):
        vals = series.reindex(grand_ids).to_numpy(dtype=np.float64, na_value=default)
        out[:] = vals.astype(dtype, copy=False)
    else:
        for i, gid in enumerate(grand_ids):
            v = series.get(int(gid))
            if v is not None:
                out[i] = dtype.type(v)
    return out


def build_catalog(
    grand_ids: list[int],
    modules_dir: Path | str,
    conditions_dir: Path | str,
    *,
    rule_version: str,
    crosswalk_version: str,
    metadata: pd.DataFrame | None = None,
    min_storage_m3: pd.Series | Mapping[int, float] | None = None,
    ood_inflow_p01_af: pd.Series | Mapping[int, float] | None = None,
    ood_inflow_p99_af: pd.Series | Mapping[int, float] | None = None,
) -> GDROMCatalog:
    """Build a catalog by parsing GDROM rule files for a set of reservoirs.

    For each identifier in ``grand_ids``, discovers the reservoir's module
    files by globbing ``modules_dir / "{grand_id}_*.txt"`` (sorted by
    module identifier) and attaches the dispatcher CART from
    ``conditions_dir / "{grand_id}.txt"`` when that file exists.
    Reservoirs with a single module and no dispatcher are handled
    naturally: the conditions branch range is empty and the evaluator
    routes the day to module 0.

    Parameters
    ----------
    grand_ids : list of int
        Strictly increasing list of GRanD identifiers (sorted unique) to
        include in the catalog. Must be non-empty for a usable catalog;
        an empty list is accepted but produces a degenerate catalog.
    modules_dir : pathlib.Path
        Directory containing the module files, layout
        ``{grand_id}_{module_id}.txt``. Must exist.
    conditions_dir : pathlib.Path
        Directory containing the dispatcher files, layout
        ``{grand_id}.txt``. Must exist; reservoirs without a dispatcher
        file are handled silently.
    rule_version : str
        Reproducibility tag embedded in the catalog and validated on
        :func:`load_catalog`. Must be a non-empty string.
    crosswalk_version : str
        Reproducibility tag for the GRanD-to-NHF crosswalk. Must be a
        non-empty string. Use ``"none"`` if no crosswalk is bundled.
    metadata : pandas.DataFrame, optional
        Output of :func:`nwm_gdrom.metadata.load_metadata`, indexed by
        ``GRAND_ID``. The columns ``CATEGORY`` and ``STORAGE_CAP`` are
        consumed; ``STATE`` is consumed when present. Reservoirs absent
        from the index default to ``Res_R``, NaN capacity, and empty
        state code.
    min_storage_m3 : pandas.Series or dict, optional
        Per-reservoir dead-pool storage in cubic meters, keyed by
        ``GRAND_ID``. Reservoirs without an entry default to zero.
    ood_inflow_p01_af : pandas.Series or dict, optional
        Per-reservoir lower out-of-distribution inflow threshold in
        acre-feet per day. Reservoirs without an entry default to
        negative infinity (lower trigger disabled).
    ood_inflow_p99_af : pandas.Series or dict, optional
        Per-reservoir upper out-of-distribution inflow threshold in
        acre-feet per day. Defaults to positive infinity (upper trigger
        disabled).

    Returns
    -------
    GDROMCatalog
        Catalog with all reservoir, module, and dispatcher arrays packed
        in the canonical CSR layout.

    Raises
    ------
    NotADirectoryError
        If ``modules_dir`` or ``conditions_dir`` exists but is not a
        directory.
    FileNotFoundError
        If a reservoir has no module files matching
        ``modules_dir / "{grand_id}_*.txt"``.
    ValueError
        If ``rule_version`` or ``crosswalk_version`` is empty, or if
        ``grand_ids`` is not strictly increasing (sorted unique).
    RuleParseError
        Propagated from the underlying parser when any rule file is
        malformed.

    Examples
    --------
    >>> from pathlib import Path
    >>> import nwm_gdrom.metadata as md_mod
    >>> from nwm_gdrom.rule_catalog import build_catalog
    >>> md = md_mod.load_metadata(Path("tests/data/reservoir_metadata.csv"))
    >>> cat = build_catalog(
    ...     [40, 41, 42, 44, 47],
    ...     Path("tests/data/modules"),
    ...     Path("tests/data/module_conditions"),
    ...     metadata=md,
    ...     rule_version="dev",
    ...     crosswalk_version="dev",
    ... )
    >>> cat.n_reservoirs
    5
    """
    if not rule_version or not crosswalk_version:
        msg = "rule_version and crosswalk_version must be non-empty strings"
        raise ValueError(msg)
    modules_dir = Path(modules_dir)
    conditions_dir = Path(conditions_dir)
    if modules_dir.exists() and not modules_dir.is_dir():
        msg = f"modules_dir is not a directory: {modules_dir}"
        raise NotADirectoryError(msg)
    if conditions_dir.exists() and not conditions_dir.is_dir():
        msg = f"conditions_dir is not a directory: {conditions_dir}"
        raise NotADirectoryError(msg)
    n = len(grand_ids)
    grand_ids_arr = np.asarray(grand_ids, dtype=np.int64)
    if grand_ids_arr.ndim != 1 or (n > 0 and np.any(np.diff(grand_ids_arr) <= 0)):
        msg = "grand_ids must be a strictly increasing 1-D list (sorted unique)"
        raise ValueError(msg)

    cat_arr = np.full(n, _CATEGORY_CODES["Res_R"], dtype=np.int8)
    cap_arr = np.full(n, np.nan, dtype=np.float32)
    state_arr = np.full(n, _STATE_UNKNOWN, dtype=_STATE_DTYPE)
    if metadata is not None:
        sub = metadata.reindex(grand_ids_arr)
        cat_codes = sub["CATEGORY"].map(_CATEGORY_CODES)
        cat_arr[:] = cat_codes.fillna(_CATEGORY_CODES["Res_R"]).astype(np.int8).to_numpy()
        cap_arr[:] = (sub["STORAGE_CAP"] * ACRE_FT_TO_M3).astype(np.float32).to_numpy()
        if "STATE" in sub.columns:
            state_arr[:] = (
                sub["STATE"]
                .fillna("")
                .astype(str)
                .str.slice(0, 2)
                .str.pad(2, fillchar=" ")
                .str.encode("ascii")
                .to_numpy()
            )

    min_storage_arr = _series_to_array(
        min_storage_m3, grand_ids_arr, default=0.0, dtype=np.dtype(np.float32)
    )
    ood_p01_arr = _series_to_array(
        ood_inflow_p01_af, grand_ids_arr, default=-np.inf, dtype=np.dtype(np.float32)
    )
    ood_p99_arr = _series_to_array(
        ood_inflow_p99_af, grand_ids_arr, default=np.inf, dtype=np.dtype(np.float32)
    )

    res_mod_start = np.zeros(n + 1, dtype=np.int32)
    modules_kind_list: list[int] = []
    modules_ptr_list: list[int] = [0]
    modules_flat_list: list[float] = []

    cond_branch_start = np.zeros(n + 1, dtype=np.int32)
    conditions_ptr_list: list[int] = [0]
    conditions_flat_list: list[float] = []

    for i, gid in enumerate(grand_ids):
        module_files = sorted(
            modules_dir.glob(f"{gid}_*.txt"),
            key=lambda p: int(p.stem.split("_", 1)[1]),
        )
        if not module_files:
            msg = f"grand_id {gid}: no module files found in {modules_dir}"
            raise FileNotFoundError(msg)

        for mf in module_files:
            mod = parse_module(mf)
            packed = _pack_module(mod)
            modules_kind_list.append(int(mod.kind))
            modules_flat_list.extend(packed)
            modules_ptr_list.append(len(modules_flat_list))
        res_mod_start[i + 1] = len(modules_kind_list)

        cond_path = conditions_dir / f"{gid}.txt"
        if cond_path.exists():
            cond = parse_module_condition(cond_path)
            for br in cond.branches:
                _pack_predicates(br, conditions_flat_list)
                conditions_flat_list.append(float(br.module_id))
                conditions_ptr_list.append(len(conditions_flat_list))
        cond_branch_start[i + 1] = len(conditions_ptr_list) - 1

    return GDROMCatalog(
        grand_ids=grand_ids_arr,
        state=state_arr,
        category=cat_arr,
        storage_cap_m3=cap_arr,
        min_storage_m3=min_storage_arr,
        ood_inflow_p01_af=ood_p01_arr,
        ood_inflow_p99_af=ood_p99_arr,
        reservoir_modules_start=res_mod_start,
        modules_kind=np.asarray(modules_kind_list, dtype=np.int8),
        modules_ptr=np.asarray(modules_ptr_list, dtype=np.int32),
        modules_flat=np.asarray(modules_flat_list, dtype=np.float64),
        conditions_branch_start=cond_branch_start,
        conditions_ptr=np.asarray(conditions_ptr_list, dtype=np.int32),
        conditions_flat=np.asarray(conditions_flat_list, dtype=np.float64),
        rule_version=rule_version,
        crosswalk_version=crosswalk_version,
    )


_FIELD_DTYPES: dict[str, np.dtype] = {
    "grand_ids": np.dtype(np.int64),
    "state": _STATE_DTYPE,
    "category": np.dtype(np.int8),
    "storage_cap_m3": np.dtype(np.float32),
    "min_storage_m3": np.dtype(np.float32),
    "ood_inflow_p01_af": np.dtype(np.float32),
    "ood_inflow_p99_af": np.dtype(np.float32),
    "reservoir_modules_start": np.dtype(np.int32),
    "modules_kind": np.dtype(np.int8),
    "modules_ptr": np.dtype(np.int32),
    "modules_flat": np.dtype(np.float64),
    "conditions_branch_start": np.dtype(np.int32),
    "conditions_ptr": np.dtype(np.int32),
    "conditions_flat": np.dtype(np.float64),
}


class CatalogVersionMismatchError(ValueError):
    """Raised when a loaded catalog's version stamps do not match expectations.

    Either ``rule_version`` or ``crosswalk_version`` (or both) on disk differ
    from the values the caller passed to :func:`load_catalog` via
    ``expected_rule_version`` / ``expected_crosswalk_version``. Inherits from
    :class:`ValueError` so callers that only catch ``ValueError`` still
    handle it.
    """


def save_catalog(catalog: GDROMCatalog, path: Path | str) -> None:
    """Serialize a catalog to a compressed NumPy archive.

    Writes every array field of ``catalog`` plus the two version stamps to
    ``path`` as a compressed ``.npz`` archive. The output is loadable by
    :func:`load_catalog` and is the canonical on-disk representation
    consumed by T-Route at initialization.

    Parameters
    ----------
    catalog : GDROMCatalog
        Catalog produced by :func:`build_catalog`.
    path : pathlib.Path
        Output path. Parent directories must already exist; the file is
        overwritten if it exists.

    Raises
    ------
    TypeError
        If ``catalog`` is not a :class:`GDROMCatalog`.
    FileNotFoundError
        If the parent directory of ``path`` does not exist.

    Examples
    --------
    >>> from pathlib import Path
    >>> save_catalog(catalog, Path("dist/nwm_gdrom_catalog.npz"))  # doctest: +SKIP
    """
    path = Path(path)
    if not path.parent.exists():
        msg = f"parent directory does not exist: {path.parent}"
        raise FileNotFoundError(msg)
    payload: dict[str, np.ndarray] = {f: getattr(catalog, f) for f in _FIELD_DTYPES}
    payload["rule_version"] = np.asarray(catalog.rule_version)
    payload["crosswalk_version"] = np.asarray(catalog.crosswalk_version)
    np.savez_compressed(path, **payload)  # type: ignore[arg-type]


def load_catalog(
    path: Path | str,
    *,
    expected_rule_version: str | None = None,
    expected_crosswalk_version: str | None = None,
) -> GDROMCatalog:
    """Load a catalog from disk and validate its version stamps.

    Reads a compressed NumPy archive previously written by
    :func:`save_catalog` and returns a :class:`GDROMCatalog`. The archive
    is opened with ``allow_pickle=False`` so loading never executes
    arbitrary code. If the caller provides ``expected_rule_version`` or
    ``expected_crosswalk_version``, those are validated against the
    embedded stamps and a mismatch raises
    :class:`CatalogVersionMismatchError` rather than silently continuing
    with an unexpected catalog. This is the recommended consumption
    pattern for operational T-Route deployments.

    Parameters
    ----------
    path : pathlib.Path
        Path to a ``.npz`` file produced by :func:`save_catalog`.
    expected_rule_version : str, optional
        If provided, the catalog's embedded ``rule_version`` must equal
        this string exactly; otherwise the load aborts. Pass ``None`` (the
        default) to skip the check, which is appropriate during catalog
        development but not for archived or operational runs.
    expected_crosswalk_version : str, optional
        If provided, the catalog's embedded ``crosswalk_version`` must
        equal this string exactly. Defaults to ``None``.

    Returns
    -------
    GDROMCatalog
        Fully populated catalog with all array fields cast to their
        canonical dtypes.

    Raises
    ------
    FileNotFoundError
        If ``path`` does not exist.
    ValueError
        If the archive is missing any required array field or version
        stamp.
    CatalogVersionMismatchError
        If either expected version is provided and does not match the
        embedded stamp.

    Examples
    --------
    >>> from pathlib import Path
    >>> import nwm_gdrom
    >>> catalog = nwm_gdrom.load_catalog(
    ...     Path("dist/nwm_gdrom_catalog.npz"),
    ...     expected_rule_version="v0.1.0",
    ... )  # doctest: +SKIP
    >>> catalog.n_reservoirs  # doctest: +SKIP
    2017
    """
    path = Path(path)
    if not path.is_file():
        msg = f"catalog file does not exist: {path}"
        raise FileNotFoundError(msg)
    with np.load(path, allow_pickle=False) as z:
        missing = set(_FIELD_DTYPES) - set(z.files)
        if missing:
            msg = f"catalog at {path} is missing required fields: {sorted(missing)}"
            raise ValueError(msg)
        for required in ("rule_version", "crosswalk_version"):
            if required not in z.files:
                msg = f"catalog at {path} is missing version stamp {required!r}"
                raise ValueError(msg)

        rule_version = str(z["rule_version"])
        crosswalk_version = str(z["crosswalk_version"])

        if expected_rule_version is not None and rule_version != expected_rule_version:
            msg = (
                f"catalog rule_version mismatch: expected {expected_rule_version!r}, "
                f"got {rule_version!r}"
            )
            raise CatalogVersionMismatchError(msg)
        if (
            expected_crosswalk_version is not None
            and crosswalk_version != expected_crosswalk_version
        ):
            msg = (
                f"catalog crosswalk_version mismatch: expected {expected_crosswalk_version!r}, "
                f"got {crosswalk_version!r}"
            )
            raise CatalogVersionMismatchError(msg)

        kwargs = {f: z[f].astype(dt, copy=False) for f, dt in _FIELD_DTYPES.items()}
        return GDROMCatalog(
            **kwargs,
            rule_version=rule_version,
            crosswalk_version=crosswalk_version,
        )
