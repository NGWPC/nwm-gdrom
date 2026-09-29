# Changelog

All notable changes to this project will be documented in this file. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `nwm-gdrom` CLI that downloads GDROM v2 from HydroShare, normalizes the source layout,
    computes out-of-distribution thresholds, and writes the rule catalog as a compressed
    `.npz`.
- Public API for T-Route: `load_catalog` with version pinning, the `GDROMCatalog` data
    structure, `CatalogVersionMismatchError`, and the `ModuleKind`, `OpCode`, and
    `VarCode` codes.
- Reference evaluator for the flat-array catalog, tested against the GDROM team's
    `rule2model.py` logic.
- Documentation built with MkDocs, including a release-response example notebook.
- CI on Linux, macOS, and Windows with Python 3.12 and 3.14, and a tag-triggered release
    that attaches the catalog to the GitHub Release.

[unreleased]: https://github.com/NGWPC/nwm-gdrom/commits/main
