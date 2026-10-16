"""Collect verification evidence and write a machine-readable record."""

from __future__ import annotations

import json
import os
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import iww_route_assignment

REPORT_PATH_VARIABLE = "IWW_VERIFICATION_REPORT"

RECORDS: list[dict[str, Any]] = []


def write_report(exit_status: int, tolerance: float) -> Path | None:
    """Write the verification record when a report path is configured."""
    destination = os.environ.get(REPORT_PATH_VARIABLE)
    if not destination or not RECORDS:
        return None
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "package_version": iww_route_assignment.__version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "tolerance": tolerance,
        "exit_status": int(exit_status),
        "case_count": len(RECORDS),
        "failed_case_count": sum(not record["passed"] for record in RECORDS),
        "cases": sorted(RECORDS, key=lambda record: str(record["case_id"])),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path
