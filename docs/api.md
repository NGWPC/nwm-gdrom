# API reference

The public Python API of `nwm-gdrom` is intentionally small: one function for loading a
catalog, one dataclass for the catalog itself, one exception, and three numeric-code
enums. Everything else lives in submodules and is considered internal. The build-side
helpers, the parser, the OOD-threshold computation, and the reference evaluator may
evolve without notice.

The names below are all importable as attributes of the top-level `nwm_gdrom` namespace;
resolution is lazy via PEP 562 so `import nwm_gdrom` itself is cheap and does not pull
in pandas unless a catalog is actually loaded.

```python
import nwm_gdrom

catalog = nwm_gdrom.load_catalog("nwm_gdrom_catalog.npz")
```

## Loading a catalog

::: nwm_gdrom.load_catalog

## The catalog dataclass

::: nwm_gdrom.GDROMCatalog

## Version-mismatch exception

::: nwm_gdrom.CatalogVersionMismatchError

## Numeric-code enums

::: nwm_gdrom.ModuleKind

::: nwm_gdrom.OpCode

::: nwm_gdrom.VarCode

## Build-side helpers (submodule, internal)

Build-side functions live in submodules (`nwm_gdrom.metadata`, `nwm_gdrom.rule_parser`,
`nwm_gdrom.rule_catalog`, `nwm_gdrom.timeseries`, `nwm_gdrom.evaluator`) and are
considered internal. They remain importable for ad-hoc programmatic builds, but the
recommended way to produce a catalog is the [`nwm-gdrom` CLI](cli.md). The internal
submodule structure may change between releases without notice.
