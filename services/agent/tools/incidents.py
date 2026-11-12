"""Ticket lookup: reads the incident manager's live data over HTTP.

Read-only by construction: the only request this module makes is
`http.get_json` (GET). Three ways to ask, each one request:

- a helpdesk reference ('482', 'TRF-482', 'TRF-000482'), normalised to
  'TRF-000482' -> `GET /api/incidents?reference=TRF-000482`. An empty list
  means the ticket does not exist;
- any other reference, taken as an incident id -> `GET /api/incidents/{id}`,
  where a 404 means it does not exist;
- no reference, only filters -> `GET /api/incidents?status=...`, a search whose
  answer may be zero tickets.

The incident routes need no credentials (`docs/INCIDENTS.md`), so none are sent.
Any failure - timeout, unreachable, error status, unreadable body - becomes an
`unavailable` result; this function only raises for a programming error.
"""

from typing import Final
from urllib.parse import quote

import httpx
from pydantic import TypeAdapter, ValidationError
from trackflow_shared.incidents import normalize_reference

from services.agent.config import get_agent_settings
from services.agent.tools.contracts import (
    MAX_TICKETS,
    TicketLookupInput,
    TicketLookupResult,
    TicketRecord,
)
from services.agent.tools.http import (
    ServiceFailure,
    Stopwatch,
    client_for_call,
    endpoint_label,
    get_json,
)

INCIDENTS_PATH: Final = "/api/incidents"
_TICKET_LIST: Final = TypeAdapter(list[TicketRecord])


def lookup_ticket(
    request: TicketLookupInput, *, client: httpx.Client | None = None
) -> TicketLookupResult:
    """Look the ticket(s) up in the incident manager. Never raises for a service failure."""
    base_url = get_agent_settings().incidents_api_url
    stopwatch = Stopwatch()
    reference = normalize_reference(request.reference) if request.reference else None
    by_id = request.reference is not None and reference is None

    params: dict[str, str] | None
    if reference is not None:
        path, params = INCIDENTS_PATH, {"reference": reference}
    elif by_id:
        path, params = f"{INCIDENTS_PATH}/{quote(request.reference or '', safe='')}", None
    else:
        path, params = INCIDENTS_PATH, request.filters()
    endpoint = endpoint_label(path, params)

    def result(**fields: object) -> TicketLookupResult:
        return TicketLookupResult.model_validate(
            {
                "request": request,
                "endpoint": endpoint,
                "reference": reference or request.reference,
                "duration_ms": stopwatch.elapsed_ms(),
                **fields,
            }
        )

    try:
        with client_for_call(client) as http:
            reply = get_json(http, f"{base_url}{path}", params=params)
        if reply.status == 404 and by_id:
            return result(outcome="not_found", http_status=404)
        if reply.status != 200:
            raise ServiceFailure(f"http_{reply.status}", reply.status)
        if by_id:
            tickets = [TicketRecord.model_validate(reply.body)]
        else:
            tickets = _TICKET_LIST.validate_python(reply.body)
    except ServiceFailure as failure:
        return result(
            outcome="unavailable", failure=failure.failure, http_status=failure.http_status
        )
    except ValidationError:
        return result(outcome="unavailable", failure="invalid_response", http_status=200)

    if reference is not None and not tickets:
        return result(outcome="not_found", http_status=200)
    return result(outcome="ok", tickets=tickets[:MAX_TICKETS], total=len(tickets), http_status=200)
