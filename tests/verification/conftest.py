"""Write the verification record after the verification session finishes."""

from __future__ import annotations

import pytest

from .cases import TOLERANCE
from .recording import write_report


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Persist the collected verification evidence if a path is configured."""
    write_report(int(exitstatus), TOLERANCE)
