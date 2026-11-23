"""Record what produced a result: input checksums, package version, platform and settings."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from importlib import metadata
from pathlib import Path
from typing import Any

PACKAGE = "iww-route-assignment"


def sha256_file(path: str | Path) -> str:
    """Checksum a file in chunks, so large inputs do not fill memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def to_record(value: Any) -> Any:
    """Turn settings dataclasses, enums, paths and sets into JSON-ready values."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_record(getattr(value, f.name)) for f in fields(value) if f.init}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, frozenset | set):
        return sorted(to_record(v) for v in value)
    if isinstance(value, Mapping):
        return {str(k): to_record(v) for k, v in value.items()}
    if isinstance(value, tuple | list):
        return [to_record(v) for v in value]
    return value


def run_metadata(inputs: Mapping[str, str | Path], **extra: Any) -> dict[str, Any]:
    """Return the provenance block written beside every generated file.

    ``inputs`` maps a short name to an input file. Each file is recorded with its name and
    checksum, never with its full path, so results do not carry a user folder.
    """
    try:
        version = metadata.version(PACKAGE)
    except metadata.PackageNotFoundError:  # pragma: no cover - source checkout without install
        version = "unknown"
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "package_version": version,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "input_files": {name: Path(path).name for name, path in sorted(inputs.items())},
        "input_sha256": {name: sha256_file(path) for name, path in sorted(inputs.items())},
        **to_record(extra),
    }


def write_json(path: str | Path, value: Any) -> Path:
    """Write sorted, indented JSON with a trailing newline."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(to_record(value), indent=2, sort_keys=True, default=str) + "\n", "utf-8"
    )
    return target
