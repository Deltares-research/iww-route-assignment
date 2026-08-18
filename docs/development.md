# Development

## Contents

1. [Set up](#set-up)
2. [Quality checks](#quality-checks)
3. [Package build](#package-build)
4. [Change rules](#change-rules)

## Set up

Python 3.12 and 3.13 are supported.

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

[Back to contents](#contents)

## Package build

```powershell
poetry run python -m build
```

Install the wheel in a clean virtual environment and run the synthetic example before release.

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
- Open separate issues for routing, navigability, locks and unloading.

[Back to contents](#contents)

