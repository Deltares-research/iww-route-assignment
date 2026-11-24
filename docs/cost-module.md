# Cost module

## Contents

1. [Scope](#scope)
2. [Formula](#formula)
3. [Scenario parameters](#scenario-parameters)
4. [Fuel resolution](#fuel-resolution)
5. [Reported and choice values](#reported-and-choice-values)
6. [Route assignment](#route-assignment)
7. [Known assumptions](#known-assumptions)

## Scope

The module calculates time and monetary components for supplied arc and trip inputs. It does not choose speeds, test navigability, find routes or simulate locks.

[Back to contents](#contents)

## Formula

Movement time is distance divided by positive realised speed. Reported fixed time is lock service, lock waiting, bridge service and real delay.

Hourly rate:

$$
c_h = c_{labour} + c_{maint,h} + c_{insurance} + r\,c_{interest,\%,h}
    + c_{depreciation} + c_{port} + c_{other}
$$

The interest rate $r$ uses percentage points. Enter 3 for 3 per cent. It is a scenario assumption, and it enters the calculation in exactly two places: the per-arc `interest_cost_eur` component above, and the hourly rate $c_h$ that monetises route-choice-only time. It never enters the trip fixed cost, the distance cost or any fuel term. See [scenario parameters](#scenario-parameters).

Arc cost:

$$
C_a = c_{maint,km}L_a + c_h t_{reported}
    + p_{fuel}q_{km}\left(L_a + f_{idle}v_a t_{fixed}\right)
$$

Fuel is given per km, as in the energy source. $q_{km}$ is the fuel rate in litres per km (`fuel_rate_l_per_km`), $L_a$ the distance in km and $v_a$ the realised speed in km/h.

- Sailing fuel is $q_{km}L_a$. It does not depend on the speed.
- Idle fuel is burnt during reported fixed time, such as a lock passage. The hourly sailing use is $q_{km}v_a$. The idle share of it is `idle_fuel_factor`, so idle fuel is $q_{km}v_a f_{idle}t_{fixed}$.

Worked example: 6 l/km over 60 km at 12 km/h, with 1 h of lock time, idle factor 0.2 and fuel at 1.50 EUR per litre. Sailing fuel is 6 x 60 = 360 l. Idle fuel is 6 x 12 x 0.2 x 1 = 14.4 l. Fuel cost is 374.4 x 1.50 = 561.60 EUR.

Loaded-trip fixed cost:

$$
C_{fixed} = (c_{load,t} + c_{unload,t})Q + c_{labour,wait}t_{cargo,wait}
$$

The fixed cost is added once after route arcs are summed.

[Back to contents](#contents)

## Scenario parameters

Four values are assumptions, not data. They are not read from any table. They form the frozen `ScenarioCostParameters`, passed as `AssignmentConfig.scenario` or to `load_cost_inputs`. All must be finite and non-negative.

| Parameter | Unit | Used in | Result columns affected |
| --- | --- | --- | --- |
| `fuel_price_eur_per_l` | EUR per litre | `calculate_arc_costs` | `sailing_fuel_cost_eur`, `idle_fuel_cost_eur`, and so `reported_variable_cost_eur` and `choice_cost_eur` |
| `interest_rate_percent` | percentage points. Enter 3.0 for 3 per cent | `calculate_arc_costs` | `interest_cost_eur`, and through the hourly rate also `choice_only_cost_eur` |
| `cargo_waiting_time_h` | hours | `calculate_trip_fixed_cost` | `cargo_waiting_cost_eur` and `total_fixed_cost_eur`, loaded trips only |
| `idle_fuel_factor` | share of the hourly sailing fuel use, at most 1. Default 0.15 | `calculate_arc_costs` | `idle_fuel_l` and `idle_fuel_cost_eur` |

The interest rate is used in two places:

1. As a cost part per arc, $C_{interest} = r\,c_{interest,\%,h}\,t_{reported}$, with $c_{interest,\%,h}$ the `interest_pct_h` column of the cost rates. It lands in `interest_cost_eur`, which is part of `time_cost_eur`.
2. As one term of the hourly rate $c_h$ above, which prices route-choice-only time. Raising the rate also raises `choice_only_cost_eur`.

Both uses multiply reported time, so the interest rate has no effect on an arc with zero reported time. Compare two scenarios by changing these four values only, with the same reference files. That keeps the difference attributable.

[Back to contents](#contents)

## Fuel resolution

Light, medium and heavy represent ship loading. Default loading anchors are 0, 0.5 and 1. The provider interpolates between adjacent loading observations.

The fuel key is the vessel class and the waterway class. There is no goods column. When a source has a general and a container table, one is selected before costing and the rows are never merged. See [Data lineage](data-lineage.md#fuel) for the source and the conversion from megajoules per km.

The audited policy uses:

1. Exact vessel and waterway.
2. Bounded waterway interpolation.
3. Nearest observed waterway boundary.
4. Nearest same-family vessel.
5. Nearest cross-family vessel if needed.

The `strict` policy allows steps 1 and 2 only and raises `FuelCoverageError` otherwise. Every option, the method and confidence label it produces, and the proxy weights are tabulated in [Enumerations](enumerations.md#fuel-resolution-policy). Select the policy with `AssignmentConfig.fuel_policy`, or the `policy` argument of `load_fuel_provider`.

Nearest-boundary selection does not extrapolate a trend. Every result contains selected keys, source rows, fractions, proxy-distance terms, method and confidence.

[Back to contents](#contents)

## Reported and choice values

Reported time and cost include physical delays. Choice time adds calibration and relaxable-constraint time. Choice cost monetises that time with the hourly operating rate. Artificial choice time does not add fuel and does not enter reported totals.

[Back to contents](#contents)

## Route assignment

`iww_route_assignment.assignment` uses this module for both route search and reported cost. `calculate_arc_cost_arrays` is the array form of the arc formula. It gives every edge weight for a routing key in one call, and prices each chosen route. `calculate_arc_costs` uses the same function, so search and reported values cannot drift apart. `combine_trip_cost_arrays` adds the fixed cost once, like `combine_trip_cost`.

`RouteCostMetric` selects what the route search minimises:

- `travel_cost_eur` (default): `choice_cost_eur`, with penalty hours priced at the hourly rate (`hourly_rate_eur_per_h`).
- `travel_time`: choice time in hours, which needs no rate or fuel.

Both metrics report the full cost of the chosen route. A trip whose cost inputs cannot be resolved gets status `cost_fail` under either metric. Empty trips are routed and costed like loaded trips. They have loading ratio zero, so the light-loading fuel rate applies, and they carry no fixed cargo-handling cost. See [Traffic assignment](assignment.md).

[Back to contents](#contents)

## Known assumptions

- Loading anchors require confirmation against source documentation.
- Proxy-distance weights are initial, versioned assumptions.
- A nearest waterway boundary is low confidence.
- The STREAM compatibility path is optional and must label placeholders.
- The fuel source gives energy in megajoules per km. That unit is inferred from the published STREAM figures and needs owner confirmation, as do the redistribution rights.
- Litres follow from 36 MJ per litre of diesel (`DIESEL_ENERGY_MJ_PER_L`).
- Route assignment uses EuRIS speed limits, not vessel-specific speeds. Penalty sizes come from the previous assignment and are not calibrated.

[Back to contents](#contents)

