"""TrackFlow API composition root.

Mounts every domain router behind one FastAPI app. Authentication is stateless
JWT only: no sessions, no auth cookies.
"""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from services.auth.router import router as auth_router
from services.core.config import get_settings
from services.core.errors import (
    IncidentNotFound,
    ProductAlreadyExists,
    ProductNotFound,
    ValidationFailed,
)
from services.core.http_errors import (
    incident_not_found_handler,
    make_validation_error_handler,
    product_already_exists_handler,
    product_not_found_handler,
    unhandled_exception_handler,
    validation_failed_handler,
)
from services.incidents.router import router as incidents_router
from services.inventory import database as inventory_database
from services.inventory.routers.inventory import router as inventory_router
from services.profiles.router import router as profiles_router
from services.reporting.router import router as reporting_router
from services.suppliers.router import router as suppliers_router
from services.tasks.router import router as tasks_router
from services.telemetry import database as telemetry_database
from services.telemetry.router import router as telemetry_router
from services.users.router import router as users_router

logger = logging.getLogger("api.timing")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Load the settings before the first request is served.

    A missing or malformed setting then stops startup with a message naming
    it, instead of surfacing as a 500 on whichever request first needs it.
    """
    get_settings()
    try:
        inventory_database.get_identity_db()
        inventory_database.check_connection()
        inventory_database.initialize_schema()
        telemetry_database.initialize_schema()
        yield
    finally:
        inventory_database.dispose_engine()


app = FastAPI(
    lifespan=lifespan,
    title="TrackFlow API",
    description=(
        "Supplier directory, identity and incident services for TrackFlow "
        "Los Angeles and Zaragoza operations. Protected routes expect an "
        "`Authorization: Bearer <token>` header; get a token from "
        "`POST /auth/login`."
    ),
    version="0.3.0",
)


@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    start = time.perf_counter()
    status_code = 500

    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        route_path = getattr(route, "path", "<unmatched>")
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "%s %s -> %s | %.1fms",
            request.method,
            route_path,
            status_code,
            duration_ms,
        )


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:5500",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(profiles_router)
app.include_router(suppliers_router)
app.include_router(incidents_router)
app.include_router(inventory_router)
app.include_router(telemetry_router)
app.include_router(reporting_router)
app.include_router(tasks_router)

# Error translation.
#
# The `RequestValidationError` remapping - FastAPI's 422-and-a-pydantic-array
# into a 400 naming one field in plain language - applies only under the
# incident router's own prefix, which is read off the router so the two cannot
# drift. Every other domain keeps the 422 its clients were built against; see
# `make_validation_error_handler`.
#
# The domain-error handlers are scoped by their exception type, and the
# `Exception` handler is the app-wide backstop that keeps a traceback from ever
# reaching a client.
app.add_exception_handler(
    RequestValidationError,
    make_validation_error_handler((incidents_router.prefix,)),
)
app.add_exception_handler(ValidationFailed, validation_failed_handler)
app.add_exception_handler(IncidentNotFound, incident_not_found_handler)
app.add_exception_handler(ProductNotFound, product_not_found_handler)
app.add_exception_handler(ProductAlreadyExists, product_already_exists_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)
