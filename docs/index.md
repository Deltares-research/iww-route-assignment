# IWW route assignment documentation

## Contents

1. [Status](#status)
2. [Documentation map](#documentation-map)
3. [Delivery roadmap](#delivery-roadmap)

## Status

The project is pre-alpha. Work package 1 provides the monetary cost core and audited fuel-resolution contracts. Route search, navigability, iterative assignment, OpenTNSim coupling and unloading are not yet implemented.

[Back to contents](#contents)

## Documentation map

- [Architecture](architecture.md)
- [Cost module](cost-module.md)
- [Data contracts](data-contracts.md)
- [Examples](examples.md)
- [Development](development.md)

The external IWW design folder contains dated modelling documents and implementation plans.

[Back to contents](#contents)

## Delivery roadmap

```mermaid
flowchart LR
    A[Cost function] --> B[Iterative routing]
    B --> C[Navigability]
    C --> D[OpenTNSim adapter]
    D --> E[Scalability evidence]
    E --> F[Capability review]
    F --> G[Unloading]
```

Every applied work package uses the illustrative case before a production case.

[Back to contents](#contents)

