# Reference-data status

The previous `fuel_consumption_table.csv` was removed from Git. It contained a generated index, combined general and container rows without a profile field, and had incomplete vessel coverage.

Use `build_shared_inputs` to create the fuel table from the energy figures, or `prepare_notebook_fuel_observations` with an explicit `selected_source` and `value_unit`. Values are stored in litres per km. Generated data stays outside Git. Runtime interpolation and proxies use source observations, not pre-filled rows. See [Data lineage](../docs/data-lineage.md#fuel).

The source, units and redistribution rights of the notebook-derived values require confirmation. The MIT code licence does not establish permission to redistribute these data. Do not commit, package or publish the generated table until that review is complete.

The required column contract is in [Fuel observations](../docs/data-contracts.md#fuel-observations), and a header-only template is in [`schemas/`](../schemas/README.md).


