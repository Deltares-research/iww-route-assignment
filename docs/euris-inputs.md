# EuRIS inputs

## Contents

1. [In short](#in-short)
2. [Files used](#files-used)
3. [Network](#network)
4. [Limits per arc](#limits-per-arc)
5. [Checks on load](#checks-on-load)
6. [Files not used](#files-not-used)

## In short

The assignment network and its physical limits come from EuRIS data prepared by the fis-crawler project. The library reads its output files, not its code. `io.load_euris_inputs(graph_path, structures_dir)` returns the network, the limits per arc and a report of what was read and skipped.

[Back to contents](#contents)

## Files used

| File | Folder | Used for |
| --- | --- | --- |
| `graph.pickle` | `euris-enriched` | Routing network: nodes, edges, lengths, speeds, CEMT class, fairway limits |
| `edges.geoparquet` | `dropins-euris-detailed` | Links each section (`section_id`) to its locks (`lock_id`) and bridges (`bridge_id`) |
| `chambers.geoparquet` | `dropins-euris-detailed` | Lock chamber width, length and draught |
| `openings.geoparquet` | `dropins-euris-detailed` | Bridge opening width, height and whether it opens |
| `locks.geoparquet`, `bridges.geoparquet` | `dropins-euris-detailed` | Names only, optional |

Only load a pickle file from a trusted source.

[Back to contents](#contents)

## Network

| Network field | EuRIS attribute | Unit and rule |
| --- | --- | --- |
| `arc_id` | node ids | `<from node>\|<to node>`, unique per edge |
| `section_code` | `code` | links an edge to structures. Several edges may share a code |
| `distance_km` | `length_m` | metres divided by 1000. Required |
| `speed_km_per_h` | `maxspeed_up`, `maxspeed_down`, `maxspeed`, `calspeed_up`, `calspeed_down` | first positive value, at least 1 km/h, else 10 km/h |
| `waterway_cemt_class` | `cemt` | class `0` or an unknown label means no class |
| `water_name` | `water_name`, else `ww_name` | used for the sea preference |
| `lock_service_time_h` | linked locks | `EurisLimitRules.lock_passage_time_h` per lock, default 0.5 h |
| node coordinates | node `geometry` | EPSG:4326 points. Required |
| edge line | edge `geometry` | used for snapping. A straight line when missing |

[Back to contents](#contents)

## Limits per arc

Each arc gets up to one limit record per structure. A zero or missing value is not a limit.

| Structure | Limit | Source | Unit | Rule |
| --- | --- | --- | --- | --- |
| Fairway | beam | `dim_structural_width`, else `mwidthcm` | m, cm | as given |
| Fairway | draught | `mdraughtcm` | cm | as given |
| Fairway | length | `mlengthcm` | cm | only with `EurisLimitRules(fairway_length=True)` |
| Lock | beam | chamber `dim_gate_width` | m | widest chamber |
| Lock | length | chamber `dim_usable_length` | m | longest chamber |
| Lock | draught | chamber `mdraughtcm` | cm | deepest chamber |
| Bridge | beam | opening `dim_gate_width` | m | widest opening |
| Bridge | air draught | opening `dim_height`, else `cl_height`, else `mheightcmc` | m, cm, cm | highest opening. None if any opening is operable |

A lock linked to a section but without chamber data still adds passage time. It has no limits.

[Back to contents](#contents)

## Checks on load

- The graph must be an undirected networkx `Graph`. A `MultiGraph` or directed graph is rejected.
- Every edge needs a valid `length_m`, and every node a geometry.
- Structure files must have the columns listed in `STRUCTURE_FILES`. A missing file or column stops the load.
- The report counts edges, nodes, edges without a code, edges with locks, limits by structure type and skipped values.
- The number of connected parts is written to the run summary. Trips between parts get `no_path`.

[Back to contents](#contents)

## Files not used

- `dropins-euris-detailed/graph.pickle`: a detailed `MultiGraph` with nodes at every structure.
- `terminals.geoparquet` and every `*.geojson` copy.
- `DTV_shiptypes_database.json`: vessel dimensions reach the library through the shared vessel tables instead.
- `unlo-geocoded-v0.1.gpkg`: IVS exports already hold port points.

[Back to contents](#contents)
