# Data contracts

## Contents

1. [Policy](#policy)
2. [Fuel observations](#fuel-observations)
3. [Vessel references](#vessel-references)
4. [Cost rates](#cost-rates)
5. [Arc requests and results](#arc-requests-and-results)

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
| `vessel_profile` | `general` or `container` |
| `diesel_light_l_per_h` | light-loading fuel rate |
| `diesel_medium_l_per_h` | medium-loading fuel rate |
| `diesel_heavy_l_per_h` | heavy-loading fuel rate |
| `source_dataset` | source and version label |

The key `(vessel_rws_class, waterway_cemt_class, vessel_profile)` must be unique. Generated CSV index columns are rejected.

[Back to contents](#contents)

## Vessel references

Required fields are `rws_class`, `cemt_class`, `capacity_t`, `beam_m`, `length_m`, `draught_loaded_m` and `draught_empty_m`. They support audited proxy selection only.

[Back to contents](#contents)

## Cost rates

Required external fields follow the existing BIVAS-derived table:

`appearance`, `rws_class`, `labor_h`, `labor_wait_h`, `maint_h`, `maint_km`, `insurance_h`, `interest_pct_h`, `depreciation_h`, `harbor_h`, `other_h`, `load_t`, `unload_t`, `source`.

Only `maint_km` is distance based. `interest_pct_h` is EUR per percentage point per hour.

[Back to contents](#contents)

## Arc requests and results

`ArcTravelInput` requires distance and positive realised speed. Lock service, lock waiting, bridge service, real delay, calibration delay and relaxation penalty default to zero.

`ArcCostBreakdown` returns each time, operating, fuel and choice component. `arc_cost_to_record` flattens the nested fuel audit for table output.

The external illustrative runner documents its request CSV in `runs/test_case_cost_function`.

[Back to contents](#contents)

