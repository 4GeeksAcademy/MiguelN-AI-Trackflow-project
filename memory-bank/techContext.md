# Technical Context

## Tech Stack (Website)

This repository currently contains two website implementations related to TrackFlow:

- `apps/website`: static marketing pages served by Flask.
- `uis/website`: interactive hiring tracker built with Next.js.

### Frontend

- `uis/website`
  - Next.js `16.2.7` (App Router structure under `app/`)
  - React `19.2.4`
  - TypeScript `^5`
  - Tailwind CSS `^4` via PostCSS plugin (`@tailwindcss/postcss`)
  - `next/font` with Plus Jakarta Sans and Space Grotesk for branding consistency
- `apps/website`
  - HTML + vanilla JavaScript
  - Tailwind via CDN (`https://cdn.tailwindcss.com`)
  - No build step required

### Backend

- Python Flask app (`server.py`) used as a lightweight web server for static delivery:
  - Serves `apps/website/index.html` at `/`
  - Serves static files from `apps/website` and fallback static files from repository root
- The Next.js UI is configured to consume an external REST API and does not define its own API routes in this repo.

### Database

- No database implementation is present in the repository.
- Data persistence is expected to be handled by the external API behind `NEXT_PUBLIC_API_URL`.

### APIs / Integrations

- External REST API integration in `uis/website` through `NEXT_PUBLIC_API_URL`.
- Current consumed endpoints:
  - `GET /records`
  - `GET /records/:id`
  - `POST /records`
  - `PATCH /records/:id`
  - `PUT /records/:id`
  - `GET /records/:id/notes`
  - `POST /records/:id/notes`
  - `DELETE /records/:id/notes/:noteId`
- Branding assets and web fonts are loaded from external sources (Google Fonts and social metadata targets).

### Language / Type System

- TypeScript in the Next.js tracker app with `strict: true` enabled in `tsconfig.json`.
- JavaScript (vanilla) for `apps/website/signup.js` form behavior.
- Python for Flask static server (`server.py`).

## Architectural Decisions Made

1. Monorepo-style separation by concern.
	- Distinct folders for apps, UIs, shared packages, agents, and skills.
2. Split website strategy.
  - Keep a static marketing site (`apps/website`) separate from the product UI (`uis/website`).
3. Backend decoupling for the tracker.
	- The Next.js tracker delegates data operations to an external API service via environment variable instead of coupling to local API routes.
4. Service-layer API access in the tracker.
	- Dedicated modules (`services/candidates-service.ts`, `services/notes-service.ts`) encapsulate HTTP calls.
5. Payload normalization boundary.
	- Candidate and note payloads are normalized in `lib/` utilities to absorb schema variants (for example `fullName` vs `name`, `status` vs `currentStatus`).
6. Client-rendered interaction model for tracker screens.
	- Main pages use `"use client"` and browser-side state management for filtering, forms, and optimistic-ish refresh behavior.
7. Shared visual identity through design tokens.
	- CSS custom properties and brand fonts in `globals.css` and `layout.tsx` define consistent TrackFlow theming.

## Technical Constraints

1. `NEXT_PUBLIC_API_URL` is mandatory for tracker API communication.
	- Missing configuration causes runtime errors in API client initialization.
2. External API contract dependency.
	- UI behavior depends on `/records` and `/notes` endpoints and their HTTP semantics (status codes, JSON content types).
3. No in-repo backend/data source for the tracker.
	- Local development of full tracker workflows requires a separately running API service.
4. Mixed frontend paradigms increase maintenance overhead.
	- Static HTML/JS pages and Next.js/TypeScript app coexist with different tooling and conventions.
5. Schema inconsistency handling is required.
	- Multiple possible field names for candidate data imply unstable upstream payload shapes.
6. Limited server-side rendering usage for data-heavy tracker pages.
	- Current client-side fetching model can impact first-load latency and SEO relevance for authenticated app content.
7. Root Flask dependency version appears non-standard.
	- Root `package.json` declares `flask` under npm dependencies, while runtime uses Python `flask` in `server.py`; setup consistency depends on developers understanding this split.
