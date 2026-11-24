# Development

## Contents

1. [Set up](#set-up)
2. [Quality checks](#quality-checks)
3. [Package build](#package-build)
4. [Continuous integration](#continuous-integration)
5. [Change rules](#change-rules)

## Set up

Python 3.13 or later is required.

```powershell
poetry install --extras dev
```

The `dev` optional dependency set contains test, coverage, lint, type and build tools.

[Back to contents](#contents)

## Quality checks

```powershell
poetry run ruff check .
poetry run ruff format --check .
poetry run mypy src
poetry run pytest --cov=iww_route_assignment --cov-report=term-missing
```

Tests use synthetic data. Add a hand-calculated test for every formula change.

The test suite also runs both examples. `tests/assignment/test_example.py` runs the synthetic assignment in `examples/assign_synthetic_trips.py` and checks the route choice it describes: by cost the shorter route with the lock, by time the faster route, and the faster route again when the lock is too narrow. It also checks that every cost column is filled and that fuel rises with the load. `tests/test_cost_example.py` runs `examples/calculate_arc_cost.py`. Both need no data files and take about two seconds.

[Back to contents](#contents)

## Package build

```powershell
poetry run python -m build
```

Install the wheel in a clean virtual environment and run the synthetic example before release.

[Back to contents](#contents)

## Continuous integration

The workflow in `.github/workflows/ci.yml` runs on every pull request and on every push to `main`. It has two jobs, both on Python 3.13.

| Job | What it does |
| --- | --- |
| `build` | Builds the package, so a broken package definition is found early. |
| `quality` | Runs the quality steps below. |

The quality job runs these steps in order.

| Step | Purpose |
| --- | --- |
| `ruff check .` | Finds likely bugs and unused imports. |
| `ruff format --check .` | Keeps the code style consistent and diffs clean. |
| `mypy src` | Finds type errors before the code runs. |
| `pytest` with coverage | Runs the tests, including both examples. Fails when coverage drops below 90 %. |

### Why CI can fail without a code change

CI installs the newest release of every tool, because `pyproject.toml` sets only minimum versions for `ruff`, `mypy` and `pandas-stubs`. A new release can be stricter than the version in your local environment. `mypy` with new `pandas-stubs` is the most common case: the code passes locally and fails in CI.

To reproduce a CI failure, create a clean virtual environment, run `python -m pip install -e ".[dev]"` and run the failing step there. That environment gets the same tool versions as CI.

Capping the tool versions, for example `mypy>=2.4,<2.5`, stops these surprise failures. You then upgrade a tool on purpose, and local results match CI.

### Rules for changing the workflow

- Keep every job and step. Each one catches a different kind of fault.
- CI tests only Python 3.13, because `requires-python` is `>=3.13`. Add a Python version to CI in the same change that allows it in `requires-python`, otherwise its support is claimed without a test.
- The workflow uses `actions/checkout@v7` and `actions/setup-python@v7`, which run on Node.js 24. When GitHub warns that an action's Node.js version is deprecated, check the current major version of the action and update to it. The warning alone does not fail the run.

[Back to contents](#contents)

## Change rules

- Preserve the package boundaries described in [Architecture](architecture.md#dependency-rules).
- Update model and data-contract documentation with behaviour changes.
- Document a new enumeration member in [Enumerations](enumerations.md) in the same change that adds it.
- Register every new document in the [documentation map](index.md#documentation-map), so the set stays findable from one place.
- Update the matching template in `schemas/` when a required column changes.
- Do not add production data or private paths.
- Record assumptions and source provenance.
- Keep the readable scalar path and batch path equivalent.
- Keep `costs` and `navigability` independent; combine them only through `restrict_choice_weights`.
- Open separate issues for routing, locks and unloading.

[Back to contents](#contents)

