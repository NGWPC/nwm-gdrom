# CLI reference

The `nwm-gdrom` command is the canonical entry point for producing a catalog from the
upstream GDROM v2 release. It orchestrates source resolution, layout normalization,
out-of-distribution threshold computation, catalog construction, and round-trip
validation in a single invocation.

## Synopsis

```
nwm-gdrom -d SOURCE_DIR [--out OUT_PATH]
          [--source-existing PATH | --source-zip PATH | --hydroshare-doi DOI]
          [--rule-version VERSION] [--crosswalk-version VERSION]
          [--ood-low FRAC] [--ood-high FRAC]
          [--download-only] [--force]
```

## Common workflows

```bash
# Default: download the GDROM v2 release from HydroShare and build the catalog.
nwm-gdrom -d ./data --out dist/nwm_gdrom_catalog.npz

# Build only, skipping the download because the source layout already exists.
nwm-gdrom -d ./data --out dist/nwm_gdrom_catalog.npz --rule-version v0.1.0

# Just normalize the upstream layout into the canonical structure, no catalog build.
nwm-gdrom -d ./data --download-only

# Use a local archive instead of downloading.
nwm-gdrom -d ./data --source-zip ~/Downloads/gdromv2.zip
nwm-gdrom -d ./data --source-existing /path/to/pre-extracted/gdromv2
```

If you're using the bundled pixi tasks:

```bash
pixi run -e release prepare-data    # download + normalize (no build)
pixi run -e release build-catalog   # full pipeline
```

## Flags

### `-d, --source-dir DIR` (required)

Working directory for the source layout. After a successful run, this directory contains
the canonical structure:

```
{source-dir}/
├── reservoir_metadata.csv
├── rule_files/
│   └── Operation Rules - GDROMs/
│       ├── modules/{grand_id}_{module_id}.txt
│       └── module_conditions/{grand_id}.txt
└── time_series/
    └── cleaned data for Res-R & Res-L/{grand_id}.csv
```

The directory is created if it doesn't exist. If it already has the canonical layout, no
source resolution is performed (unless `--force` is passed).

### `--out PATH`

Output path for the catalog `.npz`. Defaults to `dist/nwm_gdrom_catalog.npz`. The parent
directory is created if missing.

### Source resolution flags

Exactly zero or one of the following may be passed; resolution order is fixed at
*existing > zip > DOI*. If the source layout is already complete at `--source-dir`, none
of these is needed.

#### `--source-existing DIR`

Pre-extracted GDROM v2 directory. The CLI copies the relevant subtrees into the
canonical layout. Useful for offline environments or when the archive is already on
local disk.

#### `--source-zip FILE`

Pre-downloaded GDROM v2 zip archive. The CLI extracts to a working subdirectory under
`--source-dir` and then normalizes the layout.

#### `--hydroshare-doi DOI`

HydroShare DOI to download from. Accepts the full DOI (`10.4211/hs.<32-hex>`), a
`doi.org` URL, or a bare 32-character hexadecimal resource ID. Defaults to the GDROM v2
release DOI `10.4211/hs.5293674cb83b4ec698db0eb4777467b8` (about 740 MB).

Pass an empty string (`--hydroshare-doi ""`) to disable the download fallback. With no
other source flag, a missing local source then raises an error rather than fetching from
the network, which is useful in CI or air-gapped environments.

### `--rule-version STRING`

Version tag embedded in the catalog and validated at load. Defaults to the output of
`git describe --tags --always`, falling back to the literal string `dev` if git is
unavailable. For any catalog that will be archived or consumed in an operational run,
pin an explicit semantic version.

### `--crosswalk-version STRING`

Version tag for the GRanD-to-NHF crosswalk. Defaults to `none` until the crosswalk
module ships. Validated at load when consumers supply `expected_crosswalk_version`.

### `--ood-low FRACTION`, `--ood-high FRACTION`

Lower and upper percentiles for the out-of-distribution inflow thresholds, expressed as
fractions in [0, 1]. Defaults are `0.01` and `0.99`. Reservoirs without training time
series (the Res_M category) get the disabled sentinels −∞ and +∞ in their place.

### `--download-only`

Stop after the source-layout step; skip the catalog build. The exit code is zero if the
layout is complete at `--source-dir`. Useful for CI prebuild caching of the upstream
release.

### `--force`

Re-run the source resolution even if `--source-dir` already has the canonical layout.
The existing layout files are overwritten.

## Source resolution order

When `--source-dir` is missing pieces of the canonical layout, the CLI resolves the
source in this order:

1. **`--source-existing`**: copy from a pre-extracted directory. Local, fastest.
1. **`--source-zip`**: extract a pre-downloaded archive. Local, no network.
1. **`--hydroshare-doi`**: stream-download the bag from HydroShare and extract it.
    Requires network egress.

The first flag whose argument is present wins. If `--source-dir` is already complete and
`--force` is not set, no source resolution runs at all.

## Output and validation

After the catalog is written, the CLI re-loads it from disk and asserts that every array
field round-trips identically (per-element bit equality for integers,
`np.array_equal(equal_nan=True)` for floats), that the dtype and shape of each field
match the in-memory original, and that both embedded version stamps round-trip. Any
divergence aborts the build with a non-zero exit status.

On success, a summary block reports reservoir count, module count, dispatcher branch
count, embedded versions, and both on-disk and in-memory sizes.

## Exit codes

| Code | Meaning                                                                |
| ---: | ---------------------------------------------------------------------- |
|    0 | Success.                                                               |
|    1 | Source resolution failed (e.g., download error, missing local source). |
|    2 | Build or round-trip validation failed.                                 |
