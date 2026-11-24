# Verification

## Contents

1. [What verification means here](#what-verification-means-here)
2. [Where the cases live](#where-the-cases-live)
3. [How to run the suite](#how-to-run-the-suite)
4. [How to generate a report](#how-to-generate-a-report)
5. [Adding a case](#adding-a-case)
6. [Scope and limits](#scope-and-limits)

## What verification means here

Verification compares a calculated value with an independent hand calculation of
the same quantity. It answers one question: does the code compute what the
formulation says it computes.

It is separate from an application case. An application run on observed data,
such as the IVS illustrative case, has no independent expected cost, so it can
only produce coverage, assumption and plausibility evidence. Neither replaces
the other.

[Back to contents](#contents)

## Where the cases live

| Path | Role |
| --- | --- |
| `tests/verification/cases.py` | Inputs, hand calculation and expected values per case. |
| `tests/verification/test_cost_verification.py` | Asserts every expectation and records the evidence. |
| `tests/verification/recording.py` | Writes the machine-readable verification record. |
| `tests/verification/conftest.py` | Writes the record once the session finishes. |

Each case carries a stable identifier, the requirement it verifies, the
arithmetic in words and the expected values, so a reviewer can repeat the
calculation without running the code.

[Back to contents](#contents)

## How to run the suite

```powershell
poetry run pytest tests/verification -q
```

The suite is part of the normal test run as well. The absolute tolerance is
`1e-9`, which is a numerical tolerance, not a modelling one.

[Back to contents](#contents)

## How to generate a report

Set `IWW_VERIFICATION_REPORT` to a path and the suite writes a JSON record of
every case and every comparison:

```powershell
$env:IWW_VERIFICATION_REPORT = "verification_record.json"
poetry run pytest tests/verification -q
```

The external `verify_report.py` script wraps this and renders a
dated markdown report named `<yyyymmdd>_cost_function_verification_report.md`.

[Back to contents](#contents)

## Adding a case

1. Add a `VerificationCase` to `tests/verification/cases.py` with a new
   identifier, the requirement, the hand calculation and the expected values.
2. Add a `compute` function that returns the same quantities from the library.
3. Run the suite and regenerate the report.

Add a case for every formula change, and keep the hand calculation in the case
rather than in a commit message.

[Back to contents](#contents)

## Scope and limits

Covered: arc time and cost decomposition, the interest convention, the
route-independent trip cost, the trip total, fuel interpolation over loading
ratio and waterway class, profile separation, and scalar and batch equivalence.

Not covered: routing, navigability, lock delays and unloading, which are later
work packages; and the quality of any input dataset, which belongs to the
coverage report of the case that uses it.

[Back to contents](#contents)

