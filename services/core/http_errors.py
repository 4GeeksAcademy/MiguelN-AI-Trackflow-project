"""Turning failures into responses a person can act on.

Three things this module exists to prevent. A malformed payload must not come
back as FastAPI's default 422 with a list of pydantic error dictionaries: the
incident manager owes the caller a 400 with one named field and a sentence. A
business rule refusal must say which field it was about. And an unhandled
exception must never reach the client as a traceback - the response says only
that something went wrong, while the detail goes to the log.

Every handler here is registered on the app in `services.main`. They take a
bare `Exception` because that is the signature Starlette's handler registry
declares; each one narrows to the type it was registered for and hands anything
unexpected to the generic 500, so a misregistration degrades instead of
crashing.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from services.core.errors import (
    IncidentNotFound,
    ProductAlreadyExists,
    ProductNotFound,
    ValidationFailed,
)

logger = logging.getLogger("trackflow.errors")

# Wrappers FastAPI puts at the head of `loc` to say where the value came from.
# They are noise to a form: the field is "title", not "body.title".
_LOCATION_PREFIXES = frozenset({"body", "query", "path", "header", "cookie"})

# Says nothing about what failed or whether anything was written: the handler
# catches reads and writes alike, and it cannot know which side of a write the
# exception landed on.
GENERIC_SERVER_ERROR = (
    "Something went wrong on our side. Please try again, and contact "
    "TrackFlow Tech if the problem continues."
)


def _problem(status_code: int, field: str | None, message: str) -> JSONResponse:
    """One error response, in the shape every handler here returns.

    `detail` is always an object with `field` and `message`, so a caller can
    read an error the same way whether it came from a 400, a 404 or a 500.
    """
    return JSONResponse(
        status_code=status_code,
        content={"detail": {"field": field, "message": message}},
    )


def _field_name(location: tuple[Any, ...]) -> str:
    """The dotted field path, minus FastAPI's location wrapper.

    An error on the request as a whole - unparseable JSON, say - has nothing
    left after the prefix is dropped, so it is reported against "body".
    """
    parts = [str(part) for part in location if part not in _LOCATION_PREFIXES]
    return ".".join(parts) if parts else "body"


def _plain_message(error: dict[str, Any]) -> str:
    """Restate one pydantic error as a sentence.

    Only the error types this API can actually produce are spelled out. Anything
    unexpected falls through to pydantic's own wording, which is stilted but
    still describes the right problem - better than a generic line that hides it.
    """
    error_type = error.get("type", "")
    context: dict[str, Any] = error.get("ctx") or {}

    if error_type == "missing":
        return "This field is required."

    if error_type == "enum":
        # pydantic pre-formats the allowed values, e.g. "'open', 'in_progress'".
        return f"Must be one of: {context.get('expected', 'the allowed values')}."

    if error_type == "string_too_short":
        minimum = context.get("min_length")
        if minimum == 1:
            return "This field cannot be empty."
        return f"Must be at least {minimum} characters long."

    if error_type == "string_too_long":
        return f"Must be at most {context.get('max_length')} characters long."

    if error_type in {"string_type", "string_pattern_mismatch"}:
        return "Must be text."

    if error_type in {"int_parsing", "int_type"}:
        return "Must be a whole number."

    if error_type in {"float_parsing", "float_type"}:
        return "Must be a number."

    if error_type == "greater_than":
        return f"Must be greater than {context.get('gt')}."

    if error_type == "too_short":
        return f"Must contain at least {context.get('min_length')} item(s)."

    if error_type in {"datetime_parsing", "datetime_from_date_parsing", "datetime_type"}:
        return "Must be a valid date and time."

    if error_type == "json_invalid":
        return "The request body is not valid JSON."

    if error_type in {"dict_type", "model_attributes_type"}:
        return "The request body must be a JSON object."

    if error_type == "extra_forbidden":
        return "This field is not recognised."

    message = str(error.get("msg", "This value is not valid."))

    if error_type == "value_error":
        # A validator's own `ValueError`, prefixed by pydantic. The email type
        # appends the parser's diagnosis, which is more than a form needs.
        if message.startswith("value is not a valid email address"):
            return "Enter a valid email address."
        message = message.removeprefix("Value error, ")

    return message[:1].upper() + message[1:]


def make_validation_error_handler(
    prefixes: tuple[str, ...],
) -> Callable[[Request, Exception], Awaitable[Response]]:
    """Build the handler that answers a failed parse with 400, naming the field.

    Scoped to `prefixes` rather than applied to the whole app. Exception
    handlers in Starlette are global, but this remapping is not: the auth,
    users, profiles and suppliers routes were built against FastAPI's 422 and
    its `detail` array, and both UIs branch on the difference. `resetPassword`
    in `uis/*/services/auth-service.ts` reads **any** 400 as a spent reset
    token and replaces the form with "request a new link", so remapping a
    too-short password from 422 to 400 there would tell someone their link had
    expired when it had not.

    Outside `prefixes` the status and the `detail` array stay exactly as
    FastAPI shapes them - `type`, `loc`, `msg` - with two differences: the
    submitted value is not echoed back under `input` (FastAPI's default returns
    a rejected password verbatim), and `msg` is the same plain sentence the
    incident routes use.
    """

    async def handler(request: Request, exc: Exception) -> Response:
        if not isinstance(exc, RequestValidationError):
            return unhandled_exception_handler(request, exc)

        if not request.url.path.startswith(prefixes):
            return JSONResponse(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                content={
                    "detail": [
                        {
                            "type": error.get("type", ""),
                            "loc": list(error.get("loc", ())),
                            "msg": _plain_message(error),
                        }
                        for error in exc.errors()
                    ]
                },
            )

        # `detail` carries the first problem so a form can focus that input;
        # `errors` carries all of them so it can mark every bad field at once.
        problems = [
            {"field": _field_name(tuple(error.get("loc", ()))), "message": _plain_message(error)}
            for error in exc.errors()
        ]
        if not problems:
            problems = [{"field": "body", "message": "The request could not be read."}]

        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": problems[0], "errors": problems},
        )

    return handler


def validation_failed_handler(request: Request, exc: Exception) -> JSONResponse:
    """A business rule refused the request: 400, naming the field.

    `exc.message` is passed through untouched, which is why the service layer
    writes those messages for a reader rather than for a log.
    """
    if not isinstance(exc, ValidationFailed):
        return unhandled_exception_handler(request, exc)
    return _problem(status.HTTP_400_BAD_REQUEST, exc.field, exc.message)


def incident_not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    """An unknown incident id: 404, in the same shape as a validation error."""
    if not isinstance(exc, IncidentNotFound):
        return unhandled_exception_handler(request, exc)
    return _problem(
        status.HTTP_404_NOT_FOUND,
        "id",
        f"No incident exists with id '{exc.incident_id}'.",
    )


def product_not_found_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, ProductNotFound):
        return unhandled_exception_handler(request, exc)
    return _problem(status.HTTP_404_NOT_FOUND, "product_id", "Product not found.")


def product_already_exists_handler(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, ProductAlreadyExists):
        return unhandled_exception_handler(request, exc)
    return _problem(
        status.HTTP_409_CONFLICT,
        "sku",
        "A product with this SKU already exists in this warehouse.",
    )


def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last resort: 500 with a fixed message and nothing about the exception.

    The traceback goes to the log, where TrackFlow Tech can read it. The
    response body never names the exception type, the module or the line,
    because any of those tells a caller about the internals.
    """
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path, exc_info=exc)
    return _problem(status.HTTP_500_INTERNAL_SERVER_ERROR, None, GENERIC_SERVER_ERROR)
