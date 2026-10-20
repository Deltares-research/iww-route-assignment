# Cost module

## Contents

1. [Scope](#scope)
2. [Formula](#formula)
3. [Fuel resolution](#fuel-resolution)
4. [Reported and choice values](#reported-and-choice-values)
5. [Route assignment](#route-assignment)
6. [Known assumptions](#known-assumptions)

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

The interest rate $r$ uses percentage points. Enter 3 for 3 per cent. It is a scenario assumption, and it enters the calculation in exactly two places: the per-arc `interest_cost_eur` component above, and the hourly rate $c_h$ that monetises route-choice-only time. It never enters the trip fixed cost, the distance cost or any fuel term. See [where each scenario parameter is used](configuration.md#where-each-scenario-parameter-is-used).

Arc cost:

$$
C_a = c_{maint,km}L_a + c_h t_{reported}
    + p_{fuel}q_h\left(t_{move} + f_{idle}t_{fixed}\right)
$$

Loaded-trip fixed cost:

$$
C_{fixed} = (c_{load,t} + c_{unload,t})Q + c_{labour,wait}t_{cargo,wait}
$$

The fixed cost is added once after route arcs are summed.

[Back to contents](#contents)

## Fuel resolution

Light, medium and heavy represent ship loading. Default loading anchors are 0, 0.5 and 1. The provider interpolates between adjacent loading observations.

General and container source rows remain separate. Container appearance selects the container profile. Other appearances select general unless explicitly overridden.

The audited policy uses:

1. exact vessel, waterway and profile;
2. bounded waterway interpolation;
3. nearest observed waterway boundary;
4. nearest same-family vessel;
5. nearest cross-family vessel if needed.

The `strict` policy allows steps 1 and 2 only and raises `FuelCoverageError` otherwise. Every option, the method and confidence label it produces, and the proxy weights are tabulated in [Enumerations](enumerations.md#fuel-resolution-policy). Select the policy with `run.fuel_policy` in a [case configuration](configuration.md#run-section).

Nearest-boundary selection does not extrapolate a trend. Every result contains selected keys, source rows, fractions, proxy-distance terms, method and confidence.

[Back to contents](#contents)

## Reported and choice values

Reported time and cost include physical delays. Choice time adds calibration and relaxable-constraint time. Choice cost monetises that time with the hourly operating rate. Artificial choice time does not add fuel and does not enter reported totals.

[Back to contents](#contents)

## Route assignment

`iww_route_assignment.routing_costs` connects the calculators to route assignment. `RoutingCostConfig` holds the run-level inputs (cost rates, fuel provider, scenario and `RouteCostMetric`). Its `voyage_model` method builds a `VoyageCostModel` for one voyage group. The model resolves the cost-rate row from the cargo appearance and vessel class, derives the loading ratio, and caches the fuel rate per waterway class.

`RouteCostMetric` selects what the route search minimises:

- `TRAVEL_TIME`: choice time in hours (`calculate_arc_choice_time_h`). It needs no fuel or rate lookup. If the cost inputs of a group cannot be resolved, the group is still routed and reported without costs.
- `TRAVEL_COST_EUR`: choice cost in EUR. A group whose cost inputs cannot be resolved is not routed and is reported as `cost_fail`.

Both metrics report the same output: reported and choice cost, fuel and time, with the fixed trip cost added once. Empty trips are routed and costed like loaded trips. They have loading ratio zero, so the light-loading fuel rate applies, and they carry no loading or unloading cost. The empty-trip appearance is an assumption and is labelled as such in the outputs.

[Back to contents](#contents)

## Known assumptions

- Loading anchors require confirmation against source documentation.
- Proxy-distance weights are initial, versioned assumptions.
- A nearest waterway boundary is low confidence.
- The STREAM compatibility path is optional and must label placeholders.
- Fuel-table source units and redistribution rights require owner confirmation.
- Route assignment uses a placeholder speed and treats the soft-constraint penalty as hours (`PENALTY_UNIT_TO_HOURS`) until the speed calculation and penalty units are defined.

[Back to contents](#contents)

