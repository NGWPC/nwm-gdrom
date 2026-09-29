"""Loader for the GDROM v2 reservoir metadata CSV.

Parses `reservoir_metadata.csv` into a typed DataFrame indexed by GRAND_ID.
The raw CSV carries many agency-use columns (USE_IRRI, USE_ELEC, ...); we
preserve them all and let callers select what they need. The catalog builder
in `nwm_gdrom.rule_catalog` consumes the DataFrame directly.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

_VALID_CATEGORIES = frozenset(("Res_R", "Res_L", "Res_M"))

ACRE_FT_TO_M3 = 1233.48


def load_metadata(path: Path | str) -> pd.DataFrame:
    """Load the GDROM v2 reservoir metadata CSV.

    Parses ``reservoir_metadata.csv`` into a typed pandas DataFrame indexed
    by GRanD identifier, applies numeric coercion to the columns the
    downstream catalog builder depends on, strips whitespace from string
    columns, and validates the file's structural assumptions.

    Parameters
    ----------
    path : pathlib.Path
        Path to ``reservoir_metadata.csv`` as shipped in the GDROM v2
        HydroShare release. The CSV must contain at minimum the columns
        ``GRAND_ID``, ``CATEGORY``, and ``STORAGE_CAP``.

    Returns
    -------
    pandas.DataFrame
        DataFrame indexed by GRanD identifier (``int64``, name
        ``GRAND_ID``) and sorted in ascending order. All original CSV
        columns are retained. Numeric coercions applied:

        - ``GRAND_ID``: ``int64`` (both as index and as a column).
        - ``STORAGE_CAP``: ``float64``, acre-feet as reported by GDROM.
        - ``MODULE_NUMBER``: ``Int64`` (nullable) when present in the file.

        String columns are stripped of leading and trailing whitespace.

    Raises
    ------
    FileNotFoundError
        If ``path`` does not exist.
    ValueError
        If any of the required columns ``GRAND_ID``, ``CATEGORY``,
        ``STORAGE_CAP`` is missing; if the ``CATEGORY`` column contains
        values outside ``{"Res_R", "Res_L", "Res_M"}``; or if the
        ``GRAND_ID`` column contains duplicate values.

    Examples
    --------
    >>> from pathlib import Path
    >>> md = load_metadata(Path("tests/data/reservoir_metadata.csv"))
    >>> md.loc[40, "CATEGORY"]
    'Res_L'
    """
    path = Path(path)
    if not path.is_file():
        msg = f"metadata file does not exist: {path}"
        raise FileNotFoundError(msg)

    df = pd.read_csv(path)

    required = {"GRAND_ID", "CATEGORY", "STORAGE_CAP"}
    missing = required - set(df.columns)
    if missing:
        msg = f"{path}: missing required columns {sorted(missing)}"
        raise ValueError(msg)

    df["GRAND_ID"] = df["GRAND_ID"].astype("int64")
    df["STORAGE_CAP"] = pd.to_numeric(df["STORAGE_CAP"], errors="coerce")
    if "MODULE_NUMBER" in df.columns:
        df["MODULE_NUMBER"] = pd.to_numeric(df["MODULE_NUMBER"], errors="coerce").astype("Int64")

    for col in df.select_dtypes(include=["object", "string"]).columns:
        df[col] = df[col].str.strip()

    bad_cat = set(df["CATEGORY"].dropna().unique()) - _VALID_CATEGORIES
    if bad_cat:
        msg = f"{path}: unknown CATEGORY values {sorted(bad_cat)}"
        raise ValueError(msg)

    df = df.set_index("GRAND_ID", drop=False).sort_index()
    if df.index.duplicated().any():
        msg = f"{path}: duplicate GRAND_ID values"
        raise ValueError(msg)
    return df
