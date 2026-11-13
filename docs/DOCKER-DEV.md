# Docker Development

## First run

Docker Desktop with Compose is required. If the repository's root `.env` does not exist, create it from the safe local template:

```sh
cp docker-compose.env.example .env
```

If `.env` already exists, keep it and add the Compose variables from `docker-compose.env.example`. The sample values are for local development only. Do not put production credentials in the sample or commit `.env`.

The `NEXT_PUBLIC_API_URL` value is the browser-reachable URL of the separate candidate-records API. The sample assumes that API is running on the host at `http://localhost:4000`.

`SUPABASE_URL` and `SUPABASE_SECRET_KEY` have no local sample: telemetry events are stored in a Supabase project's `telemetry_events` table, not in the Compose PostgreSQL, so they need a real project's URL and secret key. Compose refuses to start the API without them, and events from a Docker session land in that project.

Start the development stack from the repository root:

```sh
docker compose up --build
```

## Services

| Service | URL | Purpose |
| --- | --- | --- |
| Website | http://localhost:3000 | Next.js public site |
| Backoffice | http://localhost:3001 | Next.js internal console |
| FastAPI | http://localhost:8000/docs | API and OpenAPI docs |
| PostgreSQL | Docker network only | Local inventory database |
| Redis | localhost:6379 | Celery broker and result backend ([Async Tasks](./ASYNC_TASKS.md)) |
| Worker | none | Celery worker running the tasks the API enqueues |
| Flower | http://localhost:5555 | Queue monitoring: queued, running and finished tasks |
| Qdrant | http://localhost:6333/dashboard | Vector store for the knowledge base (`trackflow_knowledge` collection, RAG) |

Both Next.js apps run in the single `ui` container. Their `/backend/...` requests are rewritten server-side to the `api` service; browser code does not need to resolve Docker service names. The website's candidate-records API remains external and uses the browser-reachable `NEXT_PUBLIC_API_URL` from `.env`.

The named `trackflow-development` network provides service-name DNS. PostgreSQL data, TinyDB data and Redis's append-only file (queued tasks) survive container recreation in named volumes. Source directories are bind-mounted for reload; dependency and Next.js build directories use separate volumes so host mounts do not hide container-installed files.

The API's local PostgreSQL URL uses `sslmode=disable` only with `APP_ENV=development`. Other environments continue to require PostgreSQL SSL. The local sample JWT secret and database password are placeholders for this development stack, not production credentials.

Redis and Flower are published on `127.0.0.1` only: neither has a password. The `worker` builds from the same image as `api` and reads the same settings; `REDIS_URL` inside Compose is `redis://redis:6379/0` (override with `COMPOSE_REDIS_URL`). Run either this worker or `npm run worker` on the host, not both: they share Redis but use different databases.

Qdrant is also published on `127.0.0.1` only (6333 REST, 6334 gRPC) and has no API key. Its image is pinned to `qdrant/qdrant:v1.20.0`, the same minor as `qdrant-client` in `uv.lock`; bump both together. Collections persist in the `qdrant_data` volume. Inside Compose `QDRANT_URL` is `http://qdrant:6333` (override with `COMPOSE_QDRANT_URL`); on the host it is `http://localhost:6333`. The API starts without waiting for Qdrant to be healthy, because only the knowledge endpoint uses it, per request. To run only the vector store: `docker compose up -d qdrant`.

The knowledge assistant's model settings (`EMBEDDING_MODEL`, `GENERATION_MODEL`, `LLM_BASE_URL`, `LLM_API_KEY`, and the optional `EMBEDDING_BASE_URL` and `EMBEDDING_API_KEY`) are passed through from the root `.env` to `api` and `worker`. While they are empty, the API still starts and `POST /knowledge/query` answers 503. See [RAG design](./rag/rag-design.md).

Stop the containers with Ctrl+C. `docker compose down` preserves named data volumes; removing volumes deletes local database and API data.