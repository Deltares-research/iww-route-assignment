# Examples

## Contents

1. [Synthetic repository example](#synthetic-repository-example)
2. [Trip preparation example](#trip-preparation-example)
3. [Small real case](#small-real-case)
4. [Navigability example](#navigability-example)
5. [Assignment example](#assignment-example)
6. [Verification suite](#verification-suite)
7. [Benchmark](#benchmark)

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

A real trip register is run outside this repository, with a short script that holds the settings and calls `run_assignment`. See [Workflow](workflow.md#one-call).

[Back to contents](#contents)

## Small real case

Real data stays outside this repository, so a real case runs from a short external script. It selects a few dozen trips from the register by a written rule, runs `run_assignment` on them, and writes the tables of `module_check_tables` beside the map. The rule should give variation: each main vessel class loaded and empty, each cargo type, a lock, a penalty, and each failure kind.

A case passes when `largest_hand_difference_eur(tables)` stays below `HAND_CHECK_TOLERANCE_EUR`. See [Checking a run](workflow.md#checking-a-run).

[Back to contents](#contents)

## Navigability example

```powershell
poetry run python examples/check_route_navigability.py
```

The example evaluates the two synthetic routes for three vessel states and prints one summary row per route and vessel, the findings and choice weights in which a blocked arc is infinite. Expected outcomes are hand-checked in `tests/test_navigability.py`. See [Navigability](navigability.md).

[Back to contents](#contents)

## Assignment example

```powershell
poetry run python examples/assign_synthetic_trips.py
```

Three trips sail from A to D on a four-node network built in code. The settings at the top of the file choose the metric, the lock width and the beam penalty. By cost the trips take A-B-D. By time, or with a lock narrower than the vessel, they take A-C-D. All tables are made up and labelled as such. `tests/assignment/test_example.py` keeps the example working. See [Traffic assignment](assignment.md).

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
