# Cost module

## Contents

1. [Scope](#scope)
2. [Formula](#formula)
3. [Fuel resolution](#fuel-resolution)
4. [Reported and choice values](#reported-and-choice-values)
5. [Known assumptions](#known-assumptions)

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

The interest rate $r$ uses percentage points. Enter 3 for 3 per cent.

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

Nearest-boundary selection does not extrapolate a trend. Every result contains selected keys, source rows, fractions, proxy-distance terms, method and confidence.

[Back to contents](#contents)

## Reported and choice values

Reported time and cost include physical delays. Choice time adds calibration and relaxable-constraint time. Choice cost monetises that time with the hourly operating rate. Artificial choice time does not add fuel and does not enter reported totals.

[Back to contents](#contents)

## Known assumptions

- Loading anchors require confirmation against source documentation.
- Proxy-distance weights are initial, versioned assumptions.
- A nearest waterway boundary is low confidence.
- The STREAM compatibility path is optional and must label placeholders.
- Fuel-table source units and redistribution rights require owner confirmation.

[Back to contents](#contents)

