# Backend Architecture Proposal

## Recommended Pattern

TrackFlow should use a **domain-first modular monolith with layered modules inside each domain**.

This suits the company because it operates in Mexico and Spain, has two independent warehouse systems, integrates with eight carriers, and is maintained by a seven-person technology team. A modular monolith provides clear ownership and boundaries without the deployment, monitoring, and coordination overhead of microservices.

The initial backend is currently Flask. The target architecture should be implemented as a deliberate migration to FastAPI, not as an assumption that FastAPI already exists.

## Domain-First Structure

The backend should be organised by business domain, not by technical file type. Each domain owns its API routes, request and response schemas, persistence models, and business services.

```text
app/
  main.py
  core/
    config.py
    dependencies.py
    errors.py
    logging.py
  domains/
    customers/
      router.py
      schemas.py
      models.py
      service.py
      repository.py
    warehouses/
      router.py
      schemas.py
      models.py
      service.py
      repository.py
      integrations/
    shipments/
      router.py
      schemas.py
      models.py
      service.py
      repository.py
    carriers/
      router.py
      schemas.py
      models.py
      service.py
      repository.py
      integrations/
    tracking/
      router.py
      schemas.py
      service.py
    reverse_logistics/
      router.py
      schemas.py
      models.py
      service.py
      repository.py
  workflows/
    carrier_assignment.py
    return_collection.py
  tests/
```

### Layer Responsibilities

- `router.py`: HTTP endpoints only. Validate requests, call services, and return responses.
- `schemas.py`: Pydantic request and response contracts.
- `models.py`: Database entities and domain data structures.
- `service.py`: Business rules and use-case orchestration.
- `repository.py`: Database access behind a domain-specific interface.
- `integrations/`: Adapters for WMS, carrier, ERP, or third-party APIs.
- `core/`: Shared configuration and technical concerns. It must not contain business rules.
- `workflows/`: Use cases that coordinate multiple domains.

Avoid generic `utils/` directories. A helper should either belong to its domain or be placed in a clearly named shared technical module.

## FastAPI Router Organisation

FastAPI routers should be grouped by domain and mounted below `/api/v1`:

- `customers_router`
- `warehouses_router`
- `shipments_router`
- `reverse_logistics_router`
- `carriers_router`
- `tracking_router`

Example endpoint groups:

```text
/api/v1/customers
/api/v1/warehouses
/api/v1/shipments
/api/v1/returns
/api/v1/carriers
/api/v1/tracking
```

## Frontend and Backend Organisation

- **Repository:** Keep a monorepo. One team owns the frontend, backend, agents, and shared documentation.
- **Communication:** REST over HTTP/JSON, versioned under `/api/v1`.
- **Contracts:** FastAPI Pydantic schemas are the API source of truth. Generate TypeScript types from the OpenAPI specification where possible, or validate both sides with contract tests.
- **Frontend configuration:** Read the API URL from `NEXT_PUBLIC_API_URL`. It is public browser configuration and must never contain secrets.
- **Backend configuration:** Use `pydantic-settings` so invalid or missing environment configuration fails at application startup.
- **CORS:** Use an explicit allowlist for each environment. Allow the local Next.js origin during development and the production UI domain in production. Do not use wildcard origins.

## Cross-Domain Rules

Some use cases span more than one domain. For example, assigning a carrier to a shipment uses shipment data, carrier availability, delivery performance, and country-specific constraints.

This logic should live in a workflow or application-service module, such as `workflows/carrier_assignment.py`, rather than being duplicated in `shipments` or `carriers`.

## Risks and Points of Attention

- **Contract drift:** The frontend currently accepts multiple possible candidate ID fields (`id`, `_id`, `candidateId`, and `candidate_id`). This can hide backend inconsistencies and cause incorrect data to render without a compilation error. Define one canonical API field, such as `id`, and migrate clients to it.
- **Country-specific integrations:** Mexico and Spain should share domain contracts, but their warehouse and carrier adapters must remain independent. Do not force both systems into one implementation.
- **Cross-domain duplication:** Without an explicit workflow layer, multi-domain business rules will be copied into multiple services and eventually diverge.
- **Premature microservices:** Separate services should only be considered after clear scaling, ownership, or deployment needs emerge. The current team size favours a modular monolith.
- **Operational visibility:** Add structured logs, correlation IDs, health checks, and alerts before relying on integrations across both countries.

## Decision Summary

TrackFlow will:

- Keep the existing monorepo.
- Migrate the current Flask backend to FastAPI in planned, incremental stages.
- Build the backend as a domain-first modular monolith with layered modules inside each domain.
- Use FastAPI Pydantic schemas and OpenAPI as the canonical API contract, with TypeScript types generated or validated from that contract.
- Keep country-specific warehouse and carrier integrations behind independent adapters.
- Put multi-domain business processes in explicit workflow modules.
- Add observability, contract tests, and explicit CORS configuration before expanding external integrations.

This direction gives the seven-person technology team a clear structure today while leaving a practical path to extract services later, if operational needs justify it.