# Examples

## Contents

1. [Synthetic repository example](#synthetic-repository-example)
2. [Illustrative external case](#illustrative-external-case)
3. [Benchmark](#benchmark)

## Synthetic repository example

Run from PowerShell:

```powershell
poetry run python examples/calculate_arc_cost.py
```

In PyCharm, open `examples/calculate_arc_cost.py`, select the Poetry interpreter and run `main()`.

The example prints arc components, trip-fixed cost, total cost and fuel audit metadata. It uses no private data.

[Back to contents](#contents)

## Illustrative external case

The generic `run_cost_case.py` script is in the shared IWW scripts folder. Its only command argument is a case TOML path. All input and output paths are relative to that case file.

The synthetic case under `runs/test_case_cost_function` covers observed, interpolated and proxied fuel rates. It is not the Weurt case.

[Back to contents](#contents)

## Benchmark

```powershell
poetry run python benchmarks/benchmark_cost_batch.py --rows 100000
```

The output contains runtime, rows per second, Python peak memory, Python version and platform. Results are descriptive until repeated on agreed benchmark hardware.

[Back to contents](#contents)

