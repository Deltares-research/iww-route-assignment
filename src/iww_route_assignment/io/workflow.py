"""The whole chain in one call: IVS trips in, cheapest routes with costs and limits out.

Three names cover a run::

    paths = AssignmentPaths(trips=..., graph=..., structures=..., reference=...)
    config = AssignmentConfig(scenario=..., cargo_weight_unit="t")
    run = run_assignment(paths, config, output_dir=...)

``AssignmentPaths`` says where the inputs are. ``AssignmentConfig`` holds every choice.
``run_assignment`` reads, checks, assigns and writes. Each step is also a function of its
own, for when only one step is needed.

Where the inputs come from:

- ``trips``: an IVS export (extended 2026 or weekmonitor layout).
- ``graph`` and ``structures``: the EuRIS network files made by fis-crawler, see
  :mod:`iww_route_assignment.io.euris`.
- ``reference``: the folder written by
  :func:`iww_route_assignment.io.reference_build.build_shared_inputs`, holding
  ``cost_rates.csv`` (BIVAS), ``fuel_observations.csv`` (CE Delft), ``vessel_references.csv``
  and ``navigability_vessels.csv`` (DTV).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from ..assignment import (
    AssignmentNetwork,
    AssignmentResult,
    AssignmentSettings,
    AssignmentTrips,
    CostInputs,
    NavigabilityPenalties,
    RoutePreferences,
    VesselCatalogue,
    assign_traffic,
)
from ..domain.enums import DuplicateTripRule, FuelResolutionPolicy, IvsFormat
from ..domain.models import ScenarioCostParameters
from ..domain.navigability import ArcRestriction
from ..exceptions import ValidationError
from .assignment import IVS_SK_CODE_CROSSWALK, read_assignment_trips, write_assignment_outputs
from .euris import EurisLimitRules, load_euris_inputs
from .loaders import load_cost_rates_csv, load_fuel_provider, load_vessel_references_csv
from .navigability import load_navigability_vessels_csv
from .provenance import run_metadata, write_json

logger = logging.getLogger(__name__)

REFERENCE_FILES = {
    "cost_rates": "cost_rates.csv",
    "fuel_observations": "fuel_observations.csv",
    "vessel_references": "vessel_references.csv",
    "navigability_vessels": "navigability_vessels.csv",
}
"""Files of a reference folder. ``navigability_vessels`` is optional."""


@dataclass(frozen=True, slots=True)
class AssignmentPaths:
    """Where the inputs of one run are. No defaults, so no hidden folder is ever read."""

    trips: Path
    graph: Path
    reference: Path
    structures: Path | None = None

    def __post_init__(self) -> None:
        for name in ("trips", "graph", "reference", "structures"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, Path(value))

    def input_files(self) -> dict[str, Path]:
        """Every input file that exists, by a short name, for checksums."""
        files = {"trips": self.trips, "graph": self.graph}
        files |= {name: self.reference / file for name, file in REFERENCE_FILES.items()}
        if self.structures is not None:
            files |= {
                f"structures_{name}": self.structures / f"{name}.geoparquet"
                for name in ("edges", "chambers", "openings")
            }
        return {name: path for name, path in files.items() if path.is_file()}


@dataclass(frozen=True, slots=True)
class AssignmentConfig:
    """Every choice of one run.

    ``scenario`` and ``cargo_weight_unit`` have no default, because both are assumptions that
    must be stated. Everything else has a documented default.

    - ``settings``: route-search metric, load rounding, point columns, workers.
    - ``penalties`` and ``preferences``: penalty hours for unmet limits and for preferences.
    - ``limit_rules``: lock passage time and which EuRIS limits are built.
    - ``class_aliases``: source vessel label to reference class.
    - ``period_start`` and ``period_end``: keep trips that start in this period, by
      ``settings.departure_column``. The end is exclusive.
    - ``sample_size``: route a random sample, for a quick check.
    """

    scenario: ScenarioCostParameters
    cargo_weight_unit: str
    settings: AssignmentSettings = field(default_factory=AssignmentSettings)
    penalties: NavigabilityPenalties = field(default_factory=NavigabilityPenalties)
    preferences: RoutePreferences = field(default_factory=RoutePreferences)
    limit_rules: EurisLimitRules = field(default_factory=EurisLimitRules)
    class_aliases: Mapping[str, str] = field(default_factory=lambda: dict(IVS_SK_CODE_CROSSWALK))
    ivs_format: IvsFormat = IvsFormat.EXTENDED_2026
    drop_export_index: bool = True
    duplicate_trip_ids: DuplicateTripRule = DuplicateTripRule.EXCLUDE
    fuel_policy: FuelResolutionPolicy = FuelResolutionPolicy.AUDITED_PROXY
    period_start: str | None = None
    period_end: str | None = None
    sample_size: int | None = None
    sample_seed: int = 1
    dataset_label: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ivs_format", IvsFormat(self.ivs_format))
        object.__setattr__(self, "duplicate_trip_ids", DuplicateTripRule(self.duplicate_trip_ids))
        object.__setattr__(self, "fuel_policy", FuelResolutionPolicy(self.fuel_policy))
        if self.sample_size is not None and self.sample_size < 1:
            raise ValidationError("sample_size must be positive")


@dataclass(frozen=True, slots=True, eq=False)
class AssignmentInputs:
    """Everything loaded for a run, ready for :func:`assign_traffic`."""

    trips: AssignmentTrips
    network: AssignmentNetwork
    restrictions: tuple[ArcRestriction, ...]
    cost_inputs: CostInputs
    catalogue: VesselCatalogue
    source_frame: pd.DataFrame
    network_report: Mapping[str, Any]


@dataclass(frozen=True, slots=True, eq=False)
class AssignmentRun:
    """A finished run: its inputs, its result and the files it wrote."""

    inputs: AssignmentInputs
    result: AssignmentResult
    config: AssignmentConfig
    output_files: Mapping[str, Path]


def _require(path: Path, what: str) -> Path:
    if not path.exists():
        raise ValidationError(f"{what} not found: {path}")
    return path


def load_cost_inputs(
    reference_dir: str | Path,
    scenario: ScenarioCostParameters,
    fuel_policy: FuelResolutionPolicy = FuelResolutionPolicy.AUDITED_PROXY,
) -> CostInputs:
    """Load cost rates and fuel from a reference folder into one object."""
    folder = Path(reference_dir)
    files = {
        name: _require(folder / REFERENCE_FILES[name], name)
        for name in ("cost_rates", "fuel_observations", "vessel_references")
    }
    return CostInputs(
        load_cost_rates_csv(files["cost_rates"]),
        load_fuel_provider(
            files["fuel_observations"], files["vessel_references"], policy=fuel_policy
        ),
        scenario,
    )


def load_vessel_catalogue(
    reference_dir: str | Path, class_aliases: Mapping[str, str] | None = None
) -> VesselCatalogue:
    """Load vessel dimensions, and air draught states when the folder has them."""
    folder = Path(reference_dir)
    references = load_vessel_references_csv(
        _require(folder / REFERENCE_FILES["vessel_references"], "vessel_references")
    )
    states_file = folder / REFERENCE_FILES["navigability_vessels"]
    states = load_navigability_vessels_csv(states_file) if states_file.is_file() else ()
    return VesselCatalogue(references, states, dict(class_aliases or {}))


def filter_trips_by_period(
    frame: pd.DataFrame, start: str | None, end: str | None, column: str = "Eerste_Tijdstip"
) -> pd.DataFrame:
    """Keep rows whose ``column`` time is at or after ``start`` and before ``end``.

    A row with an unreadable time is dropped. With neither bound the frame is returned as is.
    """
    if start is None and end is None:
        return frame
    if column not in frame.columns:
        raise ValidationError(f"Period filter needs column {column!r}")
    message = f"Period bounds must be dates; received {start!r}, {end!r}"
    try:
        first = None if start is None else pd.Timestamp(start)
        last = None if end is None else pd.Timestamp(end)
    except (TypeError, ValueError) as error:
        raise ValidationError(message) from error
    if any(bound is not None and pd.isna(bound) for bound in (first, last)):
        raise ValidationError(message)  # the text "NaT" parses without an error
    times = pd.to_datetime(frame[column], errors="coerce")
    keep = times.notna()
    if first is not None:
        keep &= times >= first
    if last is not None:
        keep &= times < last
    if not keep.any():
        raise ValidationError(f"No trips start between {start} and {end}")
    return frame[keep]


def read_trip_frame(path: str | Path) -> pd.DataFrame:
    """Read an IVS export as text, so identifiers keep their exact form."""
    source = _require(Path(path), "trips")
    if source.suffix.lower() == ".parquet":
        return pd.read_parquet(source).astype("string")
    return pd.read_csv(source, dtype="string")


def load_assignment_inputs(paths: AssignmentPaths, config: AssignmentConfig) -> AssignmentInputs:
    """Read trips, network, limits, costs and vessels for one run."""
    frame = read_trip_frame(paths.trips)
    column = config.settings.departure_column or "Eerste_Tijdstip"
    frame = filter_trips_by_period(frame, config.period_start, config.period_end, column)
    if config.sample_size is not None and config.sample_size < len(frame):
        frame = frame.sample(n=config.sample_size, random_state=config.sample_seed)
    trips = read_assignment_trips(
        frame,
        source_dataset=config.dataset_label or paths.trips.stem,
        format=config.ivs_format,
        cargo_weight_unit=config.cargo_weight_unit,
        drop_export_index=config.drop_export_index,
        duplicate_trip_ids=config.duplicate_trip_ids,
        settings=config.settings,
    )
    euris = load_euris_inputs(_require(paths.graph, "graph"), paths.structures, config.limit_rules)
    return AssignmentInputs(
        trips,
        euris.network,
        euris.restrictions,
        load_cost_inputs(paths.reference, config.scenario, config.fuel_policy),
        load_vessel_catalogue(paths.reference, config.class_aliases),
        frame,
        euris.report,
    )


def assign(inputs: AssignmentInputs, config: AssignmentConfig) -> AssignmentResult:
    """Assign loaded inputs. Reads and writes no files."""
    return assign_traffic(
        inputs.trips,
        inputs.network,
        inputs.cost_inputs,
        inputs.catalogue,
        inputs.restrictions,
        settings=config.settings,
        penalties=config.penalties,
        preferences=config.preferences,
    )


def run_assignment(
    paths: AssignmentPaths,
    config: AssignmentConfig,
    output_dir: str | Path | None = None,
    *,
    write_map: bool = True,
) -> AssignmentRun:
    """Read the inputs, assign every trip, and write the outputs.

    With ``output_dir`` the run writes the result tables, ``run_metadata.json`` (settings and
    input checksums) and, unless ``write_map`` is false, the interactive ``assignment_map.html``.
    Without it nothing is written.
    """
    inputs = load_assignment_inputs(paths, config)
    result = assign(inputs, config)
    files: dict[str, Path] = {}
    if output_dir is not None:
        folder = Path(output_dir)
        files |= write_assignment_outputs(result, folder, metadata={"config": config})
        files["run_metadata"] = write_json(
            folder / "run_metadata.json",
            run_metadata(
                paths.input_files(),
                config=config,
                network_report=dict(inputs.network_report),
                summary=dict(result.summary),
            ),
        )
        if write_map:
            from .viewer import write_assignment_map

            files["map"] = write_assignment_map(
                result, inputs.network, folder / "assignment_map.html"
            )
    logger.info("Assignment run finished: %s", result.summary["status_counts"])
    return AssignmentRun(inputs, result, config, files)
