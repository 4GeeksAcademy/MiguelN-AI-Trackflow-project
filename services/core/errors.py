class DomainError(Exception):
    """Base class for business rule violations raised by the service layer.

    Routers translate these into HTTP responses; services stay HTTP-agnostic.
    """


class EmailAlreadyRegistered(DomainError):
    def __init__(self, email: str) -> None:
        super().__init__(f"Email {email} is already registered")
        self.email = email


class InvalidResetToken(DomainError):
    """A password reset token that cannot be honoured.

    `reason` says which way it failed - forged, expired, unknown or already
    spent. It is for the log only: the router answers every case with the same
    message, so a caller cannot use the response to tell a real but spent token
    from one they invented.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"Password reset token {reason}")
        self.reason = reason


class IncorrectPassword(DomainError):
    """The current password supplied to a change-password request did not match."""

    def __init__(self) -> None:
        super().__init__("Current password is incorrect")


class ValidationFailed(DomainError):
    """A payload the service layer refused on a business rule.

    Routers turn this into a 400 whose body names `field` and repeats `message`
    verbatim, so the message has to read as plain language for whoever is
    looking at the form - not as a rule identifier.
    """

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


class IncidentNotFound(DomainError):
    """No incident exists with the requested id."""

    def __init__(self, incident_id: str) -> None:
        super().__init__(f"Incident {incident_id} not found")
        self.incident_id = incident_id


class ProductNotFound(DomainError):
    """No inventory product exists with the requested UUID."""


class ProductAlreadyExists(DomainError):
    """A SKU already exists in the requested warehouse."""
