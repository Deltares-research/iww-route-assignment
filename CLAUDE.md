# Development guidance

## Architecture

- Keep domain and numerical code independent of files, routing engines and OpenTNSim.
- Put pandas and file formats in `iww_route_assignment.io`.
- Keep reported time and cost separate from route-choice penalties.
- Preserve source and fallback metadata in every derived result.
- Keep OpenTNSim optional behind a typed adapter.

## Modelling

- Use BIVAS formulas where specified and cite requirement identifiers in design documents.
- Use explicit unit suffixes in public fields.
- Treat fuel light, medium and heavy as ship-loading categories.
- Keep general and container fuel profiles separate.
- Never resolve duplicate or missing reference rows by input order.
- Do not claim scalability before benchmark evidence exists.

## Data

- Keep production and reference datasets outside Git.
- Commit only schemas, provenance notes and small licence-safe synthetic fixtures.
- Check redistribution rights separately from the MIT code licence.
- Never hard-code a user profile or OneDrive path in reusable code.

## External working folders

- Keep run scripts, case folders and generated reports in the external IWW folder, not in this repository.
- Instructions live in `IWW/model/instructions`, design documents in `IWW/model/design` and verification reports in `IWW/model/verification`.
- Name every generated instruction, design document and report `<yyyymmdd>_descriptive_name`.
- Update the run instruction whenever a script, a folder convention or an acceptance check changes.

## Code and documentation

- Support Python 3.12 and later.
- Use frozen dataclasses and protocols for public contracts.
- Use British English and no em dashes in user-facing documentation.
- Update module documentation whenever behaviour or schemas change.
- Document every enumeration member and run option in `docs/enumerations.md`.
- Register every new document in the map in `docs/index.md`.
- Create or update `CLAUDE.md`, not `AGENTS.md`.

## Commands

```powershell
poetry install --extras dev
poetry run ruff check .
poetry run ruff format --check .
poetry run mypy src
poetry run pytest --cov=iww_route_assignment --cov-report=term-missing
poetry run python -m build
poetry run python examples/calculate_arc_cost.py
poetry run python examples/prepare_ivs_arc_requests.py
```

## Tests

- Add hand-calculated tests for every formula change.
- Cover malformed, missing, duplicate, non-finite and boundary inputs.
- Assert scalar and batch equivalence.
- Test deterministic proxy selection under input reordering.
- Keep verification cases in `tests/verification` and regenerate the dated report after a formula change.
- Run the full suite before a pull request.

