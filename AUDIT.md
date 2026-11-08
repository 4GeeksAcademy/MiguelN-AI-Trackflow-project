# Frontend Performance Audit

Audit date: 2026-10-10

## Scope and Method

Targets are the corporate homepage in `uis/website` and representative, authenticated data pages in both frontends: the candidate pipeline and inventory products. All targets were built and served as optimized Next.js production builds. To avoid touching real accounts, records, or inventory, protected views used an isolated Chrome profile with a synthetic bearer token and a temporary in-memory API serving 40 fictional rows. The API and profile are not repository dependencies or deliverables.

Lighthouse 12.8.2 ran in Chrome 154.0.8037.95 with Performance, Accessibility, Best Practices, and SEO categories enabled. Each target/mode has three runs; reported scores below are category medians. Corporate home was measured on desktop and mobile; candidate pipeline and inventory products were measured on desktop. Raw JSON and HTML reports are in `audit/before/`. The desktop corporate homepage report screenshot is `audit/before/corporate-home-desktop-report.png`.

These are local lab measurements, not field data. Machine load, external font/network availability, and Lighthouse simulation affect timings. The corporate mobile LCP varied from about 1.5 s to 2.6 s, so the median is reported with that spread called out. The temporary mock API does not enable production response compression: its compression-related Lighthouse findings are excluded from application conclusions. No production service or account data was used.

## Baseline Results

| Target | Mode | Runs | Performance | Accessibility | Best Practices | SEO | Median FCP | Median LCP | Median TBT | Median CLS |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Corporate homepage | Desktop | 3 | 100 | 96 | 100 | 100 | 205 ms | 579 ms | 0 ms | 0.00 |
| Corporate homepage | Mobile | 3 | 100 | 96 | 100 | 100 | 1,508 ms | 1,508 ms | 24 ms | 0.00 |
| Candidate pipeline | Desktop | 3 | 100 | 92 | 100 | 100 | 205 ms | 700 ms | 0 ms | 0.00 |
| Inventory products | Desktop | 3 | 100 | 100 | 96 | 100 | 203 ms | 692 ms | 0 ms | 0.00 |

All scores are Lighthouse category scores on a 0-100 scale. Individual mobile homepage performance scores were 100, 93, and 100; LCP was approximately 1.5 s, 2.6 s, and 1.5 s respectively. The performance score median alone therefore hides material run-to-run variation.

## Findings and Root Causes

### Accessibility

1. **Muted navigation text has insufficient contrast.** Lighthouse measured `#82a2e5` on `#f2f7ff` at 2.36:1, below 4.5:1 for normal text. This affects the corporate header subtitle and muted navigation links, and appears on the home and candidate routes. Root cause is the shared `--text-muted` value in `uis/website/app/globals.css`.
2. **The brand link's accessible name omits its visible text.** The corporate link's `aria-label="TrackFlow corporate home"` replaces its visible “TrackFlow” text, triggering Lighthouse's label-content-name mismatch audit. The backoffice equivalent, `aria-label="TrackFlow backoffice dashboard"`, has the same problem. Root cause is redundant overriding labels on visible-text links in `uis/website/components/common/TrackFlowNav.tsx` and `uis/backoffice/components/common/BackofficeNavShell.tsx`.
3. **Candidate status and stage filters lack accessible names.** Lighthouse flags both `<select>` controls in `uis/website/components/candidates/CandidatesListPage.tsx`. Their first option is visible but is not a programmatic label. Root cause is that the controls have neither associated `<label>` elements nor `aria-label`/`aria-labelledby` attributes.

### Best Practices

4. **Backoffice reports a browser console error.** Lighthouse records a failed request with HTTP 404 on the inventory products page. Direct checks confirm `/favicon.ico` is missing in `uis/backoffice`; the site has no `public` directory or favicon route. Root cause is the absent app favicon. Add a focused app icon asset/route rather than changing the page or shared navigation.

### Performance Interpretation

5. **No stable, material page-performance bottleneck was identified in the measured routes.** Desktop medians have FCP around 203-205 ms, TBT and CLS at zero, and LCP below 700 ms except the inventory page at 692 ms. Lighthouse reports approximately 26-30 KiB of unused JavaScript and 13-14 KiB of legacy JavaScript on these builds, but the route-level performance medians are already 100 (inventory 100 in two runs and 99 in one). Do not remove framework/runtime code speculatively; re-evaluate only if a narrowly scoped change can demonstrate a repeatable improvement without changing behavior.
6. **Compression findings on data pages are fixture artifacts.** The synthetic API returns uncompressed JSON and has no production middleware or proxy. The reported 4-6 KiB savings are not evidence about the deployed API and are excluded from the fixes.

## Duplication and Refactor Candidates

1. **State-message component is duplicated verbatim.** `uis/website/components/common/StateMessage.tsx` and `uis/backoffice/components/common/StateMessage.tsx` have identical props, tone mapping, and markup. A small shared UI package component could keep error/info/success presentation consistent. This is a maintainability opportunity, not a measured performance issue; moving it across package boundaries is not justified for this audit unless a suitable shared package boundary already exists.
2. **Session provider logic is duplicated.** `uis/website/components/auth/AuthProvider.tsx` and `uis/backoffice/components/auth/AuthProvider.tsx` implement the same token check, redirect, retry, profile update, and sign-out state flow, with app-specific API error imports. A shared provider with injected client operations could reduce drift, but would widen the change and is not justified by Lighthouse evidence. Defer extraction rather than restructure authentication during a targeted performance audit.
3. **Candidate filters repeat the same control structure.** The status and stage `<select>` blocks in `CandidatesListPage.tsx` share the same styling and query-update pattern. A small local, typed filter-select component or a data-driven rendering helper is a contained extraction. It can also carry the missing accessible label as an explicit prop; this is the recommended narrow refactor to make alongside the measured candidate accessibility correction.

## Correction Priorities

1. Fix contrast and accessible-name issues with targeted token/attribute changes, preserving visible navigation and page structure.
2. Give the two candidate filters explicit accessible names; use a small local shared filter control if it keeps markup simpler.
3. Add the missing backoffice favicon so the route no longer emits a console 404.
4. Do not pursue speculative JavaScript or API-compression changes while measured performance is already strong and the API is represented by a test fixture.

## Artifact Index

- `audit/before/`: three Lighthouse HTML and JSON reports per target/mode, including all four categories; contains one screenshot per measured target/mode.
- `audit/after/`: canonical final `*-final-01` through `*-final-03` report triplicates; contains matching target/mode screenshots.
- [REPORT.md](REPORT.md): final before/after medians, applied corrections, verification, methodology, and residual limitations.
