"""Tests for the `nwm-gdrom` CLI entry point.

The CLI is a thin orchestrator over public package functions. The tests below
exercise the layout-normalization and the build path against the in-repo
fixture set; they do not depend on the bulk GDROM v2 download.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from nwm_gdrom.cli import (
    DEFAULT_HYDROSHARE_DOI,
    _canonical_paths,
    _layout_complete,
    _resource_id_from_doi,
    build_release_catalog,
    main,
    prepare_source_layout,
)


@pytest.fixture
def fake_extracted(
    tmp_path: Path,
    modules_dir: Path,
    conditions_dir: Path,
    metadata_csv: Path,
    cleaned_ts_dir: Path,
) -> Path:
    """A fake "raw extraction" mirroring the GDROM v2 release layout."""
    src = tmp_path / "raw_release"
    rules_root = src / "Operation Rules - GDROMs"
    rules_root.mkdir(parents=True)
    shutil.copytree(modules_dir, rules_root / "modules")
    shutil.copytree(conditions_dir, rules_root / "module_conditions")
    ts_root = src / "Time Series of Reservoir Variables"
    ts_root.mkdir(parents=True)
    shutil.copytree(cleaned_ts_dir, ts_root / "cleaned data for Res-R & Res-L")
    shutil.copy2(metadata_csv, src / "reservoir_metadata.csv")
    return src


def test_prepare_source_layout_from_existing(tmp_path: Path, fake_extracted: Path) -> None:
    """prepare_source_layout copies a raw extraction into the canonical layout."""
    target = tmp_path / "data"
    prepare_source_layout(target, source_existing=fake_extracted)
    assert _layout_complete(target)
    paths = _canonical_paths(target)
    assert paths["metadata_csv"].is_file()
    assert paths["modules_dir"].is_dir()
    assert paths["conditions_dir"].is_dir()
    assert paths["timeseries_dir"].is_dir()


def test_prepare_source_layout_idempotent(tmp_path: Path, fake_extracted: Path) -> None:
    target = tmp_path / "data"
    prepare_source_layout(target, source_existing=fake_extracted)
    # Second call is a no-op once the layout is complete.
    prepare_source_layout(target, source_existing=fake_extracted)
    assert _layout_complete(target)


def test_prepare_source_layout_missing_source_raises(tmp_path: Path) -> None:
    """With no local source AND DOI explicitly disabled, prepare must error."""
    target = tmp_path / "data"
    with pytest.raises(FileNotFoundError, match="no download source resolved"):
        prepare_source_layout(target, hydroshare_doi=None)


def test_prepare_source_layout_bad_existing_raises(tmp_path: Path) -> None:
    target = tmp_path / "data"
    with pytest.raises(FileNotFoundError, match="--source-existing"):
        prepare_source_layout(target, source_existing=tmp_path / "does_not_exist")


def test_build_release_catalog_smoke(tmp_path: Path, fake_extracted: Path) -> None:
    """End-to-end: prepare → build → round-trip."""
    target = tmp_path / "data"
    out = tmp_path / "dist" / "catalog.npz"
    prepare_source_layout(target, source_existing=fake_extracted)
    result = build_release_catalog(target, out, rule_version="test-v0")
    assert result == out
    assert out.is_file()
    # Validate via the package's load_catalog with the version pinned.
    from nwm_gdrom import load_catalog

    catalog = load_catalog(out, expected_rule_version="test-v0")
    assert catalog.n_reservoirs > 0


def test_build_release_catalog_rejects_corrupted_roundtrip(
    tmp_path: Path, fake_extracted: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The build step must fail loudly if any field changes through round-trip.

    Pins the report claim that "the build pipeline rejects any catalog whose
    on-disk representation does not round-trip identically to the in-memory
    original". We force a divergence by mutating the in-memory catalog after
    save but before the load-and-compare step.
    """
    import numpy as np

    from nwm_gdrom import rule_catalog as rc_mod

    target = tmp_path / "data"
    out = tmp_path / "dist" / "catalog.npz"
    prepare_source_layout(target, source_existing=fake_extracted)

    original_save = rc_mod.save_catalog

    def save_then_corrupt(catalog: object, path: Path) -> None:
        original_save(catalog, path)
        # Mutate the in-memory grand_ids so the post-save round-trip cannot
        # match. The save already wrote the correct bytes to disk; the
        # subsequent reload then compares the on-disk truth to a corrupted
        # in-memory original, which must be reported as a round-trip failure.
        catalog.grand_ids = np.zeros_like(catalog.grand_ids)  # type: ignore[attr-defined]

    monkeypatch.setattr("nwm_gdrom.cli.save_catalog", save_then_corrupt)
    with pytest.raises(RuntimeError, match="round-trip"):
        build_release_catalog(target, out, rule_version="test-v0")


def test_main_download_only(tmp_path: Path, fake_extracted: Path) -> None:
    """`nwm-gdrom -d ... --download-only` normalizes layout and returns 0."""
    target = tmp_path / "data"
    rc = main(
        [
            "--source-dir",
            str(target),
            "--source-existing",
            str(fake_extracted),
            "--download-only",
        ]
    )
    assert rc == 0
    assert _layout_complete(target)


def test_main_full_pipeline(tmp_path: Path, fake_extracted: Path) -> None:
    """`nwm-gdrom -d ... --out ... --rule-version ...` runs the full pipeline."""
    target = tmp_path / "data"
    out = tmp_path / "dist" / "catalog.npz"
    rc = main(
        [
            "-d",
            str(target),
            "--source-existing",
            str(fake_extracted),
            "--out",
            str(out),
            "--rule-version",
            "test-v0",
        ]
    )
    assert rc == 0
    assert out.is_file()


def test_main_missing_source_returns_error(tmp_path: Path) -> None:
    """With DOI fallback disabled and no local source, main returns 1."""
    target = tmp_path / "data"
    rc = main(["-d", str(target), "--download-only", "--hydroshare-doi", ""])
    assert rc == 1


# ---------------------------------------------------------------------------
# DOI parser


def test_resource_id_from_full_doi() -> None:
    rid = _resource_id_from_doi("10.4211/hs.5293674cb83b4ec698db0eb4777467b8")
    assert rid == "5293674cb83b4ec698db0eb4777467b8"


def test_resource_id_from_doi_with_prefix() -> None:
    rid = _resource_id_from_doi("doi:10.4211/hs.5293674cb83b4ec698db0eb4777467b8")
    assert rid == "5293674cb83b4ec698db0eb4777467b8"
    rid = _resource_id_from_doi("https://doi.org/10.4211/hs.5293674cb83b4ec698db0eb4777467b8")
    assert rid == "5293674cb83b4ec698db0eb4777467b8"


def test_resource_id_from_bare_id() -> None:
    rid = _resource_id_from_doi("5293674cb83b4ec698db0eb4777467b8")
    assert rid == "5293674cb83b4ec698db0eb4777467b8"


def test_resource_id_uppercase_bare() -> None:
    """Hex resource IDs are case-folded to lowercase."""
    rid = _resource_id_from_doi("5293674CB83B4EC698DB0EB4777467B8")
    assert rid == "5293674cb83b4ec698db0eb4777467b8"


def test_resource_id_rejects_garbage() -> None:
    for bad in ("not-a-doi", "10.4211/hs.too-short", "10.5066/P9PHECFB"):
        with pytest.raises(ValueError, match="Hydroshare resource ID"):
            _resource_id_from_doi(bad)


def test_default_doi_constant_is_the_gdrom_v2_doi() -> None:
    """Pin the documented default in case it gets edited by accident."""
    assert DEFAULT_HYDROSHARE_DOI == "10.4211/hs.5293674cb83b4ec698db0eb4777467b8"
    assert _resource_id_from_doi(DEFAULT_HYDROSHARE_DOI) == "5293674cb83b4ec698db0eb4777467b8"


# ---------------------------------------------------------------------------
# Live download (network-marked; runs only with `pixi run test-network`)


@pytest.mark.network
def test_hydroshare_bag_url_fetches_a_zip(tmp_path: Path) -> None:
    """Download just the first 1 KB and confirm it's a valid zip header.

    Avoids pulling the full 705 MB bag in CI; just verifies the URL responds
    and the body actually starts with the zip magic bytes.
    """
    import urllib.request

    from nwm_gdrom.cli import HYDROSHARE_BAG_URL_TEMPLATE

    rid = _resource_id_from_doi(DEFAULT_HYDROSHARE_DOI)
    url = HYDROSHARE_BAG_URL_TEMPLATE.format(resource_id=rid)
    req = urllib.request.Request(url, headers={"Range": "bytes=0-1023"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        head = resp.read()
    assert len(head) >= 4
    assert head[:4] == b"PK\x03\x04", f"expected zip magic, got {head[:4]!r}"


def test_cli_entry_point_installed() -> None:
    """The `nwm-gdrom` script is registered and runnable via `python -m`."""
    result = subprocess.run(
        [sys.executable, "-m", "nwm_gdrom.cli", "--help"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert "nwm-gdrom" in result.stdout
    assert "--source-dir" in result.stdout
    assert "--download-only" in result.stdout
