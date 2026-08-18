"""Assert every hand-calculated verification case and record the evidence.

Set ``IWW_VERIFICATION_REPORT`` to a file path to write a machine-readable
verification record. The external report generator renders that record into a
dated markdown report.
"""

from __future__ import annotations

from typing import Any

import pytest

from .cases import CASES, TOLERANCE, VerificationCase
from .recording import RECORDS


@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
def test_verification_case(case: VerificationCase) -> None:
    """Compare calculated values with the hand calculation for one case."""
    actual = case.compute()
    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    for name, expected in case.expected.items():
        observed = actual[name]
        if isinstance(expected, str):
            passed = observed == expected
            deviation: float | None = None
        else:
            deviation = abs(float(observed) - float(expected))
            passed = deviation <= TOLERANCE
        checks.append(
            {
                "quantity": name,
                "expected": expected,
                "actual": observed,
                "absolute_deviation": deviation,
                "passed": passed,
            }
        )
        if not passed:
            failures.append(f"{name}: expected {expected!r}, calculated {observed!r}")
    RECORDS.append(
        {
            "case_id": case.case_id,
            "requirement": case.requirement,
            "description": case.description,
            "hand_calculation": case.hand_calculation,
            "tolerance": TOLERANCE,
            "checks": checks,
            "passed": not failures,
        }
    )
    assert not failures, f"{case.case_id} failed: " + "; ".join(failures)
