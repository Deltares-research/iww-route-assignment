# IWW route assignment

An open-source Python library for inland-waterway routing and assignment. The modelling approach draws mainly on BIVAS, with selected data-pipeline ideas from fis-crawler and optional future lock-delay coupling to OpenTNSim.

## Contents

1. [Status](#status)
2. [Quick start](#quick-start)
3. [Current capability](#current-capability)
4. [Documentation](#documentation)
5. [Data and licence](#data-and-licence)

## Status

Pre-alpha. The first work package implements an auditable monetary cost core and fuel-resolution policy. Routing, navigability, iterative lock assignment and unloading remain planned work.

[Back to contents](#contents)

## Quick start

Requires Python 3.12 or later.

```powershell
poetry install --extras dev
poetry run python examples/calculate_arc_cost.py
poetry run pytest
```

[Back to contents](#contents)

## Current capability

- BIVAS-style distance, hourly, interest, fuel and trip-fixed costs.
- Separate reported and route-choice values.
- Profile-aware light, medium and heavy loading fuel rates.
- Exact lookup, bounded interpolation and deterministic audited proxies.
- Scalar and NumPy batch calculation.
- Validated CSV adapters and flat audit records.
- Synthetic illustrative case and benchmark harness.

OpenTNSim is not a core dependency.

[Back to contents](#contents)

## Documentation

Start with the [documentation map](docs/index.md). See the [cost formula](docs/cost-module.md), [data contracts](docs/data-contracts.md), [examples](docs/examples.md) and [development guide](docs/development.md).

[Back to contents](#contents)

## Data and licence

Code is licensed under the [MIT licence](LICENSE). Production and reference data should remain outside Git. The code licence does not establish redistribution rights for BIVAS, DTV, STREAM or notebook-derived parameter tables.

Repository test fixtures are synthetic.

[Back to contents](#contents)
