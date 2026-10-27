# Schemas

## Contents

1. [Role of this folder](#role-of-this-folder)
2. [File index](#file-index)
3. [Cost-run schemas](#cost-run-schemas)
4. [Trip-preparation schemas](#trip-preparation-schemas)
5. [Navigability schemas](#navigability-schemas)
6. [Rules that apply to every file](#rules-that-apply-to-every-file)

## Role of this folder

Each file here is a header-only CSV template. It carries the required column names in the expected order and no data rows.

The folder exists because the repository never holds production or reference data, yet the column contracts must still be versioned, reviewable and diffable. A schema file is therefore the machine-readable half of the [data contracts](../docs/data-contracts.md); the prose half explains meaning, units and validation.

Use a schema file in three ways:

1. Copy it into a case folder and fill in rows to create a valid input.
2. Compare it against an existing file to find a renamed or missing column.
3. Read the header to see the exact spelling the loaders require, including the abbreviated BIVAS column names in `cost_rates.csv`.

The templates are not validated fixtures. Synthetic, licence-safe test data lives in `tests/fixtures/`.

[Back to contents](#contents)

## File index

| File | Consumed by | Purpose |
| --- | --- | --- |
| `arc_cost_requests.csv` | `run_cost_case.py`, `[inputs].arcs` | One row per arc to cost. |
| `fuel_observations.csv` | `load_fuel_observations_csv` | Litres per hour by vessel and waterway, with three loading anchors. No goods/profile column. |
| `vessel_references.csv` | `load_vessel_references_csv` | Vessel attributes for deterministic proxy selection. |
| `cost_rates.csv` | `load_cost_rates_csv` | BIVAS rates per appearance and vessel class. |
| `ivs_trips.csv` | `load_ivs_trips_csv` | IVS trip-register export used to derive arc requests. |
| `arc_profiles.csv` | `load_arc_profiles_csv` | Directed edges and supplied speed/delay snapshots. |
| `route_sequences.csv` | `load_route_sequences_csv` | Ordered edge traversals per supplied route, starting at sequence zero. |
| `trip_routes.csv` | `load_trip_routes_csv` | Optional explicit trip-to-route associations. |
| `vessel_type_map.csv` | `load_vessel_type_map_csv` | Explicit verified source-code crosswalk; not an assumed BIVAS-ID join. |
| `nstr_appearance_map.csv` | `load_appearance_map_csv` | NSTR chapter to cargo appearance, with a source column. |
| `arc_restrictions.csv` | `load_arc_restrictions_csv` | Structure limits, dangerous-goods permission and closures per directed arc. |
| `navigability_vessels.csv` | `load_navigability_vessels_csv` | Vessel dimensions and dangerous-goods flag per vessel state. |

[Back to contents](#contents)

## Cost-run schemas

The first four files are the four `[inputs]` keys of a [case configuration](../docs/configuration.md#inputs-section). Together they are sufficient to produce costs; no other file is read during a cost run.

```mermaid
flowchart LR
    A[arc_cost_requests.csv] --> R[run_cost_case.py]
    B[fuel_observations.csv] --> R
    C[vessel_references.csv] --> R
    D[cost_rates.csv] --> R
    R --> E[cost_results.csv]
    R --> F[validation_report.json]
    R --> G[run_metadata.json]
```

[Back to contents](#contents)

## Trip-preparation schemas

Trip preparation accepts both IVS layouts and supplied directed sequences. A case may author complete `arc_cost_requests.csv` directly. Version 0.1 OD profiles cannot be renamed without constructing actual edge identities and sequences. See [data contracts](../docs/data-contracts.md).

```mermaid
flowchart LR
    A[ivs_trips.csv] --> P[io.ivs adapters]
    B[arc_profiles.csv and route_sequences.csv] --> P
    R[trip_routes.csv, optional] --> P
    C[vessel_type_map.csv] --> P
    D[nstr_appearance_map.csv] --> P
    P --> E[arc_cost_requests.csv]
    P --> F[trip rejections]
```

`ivs_trips.csv` keeps the original Dutch export column names, because renaming them at the source would break traceability to the register. The adapter maps them to typed fields and records the mapping in `IVS_COLUMNS`. Rejected trips are reported with a coded reason from [Trip rejection reason](../docs/enumerations.md#trip-rejection-reason) rather than dropped.

[Back to contents](#contents)

## Navigability schemas

The navigability evaluator reads restrictions and vessel states, and evaluates supplied routes from `arc_profiles.csv` and `route_sequences.csv`. It never reads cost inputs. See [Navigability](../docs/navigability.md).

```mermaid
flowchart LR
    A[arc_restrictions.csv] --> N[navigability evaluator]
    B[navigability_vessels.csv] --> N
    C[arc_profiles.csv and route_sequences.csv] --> N
    N --> D[route summary and findings]
```

Blank limits mean no limit. Blank flags use `dangerous_goods_permitted=true`, `is_open=true` and `carries_dangerous_goods=false`. A blank air draught is unknown, not zero.

[Back to contents](#contents)

## Rules that apply to every file

- Write files with `index=False`. A generated `Unnamed: 0` column is rejected by the loaders.
- Column order in a real input file is free; the header spelling is not.
- An empty table is rejected, as is a missing required value in a required column.
- Duplicate keys are rejected rather than resolved by row order. This applies to the fuel key, the cost-rate key, the vessel-type mapping and the appearance mapping.
- Controlled values such as CEMT classes and vessel profiles must match [Enumerations](../docs/enumerations.md).
- Do not commit filled versions of these files unless the content is synthetic and licence-safe.

[Back to contents](#contents)
