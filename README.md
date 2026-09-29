# NWM GDROM: GDROM reservoir operation rules for the National Water Model

[![CI](https://github.com/NGWPC/nwm-gdrom/actions/workflows/test.yml/badge.svg)](https://github.com/NGWPC/nwm-gdrom/actions/workflows/test.yml)
[![Docs](https://github.com/NGWPC/nwm-gdrom/actions/workflows/docs.yml/badge.svg)](https://github.com/NGWPC/nwm-gdrom/actions/workflows/docs.yml)
[![License: BSD 2-Clause and Apache 2.0](https://img.shields.io/badge/License-BSD%202--Clause%20%2B%20Apache%202.0-blue.svg)](LICENSE)

GDROM v2 reservoir operation rules packaged for the National Water Model's T-Route
routing engine.

`nwm-gdrom` transforms the public
[GDROM v2 dataset](https://doi.org/10.4211/hs.5293674cb83b4ec698db0eb4777467b8) (learned
daily operating rules for 2,017 regulated reservoirs across the contiguous United
States) into a single versioned binary catalog (`.npz`) that T-Route loads once at
initialization. The catalog occupies about 23 megabytes resident, compresses to about 2
megabytes on disk, and loads in tens of milliseconds.

## Install

```bash
pip install git+https://github.com/NGWPC/nwm-gdrom.git
```

For development with the bundled [pixi](https://pixi.sh) environment:

```bash
git clone https://github.com/NGWPC/nwm-gdrom.git
cd nwm-gdrom
pixi install -e dev
```

## Generate the catalog

Build the catalog end-to-end from the upstream HydroShare release. This downloads the
GDROM v2 bag (~740 MB), normalizes the layout, computes the out-of-distribution
thresholds from the training time series, packs the rules into a flat-array catalog, and
writes the compressed `.npz`. Total wall time is dominated by the network download; CPU
work is about 15 seconds once the data is local:

```bash
nwm-gdrom --source-dir ./data --out dist/nwm_gdrom_catalog.npz --rule-version v0.1.0
```

If you already have the GDROM v2 release locally, skip the download:

```bash
# Pre-extracted directory
nwm-gdrom -d ./data --source-existing /path/to/gdromv2 --rule-version v0.1.0

# Pre-downloaded zip
nwm-gdrom -d ./data --source-zip /path/to/gdromv2.zip --rule-version v0.1.0
```

The `--rule-version` flag pins the version stamp that gets embedded in the `.npz`;
defaults to `git describe --tags --always`.

## Load the catalog

```python
import nwm_gdrom

catalog = nwm_gdrom.load_catalog(
    "dist/nwm_gdrom_catalog.npz",
    expected_rule_version="v0.1.0",
)

print(f"{catalog.n_reservoirs} reservoirs, rule_version={catalog.rule_version}")
# 2017 reservoirs, rule_version=v0.1.0

# The dataclass exposes the flat numpy arrays directly; T-Route's reservoir
# kernel reads them by reference in its compute loop.
print(catalog.grand_ids[:5])  # int64 GRanD identifiers, sorted
print(catalog.category[:5])  # 0=Res_R, 1=Res_L, 2=Res_M
print(catalog.storage_cap_m3[:5])  # float32, cubic meters
```

The `expected_rule_version` argument is validated against the version embedded in the
`.npz` at build time; a mismatch raises `CatalogVersionMismatchError` rather than
silently continuing.

## Use a prebuilt catalog

For T-Route integrators who don't need to build locally, every tagged release publishes
the prebuilt catalog as a GitHub Release asset:

```bash
# Latest release
curl -L -o nwm_gdrom_catalog.npz \
  https://github.com/NGWPC/nwm-gdrom/releases/latest/download/nwm_gdrom_catalog.npz

# Specific version
curl -L -o nwm_gdrom_catalog.npz \
  https://github.com/NGWPC/nwm-gdrom/releases/download/v0.1.0/nwm_gdrom_catalog.npz
```

## Documentation

The documentation lives in `docs/`; serve it locally with `pixi run -e docs docs-serve`:

- [Installation](docs/installation.md): pip and pixi setups, system requirements.
- [Quickstart](docs/quickstart.md): produce and load a catalog in five minutes.
- [CLI reference](docs/cli.md): the `nwm-gdrom` command and all its flags.
- [Catalog format](docs/catalog-format.md): on-disk schema, rule grammar, unit
    conventions.
- [API reference](docs/api.md): public Python API.
- [Design notes](docs/design.md): context, T-Route integration, and key design
    decisions.

## Repository layout

```
nwm-gdrom/
├── src/nwm_gdrom/      Package source (incl. cli.py for the `nwm-gdrom` command)
├── tests/              Pytest suite
│   └── data/           Small fixture set (12 representative reservoirs), version-controlled
├── docs/               mkdocs site
└── .github/workflows/  CI for tests, docs, and releases
```

The full GDROM v2 source data is gitignored under `./data/` and downloaded on demand via
the CLI.

## License

The software is licensed under BSD 2-Clause and Apache 2.0, copyright 2024-2026 Raytheon
Company; see [LICENSE](LICENSE) for the full terms. The GDROM v2 dataset itself is a
separate work; see its
[HydroShare resource](https://doi.org/10.4211/hs.5293674cb83b4ec698db0eb4777467b8) for
its own citation and license.
