# AI Engineering Company Project — Student Template

[![4Geeks Academy](https://img.shields.io/badge/4Geeks-Academy-blue)](https://4geeksacademy.com)
[![AI Engineering](https://img.shields.io/badge/track-AI%20Engineering-green)](https://4geeksacademy.com/es/programas-de-carrera/ingenieria-ia)

_Base template for transversal projects in the AI Engineering Career Program — 4Geeks Academy._

> _Instrucciones disponibles en español en [README.es.md](./README.es.md)._

---

## Purpose

This repository is the **starter template** for transversal projects. You will work on real company scenarios (Brasaland, TrackFlow, Nexova), building deliverables that map to course milestones (Web, Programming, Backend, Telemetry, RAG, Agents, Workflows, Real-time).

- **Create a template** from this repo (do not clone the milestone-specific one).
- Read your **CONTEXT** file for your assigned company — it defines domain data, fields, and constraints.
- Use the **skills/** folder so AI agents can assist you consistently.

---

## Repository structure

```text
ai-engineering-project-template/
├── README.md                 # This file
├── AGENTS.md                 # Index for AI agents: which skills exist and when to use them (you will add it in the future)
├── CONTEXT.md                # Your company context (Brasaland | TrackFlow | Nexova) — add after assignment
├── apps/                     # Your applications (web app, API, dashboards)
├── packages/
│   └── shared-types/         # Shared TypeScript/JSON types used across apps
├── pipelines/
│   └── data/                 # ETL, ingestion, or data pipeline configs/scripts
└── skills/                   # Agent skills (SKILL.md + optional examples, scripts, templates)
    ├── research/
    ├── data-analysis/
    ├── web-scraping/
    ├── code-review/
    └── math-reasoning/
```

---

## How to start

1. **Fork** this repository to your GitHub account.
2. **Clone** your fork (or open in GitHub Codespaces).
3. **Add your CONTEXT**: copy the `CONTEXT-<company>.md` for your assigned company into the root as `CONTEXT.md`.
4. **Read** `AGENTS.md` so you know which skills are available when working with AI.
5. **Build** your milestone deliverables inside `apps/`, reusing `packages/shared-types` and `pipelines/data` as needed.

## TrackFlow development stack

For the Dockerized Next.js and FastAPI development environment, see [Docker Development](./docs/DOCKER-DEV.md). It covers the one-time local `.env` setup, Compose startup, service URLs, and the external candidate-records API dependency.

### Background worker (Celery)

Heavy operations do not run inside the API. The endpoint enqueues them on Redis and answers `202 Accepted` with a `task_id`. A Celery worker, a separate process, runs them, and the client polls `GET /tasks/{task_id}`. Today that covers the technical report (`POST /telemetry/report`). The design, retries, Dead Letter Queue and logs are in [Async Tasks](./docs/ASYNC_TASKS.md).

**Start it.** Redis must be running first. The API and the worker read the same `REDIS_URL`, which defaults to `redis://localhost:6379/0`.

```sh
docker compose up -d redis      # Redis on localhost:6379
npm run worker                  # the worker on the host, reading the root .env
```

Alternatively, `docker compose up worker` runs the worker in Docker, next to `api`, `redis` and `flower`. Run one of the two, never both. They share Redis but read different databases: the host worker uses the `.env` `DATABASE_URL`, and the Compose worker uses the Compose PostgreSQL.

**Check it.** Flower is at http://localhost:5555 after `docker compose up -d flower`, or run `uv run celery -A services.tasks.celery_app:celery_app inspect ping`.

**Stop it.**

- Press `Ctrl+C` once in the worker's terminal. This is a warm shutdown: the worker stops taking new tasks and finishes the ones it is running.
- A second `Ctrl+C` is a cold shutdown. It stops at once, and the interrupted tasks go back to the queue.
- In Docker, `docker compose stop worker` sends the same warm shutdown, with 60 seconds of grace.
- From anywhere, `uv run celery -A services.tasks.celery_app:celery_app control shutdown` stops every worker connected to that Redis.

Tasks enqueued while no worker is running wait in Redis and run when one starts.

---

## Milestones (reference)

| Milestone | Focus        | Typical deliverables                        |
| --------- | ------------ | ------------------------------------------- |
| 0         | Prework      | Environment setup, first prompts            |
| 1         | Web          | Corporate website, forms, SEO               |
| 2         | Programming  | Business logic, scoring, calculations       |
| 3         | AI-driven UI | AI-generated interfaces                     |
| 4         | Next.js      | Portals, loyalty app, operations UI         |
| 5         | Backend      | Central API (locations, menus, sales, etc.) |
| 6         | Telemetry    | Data pipeline, dashboards                   |
| 7         | RAG & Memory | Semantic knowledge base, search             |
| 8         | Agents       | Support, onboarding, training agents        |
| 9         | Workflows    | n8n automations                             |
| 10        | Real-time    | Live dashboards, alerts, streaming          |

---

## Links

- [4Geeks Academy — AI Engineering](https://4geeksacademy.com/es/programas-de-carrera/ingenieria-ia)
- [How to start a coding project](https://4geeks.com/lesson/how-to-start-a-project)

---

## Contributors

This template was built as part of the 4Geeks Academy AI Engineering Career Program by [@marcogonzalo](https://www.linkedin.com/in/marcogonzalo) and [@alezanchezr](https://x.com/alesanchezr) and many other contributors. Find out more about our [AI Engineering Course](https://4geeksacademy.com/en/career-programs/ai-engineering), and [other courses](https://4geeksacademy.com/en/program-comparison).

You can find other templates and resources like this at the [4Geeks Academy GitHub page](https://github.com/4geeksacademy).

_This template is maintained by 4Geeks Academy for the AI Engineering track. For exclusive use in the programme._
