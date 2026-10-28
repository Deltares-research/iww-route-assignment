# Traffic assignment

## Contents

1. [In short](#in-short)
2. [Inputs](#inputs)
3. [How a run works](#how-a-run-works)
4. [How the cost function chooses the route](#how-the-cost-function-chooses-the-route)
5. [Penalties](#penalties)
6. [Settings](#settings)
7. [Outputs](#outputs)
8. [Running it](#running-it)
9. [What not to conclude](#what-not-to-conclude)
10. [Assumptions and open points](#assumptions-and-open-points)

## In short

- Each IVS trip becomes a trip between two points on the network.
- The route search minimises the cost function's route-choice cost by default (`travel_cost_eur`). Time only (`travel_time`) is the alternative.
- Physical limits and route preferences add penalty hours to the route-choice weight. They never enter reported time or cost.
- Each chosen route is then priced again with the trip's exact load. The fixed cargo-handling cost is added once per trip.
- Every trip gets a status. Failures carry a reason and are never dropped.

[Back to contents](#contents)

## Inputs

| Input | Contract | Built by |
| --- | --- | --- |
| Trips with points | `AssignmentTrips` | `io.read_assignment_trips` from an IVS export |
| Network | `AssignmentNetwork` of `NetworkEdge` | `io.load_euris_inputs`, see [EuRIS inputs](euris-inputs.md) |
| Physical limits per arc | `ArcRestriction` | `io.load_euris_inputs` |
| Cost rates, fuel, scenario | `CostInputs` | `io.load_cost_rates_csv`, `io.load_fuel_provider` |
| Vessel dimensions | `VesselCatalogue` | `io.load_vessel_references_csv`, `io.load_navigability_vessels_csv` |

Trips are read with the same IVS reader as the cost case, so the cargo unit must be declared and repeated trip ids must be handled explicitly. The assignment adds one segment rule: `Overig` maps to the appearance `overig`.

A vessel class must be in the vessel references. Labels such as `B04` and `BO4` are different classes unless `VesselCatalogue(class_aliases=...)` states otherwise. `io.IVS_SK_CODE_CROSSWALK` is the confirmed IVS mapping: `B01`, `B02` and `B04` are `BO1`, `BO2` and `BO4`. `B03` is not mapped, because `BO3` has no vessel reference.

[Back to contents](#contents)

## How a run works

```mermaid
flowchart LR
    T[Trips] --> S[Snap points to network]
    S --> C[Check vessel and cost inputs]
    C --> K[Group by routing key]
    K --> W[Edge weights from cost function plus penalties]
    W --> D[Shortest path per origin]
    D --> P[Reprice route with exact load]
    P --> A[Sum per edge]
```

1. **Snap.** Each point goes to the nearest edge, then to the nearer node of that edge. Distances are measured in EPSG:3035.
2. **Check.** The status becomes `snap_fail`, `vessel_fail` or `cost_fail` if something is missing. Checks run in that order and the first failure wins.
3. **Group.** Trips, each one ship, with the same routing key share edge weights. For `travel_time` the key is the vessel class and whether it is loaded. For `travel_cost_eur` it also holds the appearance and the rounded loading ratio. One search from each origin serves the whole group. Each trip is still priced alone with its own exact load.
4. **Search.** One Dijkstra search per routing key and origin node serves every trip from that node. The network is undirected.
5. **Price.** The chosen route is priced with the cost function at the exact loading ratio. The fixed cost is added once.
6. **Sum.** Trips, cargo, time, cost, fuel and penalties are summed per edge.

[Back to contents](#contents)

## How the cost function chooses the route

The search weight of an edge comes from `calculate_arc_cost_arrays`, the same formula that prices the route afterwards.

| Metric | Edge weight |
| --- | --- |
| `travel_cost_eur` | `choice_cost_eur`: distance cost, hourly costs and fuel over reported time, plus penalty hours at the vessel's hourly rate |
| `travel_time` | movement time plus lock time plus penalty hours |

Fuel per edge uses the edge's CEMT class. An edge without a class uses the vessel's own class.

Lock time is the number of locks on the section times 0.5 h (`EurisLimitRules.lock_passage_time_h`). It is reported time, priced at the hourly rate, and burns idle fuel. It is the same for every vessel and direction and has no waiting.

Terms used here:

- **Vessel class**: the RWS class from `SK_CODE`. It selects cost rates, fuel and dimensions.
- **Appearance**: the cargo type that selects the cost-rate row (`droge_bulk`, `natte_bulk`, `container`, `overig`), from the IVS `Segment` column.
- **Loading ratio**: cargo divided by capacity. It sets fuel and, with the cargo, the fixed cost.
- **Rounded load**: the loading ratio rounded by `routing_ratio_step`, for the search only.

`routing_ratio_step` rounds the loading ratio used for the search, for example to multiples of 0.05. Trips can then share a search. Reported cost always uses the exact ratio. A loaded trip never rounds to 0.

[Back to contents](#contents)

## Penalties

Two kinds of penalty add hours to the route-choice weight. Each is set in a settings object.

| Kind | Settings | Applies when | Default |
| --- | --- | --- | --- |
| Beam | `NavigabilityPenalties.beam_h` | the vessel is wider than the narrowest lock, bridge or fairway on an arc with a Dutch structure | 1000 h |
| Beam abroad | `NavigabilityPenalties.beam_abroad_h_per_m` | as above, at a structure outside `flat_beam_countries` | 5 h per metre over |
| Length | `NavigabilityPenalties.length_h` | the vessel is longer than the shortest lock on the arc | 1000 h |
| Draught | `NavigabilityPenalties.draught_h` | the vessel draught exceeds a lock or fairway draught | 0 h, reported only |
| Air draught | `NavigabilityPenalties.air_draught_h` | the vessel is higher than a fixed bridge | 0 h, reported only |
| Undetermined | `NavigabilityPenalties.undetermined_h` | a limit exists but the vessel value is unknown | 0 h, reported only |
| CEMT | `RoutePreferences.cemt_h` | the vessel CEMT class is above the edge class | 1000 h |
| Sea | `RoutePreferences.sea_h` | the water name is in `sea_water_names` | 100000 h |

Rules:

- One penalty per limit type per arc, set by the tightest limit.
- A penalty of 0 stops a check from steering the route. The unmet limit is still listed in the outputs.
- Draught and air draught are reported only by default. The check uses the full loaded draught for any loaded trip and fixed bridge clearances without water levels, so it overstates both.
- A penalty steers the search. It does not forbid an arc. If every route meets a limit, the cheapest one is still chosen and the penalty is visible.

[Back to contents](#contents)

## Settings

| Setting | Values | What it changes |
| --- | --- | --- |
| `metric` | `travel_cost_eur`, `travel_time` | What the search minimises |
| `routing_ratio_step` | `None` or a value in (0, 1] | Rounding of the search load |
| `points_crs` | a CRS code | CRS of the trip points, for example `EPSG:28992` |
| `origin_column`, `destination_column` | column names | Which points to route between, for example the first and last counting point or the ports |
| `max_snap_distance_m` | `None` or metres | Points further than this from the network give `snap_fail` |
| `on_missing_cost` | `reject`, `fail` | Missing cost rates give `cost_fail`, or stop the run |
| `n_workers` | 1 or more | Worker processes. Results do not depend on it |
| `origins_per_search` | 1 or more | Origins per Dijkstra call. Limits memory |

`EurisLimitRules` sets the lock passage time and whether the fairway length limit is used. See [EuRIS inputs](euris-inputs.md).

[Back to contents](#contents)

## Outputs

`assign_traffic` returns an `AssignmentResult`. `io.write_assignment_outputs` writes it to a folder.

Output names keep the older word voyage: `voyages.parquet` has one row per trip, `voyage_edges.parquet` one row per sailed edge of a trip, and `voyage_index` numbers the trips. In Python they are `result.trips` and `result.trip_edges`.

| File | One row per | Main columns |
| --- | --- | --- |
| `voyages.parquet` | trip | `trip_id`, `status`, `detail`, `vessel_rws_class`, `appearance`, `loading_ratio`, `routing_loading_ratio`, `n_edges`, `distance_km`, `reported_time_h`, `choice_time_h`, `fuel_l`, `fixed_cost_eur`, `reported_total_cost_eur`, `choice_total_cost_eur`, `total_cost_eur`, `penalty_total_h`, `penalised_section_count`, `fuel_lowest_confidence` |
| `trip_rejections.csv` | source row that is not a valid trip | `source_row_id`, `reason`, `detail` |
| `voyage_edges.parquet` | sailed edge | `trip_id`, `seq`, `arc_id`, `from_node`, `to_node`, `penalty_<type>_h`, `reported_time_h`, `reported_cost_eur`, `choice_cost_eur`, `fuel_l`, `fuel_rate_l_per_h`, `fuel_method`, `fuel_confidence` |
| `intensity.geoparquet` | used edge | trips, empty trips, cargo, time, cost, fuel and penalty sums |
| `assignment.gpkg` | layers `intensity` and `penalised_edges` | penalised edges list the structure, limit, penalty, trips and vessel classes |
| `summary.json` | run | status counts, missing classes and rates, network size and parts, settings |

`total_cost_eur` equals `choice_total_cost_eur` and includes penalties. Use `reported_total_cost_eur` for the cost a vessel actually incurs.

**Time period.** The assignment does not use time. Every trip in the input file is routed once, whatever its date, and the outputs add up all of them. A run therefore covers exactly the period of its input file. To assign one period, filter the input rows first, for example on `Eerste_Tijdstip`, the time at the first counting point. `Begintijd_evenement` is not a start time: in the 2026 register it equals the last counting-point time in 98.1 % of rows.

Trips with `vessel_fail`, `cost_fail` or `snap_fail` have no route. They are in `voyages.parquet` with a reason but absent from `voyage_edges.parquet` and the intensity tables, so intensity counts routed trips only.

[Back to contents](#contents)

## Running it

From the IDE, with no data needed:

```powershell
poetry run python examples/assign_synthetic_trips.py
```

In code:

```python
from iww_route_assignment import AssignmentSettings, CostInputs, VesselCatalogue, assign_traffic
from iww_route_assignment.io import (
    load_cost_rates_csv,
    load_euris_inputs,
    load_fuel_provider,
    load_vessel_references_csv,
    read_assignment_trips,
    write_assignment_outputs,
)

settings = AssignmentSettings(metric="travel_cost_eur", routing_ratio_step=0.05)
trips = read_assignment_trips(
    frame,
    source_dataset="ivs_2026",
    format="extended_2026",
    cargo_weight_unit="t",
    drop_export_index=True,
    duplicate_trip_ids="exclude",
    settings=settings,
)
euris = load_euris_inputs(graph_path, structures_dir)
catalogue = VesselCatalogue(load_vessel_references_csv(vessel_path))
costs = CostInputs(
    load_cost_rates_csv(rates_path), load_fuel_provider(fuel_path, vessel_path), scenario
)
result = assign_traffic(
    trips, euris.network, costs, catalogue, euris.restrictions, settings=settings
)
write_assignment_outputs(result, output_dir)
```

The real-data run script lives in the external IWW scripts folder. Guard it with `if __name__ == "__main__":` when `n_workers > 1` on Windows.

[Back to contents](#contents)

## What not to conclude

- A penalised route is not proof that a vessel sailed through a too-small structure. Penalties steer route choice only.
- Routes are between counting points by default, not between ports. Distances are then shorter than full trips.
- Reported time has no waiting, congestion or calibration. It is not an observed travel time.
- Intensity is not a yearly or daily figure. It counts every trip in the input file, so divide by the number of days the file covers.
- A rounded search load can choose a slightly different route than the exact load would. Reported cost is exact for the chosen route.

[Back to contents](#contents)

## Assumptions and open points

1. Speed per edge is the first positive EuRIS speed attribute, at least 1 km/h, else 10 km/h. It does not depend on the vessel.
2. Lock passage time is 0.5 h per lock on a section. No lock waiting is modelled.
3. The network is undirected. Direction-dependent speeds and one-way rules are not modelled.
4. The beam rule distinguishes Dutch structures (flat penalty) from others (per metre), using the first two letters of the structure id.
5. Draught uses the full loaded draught for any loaded trip.
6. Penalty sizes come from the previous assignment and are not calibrated.

[Back to contents](#contents)
