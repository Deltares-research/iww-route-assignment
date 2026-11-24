# Data lineage

## Contents

1. [In short](#in-short)
2. [Sources](#sources)
3. [Two ship-type tables](#two-ship-type-tables)
4. [Trips](#trips)
5. [Cost rates](#cost-rates)
6. [Fuel](#fuel)
7. [Vessels](#vessels)
8. [Network and limits](#network-and-limits)
9. [Generated files](#generated-files)
10. [Run outputs](#run-outputs)
11. [Labelled rules](#labelled-rules)

## In short

This page follows every input from its source name to the field that uses it. Read it as: source name, then the name in this library, then the function that translates it, then where it is used.

No source data is in the repository. The functions below read the files where they are kept.

[Back to contents](#contents)

## Sources

Five sources feed the model. The scenario adds four stated assumptions.

| Source | What it is | What the model takes | Why it is used |
| --- | --- | --- | --- |
| IVS extended export | The trip register of IVS, the shipping information system of Rijkswaterstaat. One row per trip, as reported by the ship. | vessel class, cargo type, loaded or empty, cargo and capacity in tonnes, first and last counting point, start time | It is the demand: which ship sails, with what load, from where to where. |
| EuRIS, prepared by fis-crawler | EuRIS is the European portal for fairway information. The fis-crawler project downloads it and builds a routable graph and tables of sections, lock chambers and bridge openings. | section length, speed limit, waterway class, section code, and the width, length, depth and height limits of fairways, locks and bridges | It is the network the ships sail on, across borders, with the physical limit of every structure. |
| BIVAS database | The database of BIVAS, the inland shipping network model of Rijkswaterstaat. | cost set 3, Kostenkengetallen 2021, from `travel_cost_values`, labelled through `ship_types` and `appearance_types`. `cemt_class` for reference | Its cost figures per vessel class and cargo type are the accepted Dutch figures, so costs agree with BIVAS studies. |
| DTV ship types | The ship-type list of the Digital Twin Waterway project, one record per RWS vessel class. fis-crawler keeps it in its reference folder. | waterway class, beam, length, loaded and empty draught, height, maximum load | It is the only source with every size the dimension check needs. fis-crawler uses the same list with EuRIS. |
| CE Delft STREAM | Energy use of inland ships per vessel class and waterway class, at three loads, from the STREAM freight transport study of CE Delft. Entered as two lists in `notebooks/create_fuel_consumption_table.ipynb`. | energy in megajoules per km at light, medium and heavy load, from the general-cargo table and, for classes it lacks, the container table | Fuel is the largest cost part. This is a published source per vessel class and waterway class. |
| Scenario | Four values chosen per run: fuel price, interest rate, cargo waiting time and idle fuel factor. | `ScenarioCostParameters` | They are assumptions, not data, so each run states and records them. See [scenario parameters](cost-module.md#scenario-parameters). |

| Source | File | Read by |
| --- | --- | --- |
| IVS | extended trip export, CSV or Parquet | `io.read_trip_frame`, `io.read_assignment_trips` |
| EuRIS | `graph.pickle`, and `edges`, `chambers` and `openings` GeoParquet | `io.load_euris_inputs` |
| BIVAS | `Bivas.db`, SQLite, opened read-only | `io.read_bivas_cost_rates`, `io.read_bivas_ship_types`, `io.read_bivas_ship_type_table`, `io.read_bivas_cemt_classes` |
| DTV | `DTV_shiptypes_database.json` | `io.read_dtv_vessel_table`, `io.compare_ship_type_sources` |
| CE Delft | the fuel notebook | `io.read_energy_use_from_notebook` |

Not used: the observed distances, times and passage lists in the IVS export, which are kept for route validation. The BIVAS network, fleet, trip and speed tables. The DTV engine power.

[Back to contents](#contents)

## Two ship-type tables

BIVAS and DTV both list vessel classes. Both describe the same RWS classes, such as `M8`, with the same width and length percentiles. They differ in what else they hold.

| | BIVAS `ship_types` | DTV ship types | The model uses |
| --- | --- | --- | --- |
| Classes | the RWS classes with cost rates | the same classes, plus a few without BIVAS cost rates, such as `BIIa-1` and `BIIL-1` | the BIVAS list. A class without cost rates cannot be priced |
| Name | `Label`, `Description` | `RWS-class`, `Description (Dutch)`, `Description (English)` | the label, matched without regard to letter case |
| Waterway class | `CEMTTypeID`, a link to `cemt_class` | `CEMT-class`, finer: `IVa`, `IVb`, `VIIa`, and a class for each push boat | DTV, with `IVa` and `IVb` read as `IV` and `VIIa` as `VIc` |
| Width | `Width_10%__m`, `Width_50%__m`, `Width_90%__m` | the same percentiles, and one class value `Beam [m]` | DTV `Beam [m]` |
| Length | `Length_10%__m`, `Length_50%__m`, `Length_90%__m` | the same percentiles, and one class value `Length [m]` | DTV `Length [m]` |
| Draught | empty only, `EmptyDepth__m` | `Draught loaded [m]`, `Draught average [m]`, `Draught empty [m]` | DTV loaded and empty |
| Height | none | `Height average [m]` | DTV, as air draught |
| Load | none | `Load weight average [ton]`, `Load weight maximum [ton]` | DTV maximum, to choose the nearest class for a fuel proxy |
| Other | `WaterplaneCoefficient`, `ResidualResistanceCoefficient`, `EmptyDepthForEmission__m` | engine and bow thruster power, `Included` | none |

So the class list comes from BIVAS and the sizes from DTV. The dimension check needs a loaded draught, a height and one beam and length per class, and only DTV holds them. BIVAS decides which classes the model knows, because each needs a cost-rate row.

`extract_parameter_tables` compares the two on every build with `compare_ship_type_sources` and writes `ship_type_comparison.csv`. Per class it shows where the class occurs, the waterway class in each source and whether they agree after folding, which shared values differ, and the DTV class beam and length beside the BIVAS medians. `build_report.json` lists the classes found in one source only and every difference. For example, BIVAS gives the push boats `BO1` to `BO4` waterway class `0`, where DTV gives `I`, `II`, `-` and `III`.

[Back to contents](#contents)

## Trips

Read by `io.read_assignment_trips`, which uses `io.read_ivs_frame` for the validation.

| IVS column | Name here | Rule | Used for |
| --- | --- | --- | --- |
| `Jaar`, `Pseudo_ReisID` | `trip_id` | `<dataset>:<Jaar>:<Pseudo_ReisID>`. A repeated id is excluded or stops the read | identity |
| `SK_CODE` | `source_vessel_class`, `vessel_rws_class` | `IVS_SK_CODE_CROSSWALK`: `B01`, `B02`, `B04` are `BO1`, `BO2`, `BO4` | cost-rate row, fuel, dimensions |
| `Segment` | `appearance` | `ASSIGNMENT_SEGMENT_MAP`: `Tankvaart` is `natte_bulk`, `Droge bulkvaart` is `droge_bulk`, `Containervaart` is `container`, `Overig` is `overig` | cost-rate row |
| `v28_Beladingscode` | `is_loaded` | code `1` is empty | loading state, fixed cost |
| `Lading` | `cargo_t` | unit declared with `cargo_weight_unit`. Kilograms are divided by 1000 | loading ratio, fixed cost |
| `Laadvermogen` | `capacity_t` | tonnes | loading ratio |
| `Aantal_Schepen` | none | must be 1 | validation only |
| `Eerste_Geom`, `Laatste_Geom` | origin and destination point | WKT in `points_crs`. Set with `origin_column` and `destination_column` | snapping |
| `Eerste_Tijdstip` | `departure_time` | text, set with `departure_column` | period filter, reporting |
| `UNLO_Herkomst`, `UNLO_Bestemming` | origin and destination label | text | reporting |

`loading_ratio` is `cargo_t` divided by `capacity_t`. Observed distances, times and passage sequences in the export are not used.

[Back to contents](#contents)

## Cost rates

Read by `io.read_bivas_cost_rates(db_path, cost_set=3)`. Cost set 3 of `travel_costs` is "Kostenkengetallen 2021" (`KOSTENKENGETALLEN_2021`).

The query joins `travel_cost_values` with `ship_types` on `ShipTypeID` and with `appearance_types` on `AppearanceTypeID`, for `TravelCosts` equal to the cost set.

| BIVAS table and column | Column in `cost_rates.csv` | Unit | Term in the cost formula |
| --- | --- | --- | --- |
| `appearance_types.Description` | `appearance` | text | selects the row. `Overig`, `Natte bulk`, `Droge bulk`, `Container` become `overig`, `natte_bulk`, `droge_bulk`, `container` |
| `ship_types.Label` | `rws_class` | text | selects the row |
| `travel_cost_values.ShipTypeID` | `ship_type_id` | id | audit |
| `LaborCostsPerHour_Euro` | `labor_h` | EUR per h | hourly rate |
| `MaintenanceCostsPerHour_Euro` | `maint_h` | EUR per h | hourly rate |
| `InsuranceCostsPerHour_Euro` | `insurance_h` | EUR per h | hourly rate |
| `InterestCostsPerPercentPerHour_Euro` | `interest_pct_h` | EUR per percentage point per h | hourly rate, times `interest_rate_percent` |
| `DepreciationCostsPerHour_Euro` | `depreciation_h` | EUR per h | hourly rate |
| `HarborFeesPerHour_Euro` | `harbor_h` | EUR per h | hourly rate |
| `OtherCostsPerHour_Euro` | `other_h` | EUR per h | hourly rate |
| `MaintenanceCostsPerKilometer_Euro` | `maint_km` | EUR per km | distance cost |
| `LoadCostsPerTon_Euro` | `load_t` | EUR per t | fixed cost, once per loaded trip |
| `UnloadCostsPerTon_Euro` | `unload_t` | EUR per t | fixed cost, once per loaded trip |
| `LaborCostsPerHourWaitingOnLoad_Euro` | `labor_wait_h` | EUR per h | fixed cost, times `cargo_waiting_time_h` |
| `travel_costs.Description` | `price_level`, `source` | year, text | audit |

The mapping is `BIVAS_COST_COLUMNS` and `BIVAS_APPEARANCES` in `io.bivas`. The formulas are in [Cost module](cost-module.md#formula).

Waterway classes come from `cemt_class` through `io.read_bivas_cemt_classes`: `Id`, `Description`, `Width__m`, `CrossSection__m2`, `Depth__m`, `MinimumAbsoluteUkc__m`, `MinimumRelativeUkc__m` and `Category` become `cemt_class_id`, `label`, `std_width_m`, `std_cross_section_m2`, `std_depth_m`, `keel_clearance_abs_m`, `keel_clearance_rel` and `waterway_type`. That table is kept for reference. The run does not load it.

[Back to contents](#contents)

## Fuel

| Step | Function | From | To |
| --- | --- | --- | --- |
| Read | `read_energy_use_from_notebook` | notebook lists `vessel_data` (general cargo) and `container_vessel_data`, fields `vessel_rws_class`, `waterway_cemt_class`, `diesel_light`, `diesel_medium`, `diesel_heavy` | `ce_delft_energy_use.csv` with `cargo_profile`, `energy_light_mj_per_km`, `energy_medium_mj_per_km`, `energy_heavy_mj_per_km` |
| Select and convert | `fuel_observations_from_energy` | `ce_delft_energy_use.csv` | `fuel_observations.csv` with `diesel_light_l_per_km`, `diesel_medium_l_per_km`, `diesel_heavy_l_per_km` |
| Resolve | `TableFuelConsumptionProvider` through `load_fuel_provider` | `fuel_observations.csv`, `vessel_references.csv` | `fuel_rate_l_per_km` with `fuel_method` and `fuel_confidence` |

Rules:

- **Unit.** The source values are megajoules per km. Litres per km are megajoules per km divided by 36 MJ per litre (`DIESEL_ENERGY_MJ_PER_L`). Example: 50 MJ per km is 1.39 l per km.
- **Source selection.** The general-cargo table is used. A vessel class that the general table lacks takes its rows from the container table. A class in both uses the general rows only. Rows are never averaged.
- **Loading.** Light, medium and heavy are loading ratios 0, 0.5 and 1. The rate is interpolated between them.
- **Gaps.** A vessel class or waterway class without a row gets a labelled proxy. `fuel_method` says which and `fuel_confidence` rates it. See [fuel resolution](enumerations.md#fuel-resolution-method).

The megajoule unit is inferred from the published STREAM figures. It needs confirmation by the table owner.

For a notebook table in another unit, `io.prepare_notebook_fuel_observations(..., value_unit=...)` takes `MJ_per_km` or `l_per_km`. The unit has no default.

[Back to contents](#contents)

## Vessels

Read by `read_dtv_vessel_table`, which joins the BIVAS `ship_types` labels to the DTV file. Labels are matched without regard to letter case. A DTV class that BIVAS lacks is left out. A class repeated in the DTV file stops the build. See [two ship-type tables](#two-ship-type-tables) for why both sources are read.

| DTV field | Column in `rws_to_cemt.csv` | Used in |
| --- | --- | --- |
| `RWS-class` | `rws_class` | key |
| `CEMT-class` | `cemt_class_raw`, `cemt_class` | vessel class for the CEMT preference and fuel proxies. `IVa` and `IVb` become `IV`, `VIIa` becomes `VIc` (`DTV_CEMT_ALIASES`) |
| `Load weight maximum [ton]` | `capacity_t` | fuel proxy distance |
| `Beam [m]` | `beam_m` | beam check |
| `Length [m]` | `length_m` | length check |
| `Draught loaded [m]` | `draught_loaded_m` | draught check, loaded state |
| `Draught empty [m]` | `draught_empty_m` | draught check, empty state |
| `Height average [m]` | `height_m` | air draught check, both states |

`build_shared_inputs` then writes two files:

- `vessel_references.csv`: classes with every dimension present and positive and a supported waterway class. Nothing is filled in. Dropped classes are listed in `build_report.json`.
- `navigability_vessels.csv`: a `<class>-loaded` and a `<class>-empty` state per class, with `draught_m` from the matching draught and `air_draught_m` from `height_m`.

A trip whose class is not in `vessel_references.csv` gets status `vessel_fail`.

[Back to contents](#contents)

## Network and limits

Read by `io.load_euris_inputs`. The full attribute tables are in [EuRIS inputs](euris-inputs.md). In short:

| EuRIS attribute | Name here | Used in |
| --- | --- | --- |
| `length_m` | `distance_km` | distance cost, sailing fuel, time |
| `maxspeed_*`, `calspeed_*` | `speed_km_per_h` | time, idle fuel |
| `cemt` | `waterway_cemt_class` | fuel row, CEMT preference |
| `code` | `section_code` | link to locks and bridges |
| `water_name`, `ww_name` | `water_name` | sea preference |
| `dim_structural_width`, `mwidthcm`, `mdraughtcm` | fairway limits | beam and draught checks |
| chamber `dim_gate_width`, `dim_usable_length`, `mdraughtcm` | lock limits | beam, length and draught checks |
| opening `dim_gate_width`, `dim_height`, `cl_height`, `mheightcmc` | bridge limits | beam and air draught checks |
| locks linked to a section | `lock_count`, `lock_service_time_h` | reported time, idle fuel |

[Back to contents](#contents)

## Generated files

| File | Made by | From |
| --- | --- | --- |
| `cost_rates_2021.csv` | `extract_parameter_tables` | BIVAS `travel_cost_values` |
| `bivas_cemt_classes.csv` | `extract_parameter_tables` | BIVAS `cemt_class` |
| `rws_to_cemt.csv` | `extract_parameter_tables` | BIVAS `ship_types`, DTV ship types |
| `ce_delft_energy_use.csv` | `extract_parameter_tables` | the two CE Delft tables |
| `ship_type_comparison.csv` | `extract_parameter_tables`, through `compare_ship_type_sources` | BIVAS `ship_types` and `cemt_class`, DTV ship types. For checking only, not loaded by a run |
| `cost_rates.csv` | `build_shared_inputs` | `cost_rates_2021.csv`, unchanged |
| `fuel_observations.csv` | `build_shared_inputs` | `ce_delft_energy_use.csv` |
| `vessel_references.csv` | `build_shared_inputs` | `rws_to_cemt.csv` |
| `navigability_vessels.csv` | `build_shared_inputs` | `rws_to_cemt.csv` |
| `build_report.json` | both | input checksums, rules, row counts, dropped classes. From the extraction also `ship_types_only_in_dtv`, `ship_types_only_in_bivas`, `cemt_class_differences` and `ship_type_value_differences` |
| `run_metadata.json` | `run_assignment` | configuration, input checksums, network report, summary |

Reports hold file names and SHA-256 checksums. They hold no folder paths.

[Back to contents](#contents)

## Run outputs

| Output | Columns | Made by |
| --- | --- | --- |
| `voyages.parquet` | identity, `departure_time`, vessel class, `appearance`, `cargo_t`, `loading_ratio` | step 1, trip reading |
| | `origin_node_id`, `origin_snap_m`, `destination_node_id`, `destination_snap_m` | step 5, snapping |
| | `status`, `detail` | steps 1 to 5 |
| | `n_edges`, `distance_km`, `lock_passages`, `reported_time_h`, `choice_time_h`, `route_weight` | step 5, route |
| | `distance_cost_eur`, `time_cost_eur`, `fuel_cost_eur`, `fixed_cost_eur`, `reported_total_cost_eur`, `fuel_l`, `fuel_lowest_confidence`, `cost_rate_source` | step 4, cost |
| | `penalty_total_h`, `penalised_section_count`, `choice_total_cost_eur`, `total_cost_eur` | step 3, dimension control |
| `voyage_edges.parquet` | `seq`, `arc_id`, `distance_km`, `speed_km_per_h`, `lock_count`, `lock_time_h` | step 2, network |
| | `penalty_<type>_h`, `penalty_total_h` | step 3 |
| | `reported_time_h`, `distance_cost_eur`, `time_cost_eur`, `fuel_cost_eur`, `reported_cost_eur`, `choice_cost_eur`, `fuel_l`, `fuel_rate_l_per_km`, `fuel_method`, `fuel_confidence` | step 4 |
| `intensity.geoparquet` | sums per used section | step 5 |
| `assignment.gpkg` | layers `intensity` and `penalised_edges` | step 5 |
| `trip_rejections.csv` | `source_row_id`, `reason`, `detail` | step 1 |
| `assignment_map.html` | the result as a map | step 6 |

Per trip, `distance_cost_eur + time_cost_eur + fuel_cost_eur + fixed_cost_eur` equals `reported_total_cost_eur`. Penalties are in the `choice` columns only.

[Back to contents](#contents)

## Labelled rules

Each rule that fills, selects or converts a value has a label that travels with the data.

| Label | Meaning | Where |
| --- | --- | --- |
| `conversion:MJ-per-km-divided-by-36-MJ-per-litre` | energy to litres | `fuel_observations.csv`, `build_report.json` |
| `assumption:container-source-for-classes-absent-from-general-v1` | container table used only for classes the general table lacks | `build_report.json` |
| `assumption:height_m-is-air-draught-for-both-loading-states-v1` | one height for loaded and empty | `navigability_vessels.csv` |
| `assumption:ivs-segment-overig-is-overig-v1` | segment `Overig` priced with the `overig` rates | `appearance_source` |
| `mapping:ivs-segment-v1` | the three named segments | `appearance_source` |
| `assumption:declared-Lading-unit=<unit>;capacity=t` | cargo unit as declared | trip record |

[Back to contents](#contents)
