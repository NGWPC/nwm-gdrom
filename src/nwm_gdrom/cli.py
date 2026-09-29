"""Command-line interface for the `nwm-gdrom` package.

Single entry point ``nwm-gdrom`` that:

1. Normalizes a GDROM v2 source directory (Hydroshare bag, raw extraction, or
   local mirror) into the canonical layout under ``--source-dir``.
2. Builds the full-CONUS rule catalog ``.npz`` and writes it to ``--out``,
   unless ``--download-only`` is passed.

Canonical layout under ``--source-dir`` (the package's runtime expectation):

    {source-dir}/reservoir_metadata.csv
    {source-dir}/rule_files/Operation Rules - GDROMs/modules/{grand_id}_{module_id}.txt
    {source-dir}/rule_files/Operation Rules - GDROMs/module_conditions/{grand_id}.txt
    {source-dir}/time_series/cleaned data for Res-R & Res-L/{grand_id}.csv

Usage:

    # Default: download GDROM v2 from Hydroshare and build the catalog
    nwm-gdrom --source-dir ./data --out dist/nwm_gdrom_catalog.npz

    # Just normalize layout (skip the catalog build)
    nwm-gdrom -d ./data --download-only

    # Use a different Hydroshare DOI
    nwm-gdrom -d ./data --hydroshare-doi 10.4211/hs.5293674cb83b4ec698db0eb4777467b8

    # Bypass the network by providing a pre-downloaded zip or extracted directory
    nwm-gdrom -d ./data --source-zip /path/to/gdromv2.zip
    nwm-gdrom -d ./data --source-existing /path/to/gdromv2

    # Pin a specific version tag in the .npz (defaults to git describe, then 'dev')
    nwm-gdrom -d ./data --rule-version v0.1.0

Source resolution order (first match wins):
  1. --source-existing: pre-extracted directory
  2. --source-zip:      pre-downloaded archive
  3. --hydroshare-doi:  Hydroshare bag download (default GDROM v2 DOI)
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from nwm_gdrom.metadata import load_metadata
from nwm_gdrom.rule_catalog import build_catalog, load_catalog, save_catalog
from nwm_gdrom.timeseries import compute_ood_thresholds

# DOI for the GDROM v2 Hydroshare resource (Jiang et al.). Used as the default
# download source when the user does not provide a local --source-zip or
# --source-existing.
DEFAULT_HYDROSHARE_DOI = "10.4211/hs.5293674cb83b4ec698db0eb4777467b8"
HYDROSHARE_BAG_URL_TEMPLATE = (
    "https://www.hydroshare.org/django_irods/download/bags/{resource_id}.zip"
)
HYDROSHARE_DOI_PREFIX = "10.4211/hs."


def _canonical_paths(source_dir: Path) -> dict[str, Path]:
    """Paths the rest of the package expects relative to ``source_dir``."""
    return {
        "metadata_csv": source_dir / "reservoir_metadata.csv",
        "modules_dir": source_dir / "rule_files" / "Operation Rules - GDROMs" / "modules",
        "conditions_dir": source_dir
        / "rule_files"
        / "Operation Rules - GDROMs"
        / "module_conditions",
        "timeseries_dir": source_dir / "time_series" / "cleaned data for Res-R & Res-L",
    }


def _layout_complete(source_dir: Path) -> bool:
    """True when the canonical layout's required pieces all exist."""
    paths = _canonical_paths(source_dir)
    return (
        paths["metadata_csv"].is_file()
        and paths["modules_dir"].is_dir()
        and paths["conditions_dir"].is_dir()
    )


def _normalize_layout(extracted_dir: Path, source_dir: Path) -> None:
    """Move/copy a raw GDROM v2 extraction into the canonical layout.

    Looks for the four canonical inputs anywhere under ``extracted_dir`` and
    places them at the package's expected paths under ``source_dir``. Idempotent:
    a destination that already exists is left alone.
    """
    found = {
        "reservoir_metadata.csv": next(extracted_dir.glob("**/reservoir_metadata.csv"), None),
        "Operation Rules - GDROMs": next(extracted_dir.glob("**/Operation Rules - GDROMs"), None),
        "Time Series of Reservoir Variables": next(
            extracted_dir.glob("**/Time Series of Reservoir Variables"), None
        ),
    }

    metadata_src = found["reservoir_metadata.csv"]
    if metadata_src is not None:
        dest = source_dir / "reservoir_metadata.csv"
        if not dest.exists():
            shutil.copy2(metadata_src, dest)
            print(f"  copied reservoir_metadata.csv → {dest}")

    rules_src = found["Operation Rules - GDROMs"]
    if rules_src is not None:
        dest = source_dir / "rule_files" / "Operation Rules - GDROMs"
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(rules_src, dest)
            print(f"  copied Operation Rules - GDROMs/ → {dest}")

    ts_src = found["Time Series of Reservoir Variables"]
    if ts_src is not None:
        dest = source_dir / "time_series"
        if not dest.exists():
            shutil.copytree(ts_src, dest)
            print(f"  copied Time Series of Reservoir Variables/ → {dest}")


def _extract_zip(zip_path: Path, into: Path) -> Path:
    """Extract a zip archive into ``into`` and return the extraction directory."""
    if not zip_path.is_file():
        msg = f"zip path does not exist: {zip_path}"
        raise FileNotFoundError(msg)
    extract_dir = into / "_gdromv2_unpacked"
    extract_dir.mkdir(parents=True, exist_ok=True)
    print(f"Extracting {zip_path} → {extract_dir} ...")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)
    return extract_dir


def _resource_id_from_doi(doi: str) -> str:
    """Extract the Hydroshare resource ID from a DOI like ``10.4211/hs.{rid}``.

    Accepts also a bare resource ID (32-char hex), in which case it is
    returned unchanged. Raises ValueError on anything else.
    """
    # Tolerate "doi:..." or "https://doi.org/..." wrappers, then peel off the
    # Hydroshare DOI prefix if present (or accept a bare 32-char resource ID).
    doi = doi.strip().removeprefix("doi:").removeprefix("https://doi.org/")
    rid = doi.removeprefix(HYDROSHARE_DOI_PREFIX)

    if not (len(rid) == 32 and all(c in "0123456789abcdef" for c in rid.lower())):
        msg = (
            f"could not parse Hydroshare resource ID from {doi!r}; expected "
            f"a DOI like {HYDROSHARE_DOI_PREFIX}<32-hex> or a bare 32-char hex ID"
        )
        raise ValueError(msg)
    return rid.lower()


def _download_hydroshare_bag(resource_id: str, dest: Path) -> Path:
    """Stream-download the Hydroshare bag for ``resource_id`` to ``dest``.

    Idempotent: if ``dest`` already exists with non-zero size, returns it
    unchanged. Use ``--force`` upstream to override.
    """
    if dest.is_file() and dest.stat().st_size > 0:
        print(
            f"Bag already cached at {dest} ({dest.stat().st_size / 1e6:.1f} MB); skipping download"
        )
        return dest

    url = HYDROSHARE_BAG_URL_TEMPLATE.format(resource_id=resource_id)
    print(f"Downloading Hydroshare bag from {url}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as resp, dest.open("wb") as out:  # noqa: S310
        total = int(resp.headers.get("Content-Length", 0))
        bytes_read = 0
        chunk_size = 1024 * 1024  # 1 MB
        last_pct = -1
        while chunk := resp.read(chunk_size):
            out.write(chunk)
            bytes_read += len(chunk)
            if total > 0:
                pct = int(100 * bytes_read / total)
                if pct != last_pct:
                    print(
                        f"\r  {bytes_read / 1e6:>7.1f} / {total / 1e6:.1f} MB  ({pct:>3}%)",
                        end="",
                        flush=True,
                    )
                    last_pct = pct
            else:
                print(f"\r  {bytes_read / 1e6:>7.1f} MB", end="", flush=True)
        print()
    return dest


def prepare_source_layout(
    source_dir: Path,
    *,
    source_zip: Path | None = None,
    source_existing: Path | None = None,
    hydroshare_doi: str | None = DEFAULT_HYDROSHARE_DOI,
    force: bool = False,
) -> None:
    """Ensure ``source_dir`` contains the canonical layout.

    Order of resolution:
      1. If ``source_dir`` already has the layout and ``force`` is False, no-op.
      2. If ``source_existing`` is given, normalize from there.
      3. If ``source_zip`` is given, extract and normalize from there.
      4. If ``hydroshare_doi`` is given (default: GDROM v2 DOI), download
         the Hydroshare bag, extract, and normalize.
      5. Otherwise (all None and layout incomplete), raise.
    """
    source_dir.mkdir(parents=True, exist_ok=True)

    if _layout_complete(source_dir) and not force:
        print(f"Source layout already complete at {source_dir}; nothing to do.")
        return

    if source_existing is not None:
        if not source_existing.is_dir():
            msg = f"--source-existing path does not exist: {source_existing}"
            raise FileNotFoundError(msg)
        print(f"Normalizing layout from existing extraction at {source_existing}")
        _normalize_layout(source_existing, source_dir)
        return

    if source_zip is not None:
        extract_dir = _extract_zip(source_zip, source_dir)
        _normalize_layout(extract_dir, source_dir)
        return

    if hydroshare_doi is not None:
        resource_id = _resource_id_from_doi(hydroshare_doi)
        bag_path = source_dir / "_gdromv2_bag.zip"
        _download_hydroshare_bag(resource_id, bag_path)
        extract_dir = _extract_zip(bag_path, source_dir)
        _normalize_layout(extract_dir, source_dir)
        return

    msg = (
        "Source layout is incomplete and no download source resolved. Pass "
        "either --source-existing, --source-zip, or --hydroshare-doi."
    )
    raise FileNotFoundError(msg)


def build_release_catalog(
    source_dir: Path,
    out_path: Path,
    *,
    rule_version: str,
    crosswalk_version: str = "none",
    ood_low: float = 0.01,
    ood_high: float = 0.99,
) -> Path:
    """Build the full GDROM v2 rule catalog .npz from a normalized source dir.

    Returns the output path on success. Raises if the source layout is
    incomplete or if the round-trip validation fails.
    """
    paths = _canonical_paths(source_dir)
    for label, p in (
        ("reservoir_metadata.csv", paths["metadata_csv"]),
        ("rule modules dir", paths["modules_dir"]),
        ("rule module_conditions dir", paths["conditions_dir"]),
    ):
        if not p.exists():
            msg = f"required {label} missing at {p}"
            raise FileNotFoundError(msg)

    print(f"Loading metadata from {paths['metadata_csv']} ...")
    md = load_metadata(paths["metadata_csv"])
    grand_ids = sorted(int(g) for g in md.index if any(paths["modules_dir"].glob(f"{g}_*.txt")))
    print(f"  {len(grand_ids)} reservoirs have rule files on disk")

    if paths["timeseries_dir"].is_dir():
        print(f"Computing OOD thresholds from {paths['timeseries_dir']} ...")
        t0 = time.perf_counter()
        ood = compute_ood_thresholds(
            paths["timeseries_dir"],
            grand_ids,
            low_percentile=ood_low,
            high_percentile=ood_high,
        )
        print(f"  {len(ood)} reservoirs got OOD thresholds in {time.perf_counter() - t0:.2f}s")
        ood_p01 = ood["p_low"]
        ood_p99 = ood["p_high"]
    else:
        print(
            f"WARNING: time series dir not present at {paths['timeseries_dir']}; "
            f"OOD thresholds default to ±inf"
        )
        ood_p01 = pd.Series(dtype=float)
        ood_p99 = pd.Series(dtype=float)

    print(f"Building catalog (rule_version={rule_version!r}) ...")
    t0 = time.perf_counter()
    catalog = build_catalog(
        grand_ids=grand_ids,
        modules_dir=paths["modules_dir"],
        conditions_dir=paths["conditions_dir"],
        metadata=md,
        ood_inflow_p01_af=ood_p01 if len(ood_p01) > 0 else None,
        ood_inflow_p99_af=ood_p99 if len(ood_p99) > 0 else None,
        rule_version=rule_version,
        crosswalk_version=crosswalk_version,
    )
    print(f"  built in {time.perf_counter() - t0:.2f}s")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Writing {out_path} ...")
    t0 = time.perf_counter()
    save_catalog(catalog, out_path)
    size_mb = out_path.stat().st_size / 1e6
    print(f"  wrote {size_mb:.2f} MB in {time.perf_counter() - t0:.2f}s")

    print("Validating round-trip ...")
    loaded = load_catalog(out_path, expected_rule_version=rule_version)
    _CATALOG_ARRAY_FIELDS = (
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
    )
    if loaded.n_reservoirs != catalog.n_reservoirs:
        msg = f"round-trip lost reservoirs ({catalog.n_reservoirs} → {loaded.n_reservoirs})"
        raise RuntimeError(msg)
    # Field-by-field equality: per the delivery report's verification claim,
    # the build pipeline rejects any catalog whose on-disk representation does
    # not round-trip identically to the in-memory original. NaN values are
    # compared with equal_nan=True so that NaN payloads (e.g. Res_M NaN rules)
    # are treated as bit-equal to themselves.
    for field in _CATALOG_ARRAY_FIELDS:
        original = getattr(catalog, field)
        reloaded = getattr(loaded, field)
        if original.dtype != reloaded.dtype:
            msg = (
                f"round-trip dtype mismatch on field {field!r}: {original.dtype} → {reloaded.dtype}"
            )
            raise RuntimeError(msg)
        if original.shape != reloaded.shape:
            msg = (
                f"round-trip shape mismatch on field {field!r}: {original.shape} → {reloaded.shape}"
            )
            raise RuntimeError(msg)
        if np.issubdtype(original.dtype, np.floating):
            if not np.array_equal(original, reloaded, equal_nan=True):
                msg = f"round-trip value mismatch on float field {field!r}"
                raise RuntimeError(msg)
        elif not np.array_equal(original, reloaded):
            msg = f"round-trip value mismatch on field {field!r}"
            raise RuntimeError(msg)
    if loaded.rule_version != catalog.rule_version:
        msg = (
            f"round-trip rule_version mismatch: {catalog.rule_version!r} → {loaded.rule_version!r}"
        )
        raise RuntimeError(msg)
    if loaded.crosswalk_version != catalog.crosswalk_version:
        msg = (
            f"round-trip crosswalk_version mismatch: "
            f"{catalog.crosswalk_version!r} → {loaded.crosswalk_version!r}"
        )
        raise RuntimeError(msg)

    in_memory = sum(getattr(catalog, f).nbytes for f in _CATALOG_ARRAY_FIELDS)
    print(
        f"\nCatalog summary:\n"
        f"  reservoirs:        {catalog.n_reservoirs:>5,}\n"
        f"  modules total:     {catalog.modules_kind.shape[0]:>5,}\n"
        f"  condition branches:{catalog.conditions_ptr.shape[0] - 1:>5,}\n"
        f"  rule_version:      {catalog.rule_version!r}\n"
        f"  crosswalk_version: {catalog.crosswalk_version!r}\n"
        f"  on-disk size:      {size_mb:.2f} MB\n"
        f"  in-memory size:    {in_memory / 1e6:.2f} MB"
    )
    return out_path


def _default_rule_version() -> str:
    """Return ``git describe --tags --always`` if in a git repo, else ``'dev'``."""
    git_path = shutil.which("git")
    if git_path is None:
        return "dev"
    try:
        result = subprocess.run(  # noqa: S603
            [git_path, "describe", "--tags", "--always"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or "dev"
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return "dev"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nwm-gdrom",
        description=(
            "Build the GDROM v2 rule catalog .npz from a source directory. "
            "Optionally just prepare the source layout without building."
        ),
    )
    parser.add_argument(
        "-d",
        "--source-dir",
        type=Path,
        required=True,
        help="Directory holding (or to receive) the GDROM v2 source data.",
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Only normalize the source layout under --source-dir; skip the catalog build.",
    )
    parser.add_argument(
        "--source-zip",
        type=Path,
        help="Path to a pre-downloaded GDROM v2 .zip; extracted into "
        "--source-dir if the layout is missing.",
    )
    parser.add_argument(
        "--source-existing",
        type=Path,
        help="Path to an already-extracted GDROM v2 directory; copied into "
        "--source-dir if the layout is missing.",
    )
    parser.add_argument(
        "--hydroshare-doi",
        default=DEFAULT_HYDROSHARE_DOI,
        help=f"Hydroshare DOI to download from when no local source is given "
        f"(default: {DEFAULT_HYDROSHARE_DOI}). Accepts the full DOI, "
        f"a doi.org URL, or a bare 32-char resource ID. Pass an empty "
        f"string to disable the download fallback.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("dist") / "nwm_gdrom_catalog.npz",
        help="Output path for the catalog .npz (default: ./dist/nwm_gdrom_catalog.npz).",
    )
    parser.add_argument(
        "--rule-version",
        default=None,
        help="Version tag baked into the catalog "
        "(default: git describe --tags --always, falling back to 'dev').",
    )
    parser.add_argument(
        "--crosswalk-version",
        default="none",
        help="Crosswalk version tag (default 'none' until the crosswalk module ships).",
    )
    parser.add_argument(
        "--ood-low",
        type=float,
        default=0.01,
        help="Lower OOD percentile (default 0.01).",
    )
    parser.add_argument(
        "--ood-high",
        type=float,
        default=0.99,
        help="Upper OOD percentile (default 0.99).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-extract source files even if --source-dir already has the layout.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the ``nwm-gdrom`` command.

    Prepares the GDROM v2 source layout under ``--source-dir``, then builds
    the catalog unless ``--download-only`` is set.

    Parameters
    ----------
    argv : list of str, optional
        Command-line arguments. Defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        Exit code: 0 on success, 1 when source files are missing or invalid,
        2 when the catalog build fails.
    """
    args = _build_parser().parse_args(argv)

    # Empty-string sentinel disables the Hydroshare fallback (set by user
    # passing `--hydroshare-doi ""`). Otherwise the argparse default kicks in.
    doi: str | None = args.hydroshare_doi or None
    try:
        prepare_source_layout(
            args.source_dir,
            source_zip=args.source_zip,
            source_existing=args.source_existing,
            hydroshare_doi=doi,
            force=args.force,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.download_only:
        print(f"\nLayout prepared at {args.source_dir}; --download-only set, skipping build.")
        return 0

    rule_version = args.rule_version or _default_rule_version()
    try:
        build_release_catalog(
            args.source_dir,
            args.out,
            rule_version=rule_version,
            crosswalk_version=args.crosswalk_version,
            ood_low=args.ood_low,
            ood_high=args.ood_high,
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
