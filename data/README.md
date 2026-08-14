# Reference-data status

The previous `fuel_consumption_table.csv` was removed from Git. It contained a generated index, combined general and container rows without a profile field, and had incomplete vessel coverage.

Use `prepare_notebook_fuel_observations` to create a profile-aware table with `index=False`. The notebook writes generated data under the ignored `output` folder. Runtime interpolation and proxies use source observations, not pre-filled rows.

The source, units and redistribution rights of the notebook-derived values require confirmation. The MIT code licence does not establish permission to redistribute these data. Do not commit, package or publish the generated table until that review is complete.

