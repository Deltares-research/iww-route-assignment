# Enumerations and controlled vocabularies

Every value in this page is a closed set. Any other value raises `ValidationError` at the boundary rather than silently defaulting. All enumerations are `StrEnum` members defined in `src/iww_route_assignment/domain/enums.py`, so the string in a CSV cell, a TOML key or an output column is identical to the Python member value.

## Contents

1. [How to read this page](#how-to-read-this-page)
2. [Vessel profile](#vessel-profile)
3. [Loading category](#loading-category)
4. [Fuel resolution policy](#fuel-resolution-policy)
5. [Fuel resolution method](#fuel-resolution-method)
6. [Confidence](#confidence)
7. [Trip rejection reason](#trip-rejection-reason)
8. [Waterway CEMT classes](#waterway-cemt-classes)
9. [Vessel classes and families](#vessel-classes-and-families)

## How to read this page

Each table gives the literal value, its meaning and where it appears.

| Direction | Meaning |
| --- | --- |
| Input | You choose the value in a CSV cell or a case TOML key. |
| Output | The library writes the value into a result column or report. |
| Internal | The value exists in the Python API but has no direct CSV column. |

[Back to contents](#contents)

## Vessel profile

`VesselProfile`. Direction: input and output.

| Value | Meaning |
| --- | --- |
| `general` | Dry bulk, liquid bulk and general cargo fuel curves. |
| `container` | Container fuel curves, kept strictly separate from general rows. |

General and container observations are never merged or averaged. A container request can only be met by container source rows.

Where it appears:

- `fuel_observations.csv`, column `vessel_profile`. Required.
- `arc_cost_requests.csv`, column `vessel_profile`. Optional override.
- Result columns `fuel_requested_profile` and `fuel_selected_profile`.

If the optional request column is blank, the profile is derived from `appearance` by `profile_for_appearance`: an appearance whose text contains `container`, compared case-insensitively, gives `container`, and every other appearance gives `general`. Set the column explicitly when that rule is not correct for your case.

[Back to contents](#contents)

## Loading category

`LoadingCategory`. Direction: internal and output.

| Value | Meaning | Default loading-ratio anchor |
| --- | --- | --- |
| `light` | Empty or lightly loaded ship. | 0.0 |
| `medium` | Half-loaded ship. | 0.5 |
| `heavy` | Fully loaded ship. | 1.0 |

These are ship-loading states, not waterway or engine categories. They name the three observed columns `diesel_light_l_per_h`, `diesel_medium_l_per_h` and `diesel_heavy_l_per_h`.

The CSV runner never sets a category directly. It passes the continuous `loading_ratio` column, and the provider interpolates linearly between the two adjacent anchors. The Python API accepts either `loading_ratio` or `loading_category`, and exactly one of the two must be supplied.

Anchors are configurable through `LoadingAnchors`, which requires `0 <= light < medium < heavy <= 1`. The default anchors are a documented assumption still awaiting confirmation against the source documentation.

Result columns `fuel_lower_loading_category` and `fuel_upper_loading_category` report the two categories used. Equal values mean no loading interpolation took place.

[Back to contents](#contents)

## Fuel resolution policy

`FuelResolutionPolicy`. Direction: input. Set through `run.fuel_policy` in a case TOML, or the `policy` argument of `load_fuel_provider` and `TableFuelConsumptionProvider`. Default `audited_proxy`.

This is the single most consequential run option, because it decides whether a gap in the fuel table stops the run or is filled with a recorded substitute.

| Value | Allowed steps | Behaviour on a gap |
| --- | --- | --- |
| `strict` | 1 and 2 only | Raises `FuelCoverageError` and the run stops. |
| `audited_proxy` | 1 to 5 | Substitutes the nearest deterministic option and records how. |

The resolution ladder, in order, stopping at the first step that succeeds:

| Step | Description | Method recorded | Confidence |
| --- | --- | --- | --- |
| 1 | Exact vessel class, waterway class and profile. | `observed` or `loading_interpolated` | `high` |
| 2 | Observed waterway classes both below and above the request, interpolated on CEMT rank. | `waterway_interpolated` | `medium` |
| 3 | Nearest observed waterway class when the request lies outside the observed range. | `waterway_proxy` | `low` |
| 4 | Nearest vessel class in the same family, then any family, by weighted attribute distance. | `vessel_proxy` | `low` |
| 5 | Explicit compatibility provider, only through `FallbackFuelConsumptionProvider`. | `stream_fallback` | `fallback` |

Choose `strict` when you must prove that every cost figure rests on a real observation, for example a reference or acceptance run. Choose `audited_proxy` for exploratory and full-network runs, and then control quality with `run.max_low_or_fallback_share` rather than by turning proxies off.

Step 3 selects the nearest observed boundary and copies it. It never extrapolates a trend beyond the observed range. Step 4 is only available for vessel classes that also appear in `vessel_references.csv`, because it needs the attributes to measure distance; without a reference row the provider raises `FuelCoverageError` even under `audited_proxy`.

### Which steps run on every request

The numbering is a fallback order, not five steps executed in sequence. Exactly one path is taken per request.

| Always attempted, every request | Attempted only if the previous attempt failed |
| --- | --- |
| Input validation. | Step 2, bounded waterway interpolation. |
| Vessel and profile row lookup, which decides whether step 4 is even considered. | Step 3, nearest waterway boundary, `audited_proxy` only. |
| Step 1, exact waterway match, once vessel rows are found. | Step 4, nearest vessel proxy, `audited_proxy` only, and only for a vessel that also has a `vessel_references.csv` row. |
| Loading-anchor interpolation against whichever waterway row is selected. This is not policy-gated. | Step 5, an explicit `FallbackFuelConsumptionProvider` wrapper, triggered only when the primary raises `FuelCoverageError`. |

Under `strict`, only steps 1 and 2 are ever reachable; anything that would need step 3 or 4 raises `FuelCoverageError` and stops the run immediately, because `run_cost_case.py` does not catch it. No output files are written for that run.

`load_fuel_provider`, and therefore the standard case runner, never wraps the table provider in `FallbackFuelConsumptionProvider`. Step 5 is only reachable from code that constructs that wrapper explicitly; it is dead in the standard runner.

[Back to contents](#contents)

## Fuel resolution method

`FuelResolutionMethod`. Direction: output only, in result column `fuel_method` and in the `method_counts` block of `validation_report.json`. It records the most consequential derivation used, so a vessel proxy that also required loading interpolation reports `vessel_proxy`.

| Value | Meaning |
| --- | --- |
| `observed` | A source row was used directly, with no interpolation. |
| `loading_interpolated` | One source row, interpolated between two loading anchors. |
| `waterway_interpolated` | Two source rows, interpolated between bounding CEMT classes. |
| `waterway_proxy` | The nearest observed CEMT class was copied without extrapolation. |
| `vessel_proxy` | A different vessel class supplied the rate. |
| `stream_fallback` | The primary provider had no coverage and a labelled compatibility provider answered. |

Every method also writes `fuel_source_row_ids` and `fuel_source_datasets`, so any figure can be traced back to its source rows. `vessel_proxy` additionally writes `fuel_proxy_distance`, `fuel_proxy_distance_terms` and `fuel_proxy_weights_version`.

[Back to contents](#contents)

## Confidence

`Confidence`. Direction: output only, in result column `fuel_confidence` and in the `confidence_counts` block of `validation_report.json`. It is a qualitative label attached by the resolution ladder, not a statistical interval.

| Value | Meaning | Counts towards the share limit |
| --- | --- | --- |
| `high` | Exact source row, with loading interpolation at most. | No |
| `medium` | Interpolated between two bounding observed waterway classes. | No |
| `low` | A nearest waterway boundary or a vessel proxy was needed. | Yes |
| `fallback` | An external compatibility provider answered. | Yes |

`low` and `fallback` rows are summed into `low_or_fallback_count` and `low_or_fallback_share` by `summarise_fuel_resolutions`. The case runner compares that share with `run.max_low_or_fallback_share` and fails the run when the limit is exceeded, after writing the outputs so the breach can be inspected.

[Back to contents](#contents)

## Trip rejection reason

`TripRejectionReason`. Direction: output only, used by the IVS trip preparation adapters in `iww_route_assignment.io.ivs` when a source trip cannot become an arc-cost request. Rejections are reported, never dropped silently.

| Value | Meaning |
| --- | --- |
| `unknown_vessel_type` | The source ship-type identifier is absent from `vessel_type_map.csv`. |
| `missing_route_profile` | No route profile exists for the origin and destination pair. |
| `missing_cargo_weight` | The transported weight is absent and the trip is not recorded as empty. |
| `non_positive_capacity` | The recorded capacity is zero or negative, so no loading ratio can be derived. |
| `unknown_appearance` | The cargo code does not map to an appearance in `nstr_appearance_map.csv`. |
| `invalid_request` | The assembled request failed domain validation, for example a loading ratio outside 0 to 1. |

[Back to contents](#contents)

## Waterway CEMT classes

Defined by `CEMT_ORDER` in `domain/classifications.py`. The order is meaningful: the index is the rank used for interpolation and nearest-class selection.

| Rank | Class |
| --- | --- |
| 0 | `0` |
| 1 | `I` |
| 2 | `II` |
| 3 | `III` |
| 4 | `IV` |
| 5 | `Va` |
| 6 | `Vb` |
| 7 | `VIa` |
| 8 | `VIb` |
| 9 | `VIc` |
| 10 | `VII` |

Accepted aliases, normalised case-insensitively: `IVA` to `IV`, `VA` to `Va`, `VB` to `Vb`, `VIA` to `VIa`, `VIB` to `VIb`, `VIC` to `VIc`. Any other value raises `ValidationError`.

Interpolation is linear in rank, not in a physical dimension. This is a modelling assumption of the fuel provider.

[Back to contents](#contents)

## Vessel classes and families

RWS vessel classes are open text, not a closed enumeration, because the supported set is defined by your `fuel_observations.csv` and `vessel_references.csv`. They are normalised before comparison: surrounding whitespace is removed, `M` followed by digits is upper-cased, and the alias `BII-6B` becomes `BII-6b`.

`vessel_family` derives a coarse family from the first letter, used only to prefer a same-family proxy in step 4.

| Family | Derived from | Meaning |
| --- | --- | --- |
| `motor_vessel` | Class starting with `M` | Self-propelled motor vessel. |
| `push_convoy` | Class starting with `B` | Pushed barge convoy. |
| `coupled_convoy` | Class starting with `C` | Coupled convoy. |
| `other` | Anything else | No family preference applied. |

Proxy distance is the weighted sum of the terms below, from `FuelProxyWeights`. The default weights are version `v1` and are an initial, versioned assumption rather than a calibrated result. The version is written to every proxied result row.

| Term | Default weight | Normalisation |
| --- | --- | --- |
| `family_mismatch` | 2.0 | 1 when families differ, otherwise 0. |
| `cemt_rank` | 1.0 | Rank difference divided by the rank range. |
| `log_capacity` | 1.0 | Absolute log ratio of capacity. |
| `beam` | 0.5 | Relative difference to the target. |
| `length` | 0.5 | Relative difference to the target. |
| `draught_loaded` | 0.5 | Relative difference to the target. |
| `draught_empty` | 0.25 | Relative difference to the target. |

Ties are broken by the sorted class label, so proxy selection does not depend on input row order.

[Back to contents](#contents)



