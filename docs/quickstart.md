# Quickstart

This page walks through the two most common workflows: producing a catalog from the
upstream GDROM v2 release, and loading a prebuilt catalog for consumption.

## Producing a catalog

The end-to-end build is a single command. It downloads the GDROM v2 HydroShare bag
(about 740 MB), normalizes the layout, computes the out-of-distribution thresholds from
training time series, packs the rules into a flat-array catalog, and writes the
compressed `.npz`:

```bash
nwm-gdrom --source-dir ./data --out dist/nwm_gdrom_catalog.npz
```

You'll see progress similar to this:

```
Downloading Hydroshare bag from https://www.hydroshare.org/django_irods/download/bags/...
   739.4 / 739.4 MB  (100%)
Extracting data/_gdromv2_bag.zip → data/_gdromv2_unpacked ...
  copied reservoir_metadata.csv → data/reservoir_metadata.csv
  copied Operation Rules - GDROMs/ → data/rule_files/Operation Rules - GDROMs
  copied Time Series of Reservoir Variables/ → data/time_series
Loading metadata from data/reservoir_metadata.csv ...
  2017 reservoirs have rule files on disk
Computing OOD thresholds from data/time_series/cleaned data for Res-R & Res-L ...
  922 reservoirs got OOD thresholds in 2.60s
Building catalog (rule_version='dev') ...
  built in 8.63s
Writing dist/nwm_gdrom_catalog.npz ...
  wrote 2.12 MB in 0.30s
Validating round-trip ...

Catalog summary:
  reservoirs:        2,017
  modules total:     4,832
  condition branches:25,729
  rule_version:      'dev'
  crosswalk_version: 'none'
  on-disk size:      2.12 MB
  in-memory size:    23.49 MB
```

If you already have the GDROM v2 release locally, skip the download:

```bash
# Pre-extracted directory
nwm-gdrom -d ./data --source-existing /path/to/gdromv2

# Pre-downloaded zip
nwm-gdrom -d ./data --source-zip /path/to/gdromv2.zip
```

See the [CLI reference](cli.md) for the full set of options.

## Pinning a version

By default the `rule_version` stamp is derived from `git describe --tags --always`. For
any catalog that will be archived or consumed in an operational run, pin an explicit
version:

```bash
nwm-gdrom -d ./data --rule-version v0.1.0 --out dist/nwm_gdrom_catalog.npz
```

The version is embedded in the `.npz` and validated at load time. Consumers must declare
the version they expect, and any mismatch raises an exception rather than silently
continuing. See
[Versioning and reproducibility](design.md#versioning-and-reproducibility).

## Loading a catalog

Once you have a catalog file (either from your own build or from the
[GitHub Releases](https://github.com/NGWPC/nwm-gdrom/releases) page), load it through
the package's public API:

```python
from pathlib import Path
import nwm_gdrom

catalog = nwm_gdrom.load_catalog(
    Path("dist/nwm_gdrom_catalog.npz"),
    expected_rule_version="v0.1.0",
)

print(f"Loaded {catalog.n_reservoirs} reservoirs")
print(f"Rule version: {catalog.rule_version}")
print(f"Crosswalk version: {catalog.crosswalk_version}")
```

The returned \[`GDROMCatalog`\][nwm_gdrom.GDROMCatalog] dataclass exposes the flat numpy
arrays directly as attributes: `grand_ids`, `category`, `storage_cap_m3`,
`modules_kind`, `modules_flat`, and so on. Consumers (such as T-Route's reservoir
kernel) read these arrays by reference and walk them in their own evaluation loop. See
[Catalog format](catalog-format.md) for the schema and [API reference](api.md) for the
field-by-field documentation.

## Sanity-checking the catalog you just built

```python
from pathlib import Path
import nwm_gdrom

catalog = nwm_gdrom.load_catalog(Path("dist/nwm_gdrom_catalog.npz"))

# Reservoirs by category
import numpy as np

unique, counts = np.unique(catalog.category, return_counts=True)
for code, count in zip(unique, counts):
    name = catalog.category_name(np.where(catalog.category == code)[0][0])
    print(f"  {name}: {count}")
```

On a full CONUS build this should print:

```
  Res_R: 748
  Res_L: 174
  Res_M: 1095
```

If the counts differ, the upstream metadata table has drifted from the rule-file set on
disk, and the build process will have logged the discrepancy.

## Next steps

- For deployment patterns and integration with T-Route, see [Design notes](design.md).
- For the full set of CLI flags, see the [CLI reference](cli.md).
- For programmatic access details, see the [API reference](api.md).
