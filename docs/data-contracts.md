# Data contracts

## Contents

1. [Policy](#policy)
2. [Fuel observations](#fuel-observations)
3. [Vessel references](#vessel-references)
4. [Cost rates](#cost-rates)
5. [Arc requests](#arc-requests)
6. [Arc results](#arc-results)
7. [Trip observations](#trip-observations)

Header-only templates for every table are committed in [`schemas/`](../schemas/README.md). Controlled values are listed in [Enumerations](enumerations.md).

## Policy

Production and reference data stays outside Git. Repository fixtures are synthetic. The MIT code licence does not grant rights to redistribute BIVAS, DTV, STREAM or notebook-derived data.

All public numeric field names include units.

[Back to contents](#contents)

## Fuel observations

Required CSV fields:

| Field | Meaning |
| --- | --- |
| `source_row_id` | stable source identifier |
| `vessel_rws_class` | canonical or supported alias |
| `waterway_cemt_class` | ordered CEMT class |
| `diesel_light_l_per_h` | light-loading fuel rate |
| `diesel_medium_l_per_h` | medium-loading fuel rate |
| `diesel_heavy_l_per_h` | heavy-loading fuel rate |
| `source_dataset` | source and version label |

The canonical key `(vessel_rws_class, waterway_cemt_class)` must be unique. Fuel has no goods/profile column. Select one source dataset explicitly when notebook sources conflict; never merge or average them silently. Generated CSV index columns are rejected.

[Back to contents](#contents)

## Vessel references

Required fields are `rws_class`, `cemt_class`, `capacity_t`, `beam_m`, `length_m`, `draught_loaded_m` and `draught_empty_m`. They support audited proxy selection only.

[Back to contents](#contents)

## Cost rates

Required external fields follow the existing BIVAS-derived table:

`appearance`, `rws_class`, `labor_h`, `labor_wait_h`, `maint_h`, `maint_km`, `insurance_h`, `interest_pct_h`, `depreciation_h`, `harbor_h`, `other_h`, `load_t`, `unload_t`, `source`.

Only `maint_km` is distance based. `interest_pct_h` is EUR per percentage point per hour.

[Back to contents](#contents)

## Arc requests

One row is one traversal of a supplied directed arc. The 0.2 runner requires the sixteen columns in `ARC_REQUEST_COLUMNS`; six optional delay fields default to zero.

Identity fields are `case_row_id`, `source_row_id`, `route_id`, `sequence`, `route_arc_count`, `arc_id`, `from_node_id` and `to_node_id`. Sequence numbers are unique, contiguous and zero-based within each trip. Repeated edges have different sequence numbers. Incomplete, disconnected or inconsistent trips fail before totals are returned.

Every column is consumed by exactly one of four consumers, which is the quickest way to predict what a change to a cell will move.

| Column | Required | Unit | Consumer | Effect |
| --- | --- | --- | --- | --- |
| `case_row_id` | Yes | text | Pass-through | Audit identifier. Copied to the result, never used in arithmetic. |
| `arc_id` | Yes | text | Pass-through | Audit identifier. Copied to the result, never used in arithmetic. |
| `appearance` | Yes | text | Monetary rate lookup | Selects the cost-rate row only; does not select fuel. |
| `vessel_rws_class` | Yes | text | Rate lookup and fuel lookup | Used by both `select_cost_rates` and the fuel provider. |
| `waterway_cemt_class` | Yes | CEMT class | Fuel lookup | Selects or interpolates the fuel row. Does not affect cost rates. |
| `loading_ratio` | Yes | 0 to 1 | Fuel lookup | Interpolates between loading anchors. Clamped, with a note, if outside the range. |
| `distance_km` | Yes | km | `ArcTravelInput` | Movement time and the only distance-based cost. |
| `realised_speed_km_per_h` | Yes | km per hour | `ArcTravelInput` | Movement time. Must be greater than zero. |
| `cargo_t` | Yes | tonnes | `TripCostInput` | Loading and unloading cost. Must be zero on an empty trip. |
| `is_loaded` | Yes | boolean | `TripCostInput` | Switches the whole fixed trip cost on or off. |
| `lock_service_time_h` | No | hours | `ArcTravelInput` | Reported fixed time, so hourly cost and idle fuel. |
| `lock_waiting_time_h` | No | hours | `ArcTravelInput` | Reported fixed time, so hourly cost and idle fuel. |
| `bridge_service_time_h` | No | hours | `ArcTravelInput` | Reported fixed time, so hourly cost and idle fuel. |
| `real_delay_time_h` | No | hours | `ArcTravelInput` | Reported fixed time, so hourly cost and idle fuel. |
| `calibration_delay_time_h` | No | hours | `ArcTravelInput` | Choice-only time. No fuel, and excluded from reported totals. |
| `relaxation_penalty_time_h` | No | hours | `ArcTravelInput` | Choice-only time. No fuel, and excluded from reported totals. |

Grouped by consumer:

- `ArcTravelInput`: `distance_km`, `realised_speed_km_per_h`, `lock_service_time_h`, `lock_waiting_time_h`, `bridge_service_time_h`, `real_delay_time_h`, `calibration_delay_time_h`, `relaxation_penalty_time_h`. Distance must be finite and non-negative, realised speed strictly positive, and the six time fields default to zero.
- `TripCostInput`: `cargo_t`, `is_loaded`.
- `select_cost_rates`: `appearance` and `vessel_rws_class`, matched case-insensitively. Exactly one rate row must match, otherwise the run fails.
- `FuelConsumptionProvider.resolve`: `vessel_rws_class`, `waterway_cemt_class` and `loading_ratio` (or one loading category).
- Pass-through identifiers: `case_row_id` and `arc_id`.

`is_loaded` accepts `true`, `false`, `1` and `0`, compared case-insensitively. Any other text is rejected.

`ArcCostRequest` also carries `vessel_class_source`, `appearance_source`, `route_source`, `loading_ratio_source`, `cargo_unit_source` and `notes`. The runner preserves these in per-arc results. Supplied fields without provenance receive an explicit `supplied` label.

[Back to contents](#contents)

## Arc results

`ArcCostBreakdown` returns each time, operating, fuel and choice component. `arc_cost_to_record` flattens it for table output, so the result column names are the dataclass field names and stay stable across releases.

| Group | Columns |
| --- | --- |
| Identity | `arc_id`, and `case_row_id` added by the runner |
| Time | `movement_time_h`, `reported_fixed_time_h`, `reported_time_h`, `choice_only_time_h`, `choice_time_h` |
| Operating cost | `distance_cost_eur`, `labour_cost_eur`, `maintenance_time_cost_eur`, `insurance_cost_eur`, `interest_cost_eur`, `depreciation_cost_eur`, `port_dues_cost_eur`, `other_cost_eur`, `time_cost_eur` |
| Fuel | `sailing_fuel_l`, `idle_fuel_l`, `sailing_fuel_cost_eur`, `idle_fuel_cost_eur` |
| Totals | `reported_variable_cost_eur`, `choice_only_cost_eur`, `choice_cost_eur` |
| Fuel audit | every `ResolvedFuelRate` field with the prefix `fuel_` |
| Trip output only | `fixed_cost_eur`, `reported_total_cost_eur`, `choice_total_cost_eur`, `total_cost_eur` |

`cost_results.csv` holds per-traversal components. `trip_cost_results.csv` adds fixed cargo-handling costs once per trip. `total_cost_eur` aliases the penalty-inclusive `choice_total_cost_eur`; `reported_total_cost_eur` excludes choice-only penalties. Zero fixed cost never suppresses a total. Empty trips still incur operating costs.

The nested fuel audit is never dropped or summarised away. `fuel_method` and `fuel_confidence` take the values in [Enumerations](enumerations.md#fuel-resolution-method); `fuel_source_row_ids`, `fuel_source_datasets`, `fuel_notes` and `fuel_proxy_distance_terms` are JSON encoded so a single CSV cell stays parsable.

The external illustrative runner documents its request CSV in `runs/test_case_cost_function`. See [Case configuration](configuration.md) for the TOML that drives it.

[Back to contents](#contents)

## Trip observations

`TripObservation` is one observed origin-destination voyage from a trip register such as IVS. `load_ivs_trips_csv` and `ivs_trips_from_frame` map the export columns in `IVS_COLUMNS` onto it, and `trips_to_arc_requests` derives the arc-request fields.

| Export column | Field | Derivation |
| --- | --- | --- |
| `unlo_herkomst`, `unlo_bestemming` | `origin_locode`, `destination_locode` | Upper-cased. Matched to supplied route ODs, never used to invent edges. |
| `sk_code` | `vessel_rws_class` | Preferred observed vessel class. |
| `v15_1_scheepstype_rws` | `vessel_type_id` | Retained numeric source code. Only an explicit verified crosswalk may resolve a missing observed class; it is not a BIVAS ID. |
| `v18_laadvermogen` | `capacity_t` | Tonnes in the source. Denominator of the loading ratio. |
| `v38_vervoerd_gewicht` | `cargo_t` | Weekly preset converts kilogrammes to tonnes by multiplying by 0.001. |
| `v30_4_containers_teu_s` | `containers_teu` | A positive count sets the appearance to `container`. |
| `nstr_nw` | `cargo_code` | Leading digit is the NSTR chapter used for the appearance mapping. |
| `v28_beladingscode` | `loading_code` | Retained for auditing. Value `1` marks an empty trip. |
| `v05_06_begindt_evenement_iso` | `observed_on` | Retained for auditing. |

Derivation rules, each recorded in a source column of the request:

- `loading_ratio` is `cargo_t` divided by `capacity_t`, clipped at 1 with a note that records the unclipped value;
- a missing transported weight on an empty loading code means zero cargo; on any other code it stays unknown and the row is rejected with `missing_cargo_weight`;
- a trip with containers takes the `container` appearance, source `observed:containers_teu`;
- a trip with no cargo code that is recorded empty takes the residual appearance, source `assumption:empty-trip-appearance-v1`;
- any other trip takes the appearance of its NSTR chapter, source label supplied by the mapping row, defaulting to `assumption:nstr-chapter-appearance-v1`;
- Distance, class, speed and delays come from supplied `ArcProfile` snapshots, not observed IVS journey fields.

Row identifiers are generated from the positional row number of the export. They label a record for auditing and are never used to match a record against a reference table.

### Extended IVS

Select `extended_2026` explicitly. Identity is `dataset:Jaar:Pseudo_ReisID`, preserving the original ID as text and rejecting duplicates. `SK_CODE`, `UNLO_Herkomst`, `UNLO_Bestemming` and `Laadvermogen` supply class, OD and capacity in tonnes. Declare `Lading` units as `t` or `kg`; the declaration is recorded as an assumption, not verification. `Aantal_Schepen` must be 1 and does not multiply costs.

`Tankvaart`, `Droge bulkvaart` and `Containervaart` map to `natte_bulk`, `droge_bulk` and `container`. Unknown segments are rejected or require a sourced mapping via `read_ivs_frame`. Missing TEU/NSTR stays unknown. Conflicting event-time columns and contradictory empty-loading codes are rejected. One raw export index may be dropped only with explicit permission. The strict adapter raises on rejected rows; `read_ivs_frame` returns audited rejections.

### Supplied routes

`arc_profiles.csv` describes directed edges. `route_sequences.csv` supplies ordered traversals and route OD labels. Optional `trip_routes.csv` associates source trips with route IDs. Without associations, only a unique supplied route for an OD is accepted; alternatives are never chosen by row order. Same-node trips need an explicit zero-distance arc. There is no route search, snapping, network repair or navigability check here.

[Back to contents](#contents)

