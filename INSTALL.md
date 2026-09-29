# Installation instructions

`nwm-gdrom` requires Python 3.12 or newer. The runtime dependencies are `numpy >= 2` and
`pandas >= 2`; no geospatial libraries are needed for the catalog build and consumption
paths.

## With pip

```bash
pip install git+https://github.com/NGWPC/nwm-gdrom.git
```

There is no PyPI publication; releases are GitHub-only. To pin to a specific tagged
release, append `@<tag>` to the URL.

To install with the optional crosswalk dependencies (only needed once the GRanD-to-NHF
crosswalk module ships):

```bash
pip install "nwm-gdrom[crosswalk] @ git+https://github.com/NGWPC/nwm-gdrom.git"
```

## With pixi

If you maintain the repository as a [pixi](https://pixi.sh) project, the canonical
environments are pre-configured. Clone the repo and run:

```bash
git clone https://github.com/NGWPC/nwm-gdrom.git
cd nwm-gdrom

pixi install -e dev          # development env: numpy, pandas, jupyter, matplotlib
pixi install -e release      # minimal env for building the catalog
pixi install -e crosswalk    # adds geopandas, shapely, pyogrio
```

To run the test suite:

```bash
pixi run -e test314 test          # fast tests on the small fixture set
pixi run -e test314 test-all      # full corpus + network tests
```

## Verifying the install

```bash
nwm-gdrom --help
```

Should print the CLI usage. If you prefer the Python API:

```python
import nwm_gdrom

print(nwm_gdrom.__version__)
```

## Getting the prebuilt catalog

If you only need the binary catalog (the most common case for T-Route integrators),
download the latest release artifact directly:

```bash
curl -L -o nwm_gdrom_catalog.npz \
  https://github.com/NGWPC/nwm-gdrom/releases/latest/download/nwm_gdrom_catalog.npz
```

Or pin a specific version:

```bash
curl -L -o nwm_gdrom_catalog.npz \
  https://github.com/NGWPC/nwm-gdrom/releases/download/v0.1.0/nwm_gdrom_catalog.npz
```

You still need `nwm-gdrom` installed (or the `numpy` package directly) to load the
catalog.

## System requirements

The catalog build itself is modest:

| Resource | Requirement                                                                                                                                                     |
| -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Disk     | ~750 MB free for the GDROM v2 download, ~25 MB for the source layout, ~2 MB for the output catalog.                                                             |
| Memory   | Peak ~250 MB during build. The catalog resident size is ~23 MB.                                                                                                 |
| Network  | One-time ~740 MB download from HydroShare. Skippable if you already have the source archive locally (see [CLI reference](docs/cli.md#source-resolution-order)). |
| Time     | About 15 seconds for the parse, threshold-computation, and pack steps once the source layout is on disk. The HydroShare download dominates wall time.           |
