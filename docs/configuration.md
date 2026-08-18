# Case configuration

The generic case runner `run_cost_case.py` takes exactly one argument, the path to a case TOML file. Every relative path inside that file is resolved against the folder holding the file, so a case folder is portable between machines and contains no user profile or OneDrive path.

## Contents

1. [What is input, assumption and output](#what-is-input-assumption-and-output)
2. [Complete example](#complete-example)
3. [Inputs section](#inputs-section)
4. [Scenario section](#scenario-section)
5. [Run section](#run-section)
6. [Output section](#output-section)
7. [Where each scenario parameter is used](#where-each-scenario-parameter-is-used)
8. [Recommended case folder layout](#recommended-case-folder-layout)

## What is input, assumption and output

Nothing in the case TOML is generated. You author all of it. The distinction that matters is between values that point at measured or derived data and values that state a belief.

| Section | Kind | Who provides it |
| --- | --- | --- |
| `[inputs]` | Data references | You supply four CSV files that satisfy the [data contracts](data-contracts.md). |
| `[scenario]` | Economic assumptions | You choose them. They are not read from any table. |
| `[run]` | Method and quality assumptions | You choose them. They control algorithm depth and acceptance. |
| `[output]` | Destination | You choose the folder. Its contents are written by the runner. |

The three files under the output folder are the only generated artefacts: `cost_results.csv`, `validation_report.json` and `run_metadata.json`.

[Back to contents](#contents)

## Complete example

```toml
[inputs]
arcs = "input/arc_cost_requests.csv"
fuel_observations = "input/fuel_observations.csv"
vessel_references = "input/vessel_references.csv"
cost_rates = "input/cost_rates.csv"

[scenario]
fuel_price_eur_per_l = 1.20
interest_rate_percent = 3.0
cargo_waiting_time_h = 1.0
idle_fuel_factor = 0.15

[run]
fuel_policy = "audited_proxy"
max_low_or_fallback_share = 0.25

[output]
directory = "output"
```

[Back to contents](#contents)

## Inputs section

All four keys are required. Each names a CSV whose required columns are fixed by the data contracts, and whose header template is committed under `schemas/`.

| Key | Contract | Role in the calculation |
| --- | --- | --- |
| `arcs` | [Arc requests](data-contracts.md#arc-requests-and-results) | One row per arc to cost. Drives the loop. |
| `fuel_observations` | [Fuel observations](data-contracts.md#fuel-observations) | Litres per hour by vessel, waterway and profile. |
| `vessel_references` | [Vessel references](data-contracts.md#vessel-references) | Vessel attributes used only for proxy selection. |
| `cost_rates` | [Cost rates](data-contracts.md#cost-rates) | BIVAS rate row per appearance and vessel class. |

`vessel_references` is required even under `strict`, because the loader always builds the provider from both tables. Under `strict` the attributes are never consulted.

[Back to contents](#contents)

## Scenario section

These four values map one to one onto the frozen `ScenarioCostParameters` value object. They are scenario assumptions, so record their origin in your case notes. All must be finite and non-negative.

| Key | Unit | Required | Meaning |
| --- | --- | --- | --- |
| `fuel_price_eur_per_l` | EUR per litre | Yes | Price applied to every litre of sailing and idle fuel. |
| `interest_rate_percent` | percentage points | Yes | Capital interest rate. Enter `3.0` for 3 per cent, not `0.03`. |
| `cargo_waiting_time_h` | hours | Yes | Assumed cargo waiting time paid at the waiting labour rate, once per loaded trip. |
| `idle_fuel_factor` | fraction of the sailing rate | No, default `0.15` | Share of the sailing fuel rate burnt per hour of reported fixed time. Must not exceed 1. |

A scenario is compared with another scenario by changing these values only, keeping the four input files fixed. That keeps the comparison attributable.

[Back to contents](#contents)

## Run section

Both keys are optional, but both should be set explicitly in any case that will be reviewed.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `fuel_policy` | `strict` or `audited_proxy` | `audited_proxy` | Fallback depth allowed when the fuel table has no exact row. |
| `max_low_or_fallback_share` | fraction, 0 to 1 | `1.0`, meaning no limit | Largest acceptable share of rows resolved with `low` or `fallback` confidence. |

`fuel_policy` values, the ladder they permit and the resulting method and confidence labels are documented once in [Enumerations](enumerations.md#fuel-resolution-policy). Nothing else in the runner depends on the policy.

`max_low_or_fallback_share` is an acceptance threshold, not a modelling parameter. The runner writes all three output files first and then raises `RuntimeError` if the observed share exceeds the limit, so a failed run can still be inspected. A breach means the fuel table lacks coverage for your fleet or network, and the correct response is to extend the table rather than to raise the limit.

This is a graceful failure. A `FuelCoverageError` from an unresolvable row is not: the runner has no try/except around resolution, so the first row that cannot be resolved under the active policy aborts the whole run before any output file is written. See [which fuel-ladder steps run every request](enumerations.md#which-steps-run-on-every-request) for exactly when that can happen under each policy.

[Back to contents](#contents)

## Output section

| Key | Default | Meaning |
| --- | --- | --- |
| `directory` | `output` | Folder created if absent, then filled with the generated artefacts. |

| Generated file | Contents |
| --- | --- |
| `cost_results.csv` | One row per arc request, with every time, cost and fuel-audit column. |
| `validation_report.json` | Row total, method counts, confidence counts, low or fallback share, the configured limit and whether the run is within it. |
| `run_metadata.json` | Package version, Python version, platform, applied fuel policy, SHA-256 of the config and of every input file, and the row count. |

The SHA-256 digests make a result reproducible: the same digests plus the same package version identify the run exactly. Treat the output folder as disposable and regenerate it rather than editing it.

[Back to contents](#contents)

## Where each scenario parameter is used

All four parameters enter through `iww_route_assignment.costs.calculator`. The table gives the exact result columns they affect.

| Parameter | Used in | Result columns affected |
| --- | --- | --- |
| `fuel_price_eur_per_l` | `calculate_arc_costs` | `sailing_fuel_cost_eur`, `idle_fuel_cost_eur`, and therefore `reported_variable_cost_eur` and `choice_cost_eur`. |
| `interest_rate_percent` | `calculate_arc_costs` | `interest_cost_eur`, and through the hourly rate also `choice_only_cost_eur`. |
| `cargo_waiting_time_h` | `calculate_trip_fixed_cost` | `cargo_waiting_cost_eur` and `fixed_trip_cost_eur`, loaded trips only. |
| `idle_fuel_factor` | `calculate_arc_costs` | `idle_fuel_l` and `idle_fuel_cost_eur`. |

The capital interest rate is used in two distinct places, which is easy to miss:

1. As a per-arc cost component:

   $$
   C_{interest} = r \, c_{interest,\%,h} \, t_{reported}
   $$

   with $r$ the rate in percentage points and $c_{interest,\%,h}$ the `interest_pct_h` column of the cost-rate table, expressed as EUR per percentage point per hour. This lands in `interest_cost_eur` and is part of `time_cost_eur`.

2. As one term of the hourly operating rate $c_h$ used to monetise route-choice-only time:

   $$
   c_h = c_{labour} + c_{maint,h} + c_{insurance} + r \, c_{interest,\%,h}
       + c_{depreciation} + c_{port} + c_{other}
   $$

   so raising the interest rate also raises `choice_only_cost_eur`, which is the money value of calibration and relaxation time.

The interest rate does not enter the trip fixed cost, the distance cost or any fuel term. Because both uses are linear in $r$ and multiply reported time, the interest rate has no effect on an arc with zero reported time, and it is unaffected by cargo weight.

[Back to contents](#contents)

## Recommended case folder layout

```text
my_case/
    case.toml
    input/
        arc_cost_requests.csv
        fuel_observations.csv
        vessel_references.csv
        cost_rates.csv
    output/
        cost_results.csv
        validation_report.json
        run_metadata.json
    notes.md
```

Keep `case.toml` and `notes.md` under version control where the data licence allows it. Keep `input/` and `output/` out of Git when they hold BIVAS, DTV, STREAM or IVS derived values. See the [data and licence policy](data-contracts.md#policy).

[Back to contents](#contents)


