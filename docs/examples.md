# Examples

## Contents

1. [Synthetic repository example](#synthetic-repository-example)
2. [Trip preparation example](#trip-preparation-example)
3. [Illustrative external case](#illustrative-external-case)
4. [Verification suite](#verification-suite)
5. [Benchmark](#benchmark)

## Synthetic repository example

Run from PowerShell:

```powershell
poetry run python examples/calculate_arc_cost.py
```

In PyCharm, open `examples/calculate_arc_cost.py`, select the Poetry interpreter and run `main()`.

The example prints arc components, trip-fixed cost, total cost and fuel audit metadata. It uses no private data.

[Back to contents](#contents)

## Trip preparation example

```powershell
poetry run python examples/prepare_ivs_arc_requests.py
```

The example reads the synthetic trip fixture in `tests/fixtures`, derives arc-cost requests, prints the rejected rows with their reason codes and prints the coverage summary. It uses no private data, so it shows the adapter without an IVS licence.

A real trip register is prepared outside this repository. `prepare_ivs_arcs.py` in the shared IWW scripts folder reads the export, writes the arc-request table, the rejection table and a preparation report, and leaves the case runner unchanged.

[Back to contents](#contents)

## Illustrative external case

The generic `run_cost_case.py` script is in the shared IWW scripts folder. Its only command argument is a case TOML path. All input and output paths are relative to that case file.

```powershell
poetry run python run_cost_case.py my_case/case.toml
```

Every TOML key, the four required input tables, the scenario assumptions and the three generated output files are documented in [Case configuration](configuration.md). Copy the header templates from [`schemas/`](../schemas/README.md) to start a new case.

The synthetic case under `runs/test_case_cost_function` covers observed, interpolated and proxied fuel rates. It is not the Weurt case.

The IVS case under `runs/case_ivs_weekmonitor_2023` applies the module to a real trip register. Its README records coverage, every applied assumption and the plausibility check. It is an application case, not a verification.

[Back to contents](#contents)

## Verification suite

```powershell
poetry run pytest tests/verification -q
```

Every case compares a calculated value with a hand calculation. See [Verification](verification.md) for the case list, the report generator and the scope.

[Back to contents](#contents)

## Benchmark

```powershell
poetry run python benchmarks/benchmark_cost_batch.py --rows 100000
```

The output contains runtime, rows per second, Python peak memory, Python version and platform. Results are descriptive until repeated on agreed benchmark hardware.

[Back to contents](#contents)
