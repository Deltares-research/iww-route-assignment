"""Package-specific exceptions with actionable failure categories."""


class IWWRouteAssignmentError(Exception):
    """Base class for expected package errors."""


class ValidationError(IWWRouteAssignmentError, ValueError):
    """Raised when a model or input table violates its contract."""


class DuplicateFuelObservationError(ValidationError):
    """Raised when a fuel key is not unique within a vessel profile."""


class FuelCoverageError(IWWRouteAssignmentError, LookupError):
    """Raised when the configured fuel policy cannot resolve a request."""
