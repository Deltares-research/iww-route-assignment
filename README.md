# IWW route assignment

An open-source Python library for inland-waterway routing and assignment. The modelling approach draws mainly on BIVAS, with selected data-pipeline ideas from fis-crawler and optional future lock-delay coupling to OpenTNSim.

## Contents

1. [Status](#status)
2. [Quick start](#quick-start)
3. [Current capability](#current-capability)
4. [Documentation](#documentation)
5. [Data and licence](#data-and-licence)

## Status

Pre-alpha. The first work package implements an auditable monetary cost core and fuel-resolution policy. The second adds an independent navigability evaluator. The third adds traffic assignment: IVS trips are routed over the EuRIS network with the cost function as the route-search weight. Iterative lock assignment and unloading remain planned work.

[Back to contents](#contents)

## Quick start

Requires Python 3.12 or later.

```powershell
poetry install --extras dev
poetry run python examples/calculate_arc_cost.py
poetry run python examples/assign_synthetic_trips.py
poetry run pytest
```

[Back to contents](#contents)

## Current capability

- BIVAS-style distance, hourly, interest, fuel and trip-fixed costs.
- Separate reported and route-choice values.
- Light, medium and heavy loading fuel rates in litres per km.
- Exact lookup, bounded interpolation and deterministic audited proxies.
- Scalar and NumPy batch calculation.
- Validated CSV adapters and flat audit records.
- Traffic assignment on the EuRIS network, minimising cost or time, with reported penalties for physical limits.
- One call for the whole chain (`run_assignment`), check tables per step and an interactive map.
- Reference tables built from the BIVAS database, the DTV ship types and the CE Delft energy figures, with checksums.
- Synthetic illustrative case and benchmark harness.

OpenTNSim is not a core dependency.

[Back to contents](#contents)

## Documentation

Start with the [documentation map](docs/index.md), which indexes every document in the repository and offers a find-by-task table.

Most used pages:

| Page | Answers |
| --- | --- |
| [Workflow](docs/workflow.md) | How the modules connect and how to run the whole chain. |
| [Data lineage](docs/data-lineage.md) | Where each input comes from, by its source name. |
| [Cost module](docs/cost-module.md) | Which formulas are applied. |
| [Traffic assignment](docs/assignment.md) | How trips are routed and priced. |
| [Data contracts](docs/data-contracts.md) | Which CSV columns are required and what each one drives. |
| [Enumerations](docs/enumerations.md) | Which option values exist and what each one means. |
| [Examples](docs/examples.md) | How to run the example, a case and the benchmark. |
| [Development](docs/development.md) | How to set up, check and build. |

Header-only CSV templates for every input table are in [`schemas/`](schemas/README.md).

[Back to contents](#contents)

## Data and licence

Code is licensed under the [MIT licence](LICENSE). Production and reference data should remain outside Git. The code licence does not establish redistribution rights for BIVAS, DTV, STREAM or notebook-derived parameter tables.

Repository test fixtures are synthetic.

[Back to contents](#contents)
