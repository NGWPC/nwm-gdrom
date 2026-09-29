"""Public API for ``nwm_gdrom``, the consumption surface T-Route reads at runtime.

This package exists to bridge GDROM v2 reservoir operation rules into
T-Route's BMI reservoir kernel. The runtime contract between this package
and T-Route is small: T-Route loads a pre-built ``.npz`` catalog at
``bmi_troute.initialize()``, reads its flat numpy arrays via the
:class:`GDROMCatalog` dataclass, and interprets the per-element
:class:`OpCode` / :class:`VarCode` / :class:`ModuleKind` numeric codes
inside its Cython kernel.

Building the catalog (parsing rule text, computing OOD thresholds, packing
flat arrays, writing the ``.npz``) is a separate concern handled by the
``nwm-gdrom`` CLI. The build helpers live in submodules and remain importable
(``from nwm_gdrom.rule_catalog import build_catalog``), but they are not
part of the stable public API and may evolve without notice.

Public surface, in order of importance to a T-Route integrator:

- :func:`load_catalog`: read a ``.npz`` catalog with version-pinning.
- :class:`GDROMCatalog`: the data structure the kernel reads.
- :class:`CatalogVersionMismatchError`: raised when ``expected_rule_version``
  or ``expected_crosswalk_version`` does not match what the file carries.
- :class:`ModuleKind`, :class:`OpCode`, :class:`VarCode`: numeric codes the
  kernel uses to interpret packed arrays.

The public names above are resolved lazily on first attribute access (PEP
562 ``__getattr__``), so ``import nwm_gdrom`` itself is fast and only pulls
in pandas/numpy when the catalog is actually loaded. Set ``NWM_GDROM_EAGER=1``
to force-resolve every public name at import time (useful for profiling).
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nwm_gdrom.rule_catalog import (
        CatalogVersionMismatchError,
        GDROMCatalog,
        load_catalog,
    )
    from nwm_gdrom.rule_parser import ModuleKind, OpCode, VarCode

try:
    __version__ = version("nwm-gdrom")
except PackageNotFoundError:  # not installed (e.g. running from source tree)
    __version__ = "0.0.0+unknown"


# Map public name → (submodule, attribute) for lazy resolution.
# `attribute is None` means "the submodule itself" (we don't use that here yet,
# but the slot is wired so adding submodule re-exports later is one line).
_LAZY: dict[str, tuple[str, str | None]] = {
    "CatalogVersionMismatchError": ("nwm_gdrom.rule_catalog", "CatalogVersionMismatchError"),
    "GDROMCatalog": ("nwm_gdrom.rule_catalog", "GDROMCatalog"),
    "load_catalog": ("nwm_gdrom.rule_catalog", "load_catalog"),
    "ModuleKind": ("nwm_gdrom.rule_parser", "ModuleKind"),
    "OpCode": ("nwm_gdrom.rule_parser", "OpCode"),
    "VarCode": ("nwm_gdrom.rule_parser", "VarCode"),
}


__all__ = [
    "CatalogVersionMismatchError",
    "GDROMCatalog",
    "ModuleKind",
    "OpCode",
    "VarCode",
    "__version__",
    "load_catalog",
]


def __dir__() -> list[str]:
    return __all__


def __getattr__(name: str) -> object:
    if name in _LAZY:
        import importlib

        module_path, attr = _LAZY[name]
        mod = importlib.import_module(module_path)
        value = mod if attr is None else getattr(mod, attr)
        globals()[name] = value  # cache so future accesses skip the lookup
        return value
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


# Eager-resolve override: set NWM_GDROM_EAGER=1 (any non-empty, non-"0" value)
# to load all lazy members immediately. Used by tests and CI to surface any
# import-time errors that lazy resolution would defer to first access.
if os.environ.get("NWM_GDROM_EAGER", "") not in ("", "0"):
    for _name in _LAZY:
        __getattr__(_name)
