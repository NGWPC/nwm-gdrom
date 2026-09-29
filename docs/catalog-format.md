# Catalog format

This page describes the on-disk catalog layout, the rule grammar it encodes, and the
unit conventions used throughout. The information here is sufficient for an integrator
who needs to consume the catalog from a non-Python language or who needs to validate a
third-party reader.

## File format

The catalog is a single compressed NumPy archive (`.npz`) containing a fixed set of
named arrays plus two version-stamp scalars. The file is loaded with
`numpy.load(path, allow_pickle=False)`, so no executable code is stored in the archive
and loading it does not execute arbitrary code.

A full-CONUS catalog at GDROM v2's current size (2,017 reservoirs, 4,832 modules, 25,729
dispatcher branches) compresses to about 2.1 megabytes on disk and occupies about 23.5
megabytes resident.

## Compressed Sparse Row (CSR) layout

The catalog packs variable-length nested sequences (reservoirs → modules → packed
parameters, and reservoirs → dispatcher branches → packed predicates) in a CSR layout.
CSR is the standard scheme for sequences-of-sequences with variable lengths: it
eliminates pointer indirection, enables vectorized iteration in a tight loop, and
serializes naturally as a small set of flat arrays.

Three levels of indirection appear in the catalog:

| Outer level | Inner level         | Offsets array             | Payload array                 |
| ----------- | ------------------- | ------------------------- | ----------------------------- |
| Reservoirs  | Modules             | `reservoir_modules_start` | `modules_kind`, `modules_ptr` |
| Modules     | Packed parameters   | `modules_ptr`             | `modules_flat`                |
| Reservoirs  | Dispatcher branches | `conditions_branch_start` | `conditions_ptr`              |
| Dispatchers | Packed predicates   | `conditions_ptr`          | `conditions_flat`             |

Reservoirs without a dispatcher have an empty range in `conditions_branch_start` (i.e.,
`conditions_branch_start[i+1] == conditions_branch_start[i]`).

## Per-reservoir scalar fields

All length `N`, where `N` is the number of in-scope reservoirs sorted by ascending GRanD
identifier.

| Field               | Dtype        | Description                                                                       |
| ------------------- | ------------ | --------------------------------------------------------------------------------- |
| `grand_ids`         | int64        | GRanD identifier (synthetic identifiers ≥ 10000 for the 111 non-GRanD additions). |
| `state`             | 2-byte ASCII | Two-letter postal code; sentinel `"  "` when unknown.                             |
| `category`          | int8         | 0 = Res_R; 1 = Res_L; 2 = Res_M.                                                  |
| `storage_cap_m3`    | float32      | Maximum reservoir storage in cubic meters (converted from acre-feet at build).    |
| `min_storage_m3`    | float32      | Dead-pool / minimum operating storage in cubic meters; default zero.              |
| `ood_inflow_p01_af` | float32      | Lower OOD inflow threshold in acre-feet per day; −∞ when unknown (trigger off).   |
| `ood_inflow_p99_af` | float32      | Upper OOD inflow threshold in acre-feet per day; +∞ when unknown (trigger off).   |

## Module-level fields

One entry per module; total length is the sum of module counts across reservoirs.

| Field          | Dtype          | Description                                                        |
| -------------- | -------------- | ------------------------------------------------------------------ |
| `modules_kind` | int8           | 0 = EXPR (single release expression); 1 = TREE (ordered branches). |
| `modules_ptr`  | int32, len M+1 | CSR offsets into `modules_flat`.                                   |
| `modules_flat` | float64        | Packed parameters; layout depends on `modules_kind` (see below).   |

### EXPR module payload

For an EXPR module, the slice `modules_flat[modules_ptr[i]:modules_ptr[i+1]]` is exactly
four floats encoding the unified affine release expression:

```
Release = max(a_inflow × Inflow + a_storage × Storage + c, clamp_min)
```

| Offset | Field       | Description                                                                                   |
| -----: | ----------- | --------------------------------------------------------------------------------------------- |
|      0 | `a_inflow`  | Coefficient on inflow.                                                                        |
|      1 | `a_storage` | Coefficient on storage.                                                                       |
|      2 | `c`         | Constant term.                                                                                |
|      3 | `clamp_min` | Lower bound; `−inf` for "no clamp", `0` for the max-of-zero clamp present in the source rule. |

This single representation subsumes all five release-expression forms observed in the
GDROM v2 corpus (constant, single-variable linear, multivariate linear, max-clamped,
NaN).

### TREE module payload

For a TREE module, the slice is a sequence of branches. Each branch has the layout:

```
[n_predicates, var_0, op_0, threshold_0, ..., var_(n-1), op_(n-1), threshold_(n-1), a_inflow, a_storage, c, clamp_min]
```

Branches are evaluated in source order; the first branch whose predicates all hold
supplies the release expression for that row. If no branch matches, the evaluator
returns `None`, which downstream T-Route code interprets as a trigger to fall back to
the level-pool physics.

Variable codes in TREE branches are restricted to `Inflow` (0) and `Storage` (1); `PDSI`
and `DOY` are reserved for dispatcher branches.

## Dispatcher fields

One entry per dispatcher branch; reservoirs without a dispatcher have an empty range.

| Field                     | Dtype          | Description                                  |
| ------------------------- | -------------- | -------------------------------------------- |
| `conditions_branch_start` | int32, len N+1 | CSR offsets into the dispatcher branch list. |
| `conditions_ptr`          | int32, len B+1 | CSR offsets into `conditions_flat`.          |
| `conditions_flat`         | float64        | Packed branches.                             |

Each dispatcher branch in `conditions_flat` has the layout:

```
[n_predicates, var_0, op_0, threshold_0, ..., var_(n-1), op_(n-1), threshold_(n-1), module_id]
```

Variable codes in dispatcher branches may be `Inflow` (0), `Storage` (1), `PDSI` (2), or
`DOY` (3). The trailing slot is the target module identifier (as a float, but
integer-valued; integer round-trip is exact for the value ranges encountered).

Branches with zero predicates never match. This mirrors the truthiness convention of the
GDROM authors' reference simulator (an empty `if ()` is false) and is the correct
behavior for the placeholder branches that appear in some Res_M dispatchers.

## Version stamps

The catalog also embeds two scalar string fields:

| Field               | Description                                                                                            |
| ------------------- | ------------------------------------------------------------------------------------------------------ |
| `rule_version`      | Version of the upstream GDROM release the catalog was built from.                                      |
| `crosswalk_version` | Version of the GRanD-to-NHF identifier crosswalk. Defaults to `none` until the crosswalk module ships. |

Both are validated at load time against caller-provided expectations. A mismatch raises
`CatalogVersionMismatchError`. See
[Versioning and reproducibility](design.md#versioning-and-reproducibility) for the
recommended versioning policy.

## Numeric codes used in payloads

### Variable codes

| Code | Variable | Allowed in                                |
| ---: | -------- | ----------------------------------------- |
|    0 | Inflow   | TREE module branches; dispatcher branches |
|    1 | Storage  | TREE module branches; dispatcher branches |
|    2 | PDSI     | Dispatcher branches only                  |
|    3 | DOY      | Dispatcher branches only                  |

### Comparison operator codes

| Code | Operator              |
| ---: | --------------------- |
|    0 | less than or equal    |
|    1 | less than             |
|    2 | greater than or equal |
|    3 | greater than          |

### Module kind codes

| Code | Kind | Payload                                                                                  |
| ---: | ---- | ---------------------------------------------------------------------------------------- |
|    0 | EXPR | Four floats `[a_inflow, a_storage, c, clamp_min]`.                                       |
|    1 | TREE | Ordered branches; each branch is predicate triples plus a four-float release expression. |

### Reservoir category codes

| Code | Category | Description                                                           | Corpus count |
| ---: | -------- | --------------------------------------------------------------------- | ------------ |
|    0 | Res_R    | Data-rich, locally trained.                                           | 748          |
|    1 | Res_L    | Locally fine-tuned through transfer learning.                         | 174          |
|    2 | Res_M    | Rules transferred from analogous reservoirs without local validation. | 1,095        |

## Unit conventions

| Quantity                          | Unit                  | Notes                                                |
| --------------------------------- | --------------------- | ---------------------------------------------------- |
| Inflow and Release (GDROM-native) | acre-feet per day     | Used inside rule evaluation.                         |
| Storage (GDROM-native)            | acre-feet             | Used inside rule evaluation.                         |
| Storage (catalog representation)  | cubic meters          | Converted from acre-feet by × 1233.48 at build time. |
| PDSI                              | dimensionless, signed | Drought index in the range ~[−5, +5] physically.     |
| DOY                               | integer               | Day of year, 1 to 366.                               |

The conversion factor is exposed as `nwm_gdrom.metadata.ACRE_FT_TO_M3 = 1233.48`.
Consumers that need release values in cubic meters per second should apply
`× 1233.48 / 86400` at the evaluation boundary.

## Sentinel values

| Sentinel                               | Meaning                                                            |
| -------------------------------------- | ------------------------------------------------------------------ |
| `clamp_min = −∞`                       | No clamp applied; the affine release expression is returned as is. |
| `clamp_min = 0`                        | Explicit max-of-zero clamp present in the source rule.             |
| `ood_inflow_p01_af = −∞`               | Lower out-of-distribution trigger disabled.                        |
| `ood_inflow_p99_af = +∞`               | Upper out-of-distribution trigger disabled.                        |
| `state = "  "`                         | State unknown.                                                     |
| Dispatcher branch with zero predicates | Never matches.                                                     |

## Precision

All thresholds and release coefficients are stored in **64-bit floating point**
(`float64`). Per-reservoir scalars (storage capacity, OOD thresholds) are stored in
32-bit (`float32`), where the dynamic range is wide enough that single precision is
harmless. Integer codes (variable, operator, predicate counts, module identifiers) are
stored in `float64` slots and round-trip exactly for the value ranges encountered.

The choice of `float64` for predicate thresholds is deliberate. See
[Numerical precision](design.md#numerical-precision-and-an-empirically-driven-design-correction)
in the design notes for the rationale.
