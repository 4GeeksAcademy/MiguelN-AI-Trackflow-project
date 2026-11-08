# Frontend Performance Audit Report

Audit date: 2026-10-10

## Executive Summary

The production-build audit found no repeatable page-loading bottleneck that justified performance-driven JavaScript removal, architectural changes, or page restructuring. All four targets had median Lighthouse Performance 100 before and after correction. The work therefore focused on measurable accessibility and browser best-practice defects while preserving routes, content, layout, and interactions.

After correction, Lighthouse Accessibility is 100 on both corporate homepage modes and the candidate pipeline; Backoffice inventory Best Practices is 100. Candidate filtering behavior is unchanged, and the backoffice now serves the existing TrackFlow favicon. No field Core Web Vitals improvement is claimed: CrUX data is unavailable for localhost.

## Before / After

Scores are medians of three Lighthouse runs. FCP and LCP are medians in milliseconds; TBT is total blocking time; CLS is cumulative layout shift.

| Target | Mode | Performance | Accessibility | Best Practices | SEO | FCP | LCP | TBT | CLS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Corporate homepage | Desktop, before | 100 | 96 | 100 | 100 | 205 | 579 | 0 | 0.00 |
| Corporate homepage | Desktop, after | 100 | 100 | 100 | 100 | 205 | 546 | 0 | 0.00 |
| Corporate homepage | Mobile, before | 100 | 96 | 100 | 100 | 1,508 | 1,508 | 24 | 0.00 |
| Corporate homepage | Mobile, after | 100 | 100 | 100 | 100 | 1,508 | 1,508 | 25 | 0.00 |
| Candidate pipeline | Desktop, before | 100 | 92 | 100 | 100 | 205 | 700 | 0 | 0.00 |
| Candidate pipeline | Desktop, after | 100 | 100 | 100 | 100 | 205 | 699 | 0 | 0.00 |
| Inventory products | Desktop, before | 100 | 100 | 96 | 100 | 203 | 692 | 0 | 0.00 |
| Inventory products | Desktop, after | 100 | 100 | 100 | 100 | 204 | 677 | 0 | 0.00 |

### Timing Interpretation

Performance scores remained at 100 for every target and phase. Desktop FCP/LCP movements are small (generally under 33 ms) and are not treated as a meaningful speed improvement. Corporate mobile median LCP is unchanged. The baseline mobile LCP ranged from 1.5 to 2.6 seconds, while final runs ranged from 1.508 to 1.509 seconds; this is a narrower observed range, not proof of an underlying product change. No site-wide or field-user speed claim is warranted from these local synthetic samples.

Lighthouse reported unused/legacy JavaScript on the Next.js runtime, but with Performance already at 100 and no stable slow interaction or loading bottleneck, removing framework code would be speculative and risks behavior. Compression findings on data pages came from the uncompressed temporary fixture API, not the application's deployment path, and were excluded.

## Corrections Applied

- **Corporate text contrast:** changed the muted text token and two local homepage text colors based on measured contrast. Original navigation text was 2.36:1; adjusted values meet or exceed 4.5:1. Lighthouse contrast failures fell to zero and homepage Accessibility rose from 96 to 100. No content, component hierarchy, or page structure changed. Commit `ee5eeaf`.
- **Brand-link accessible names:** removed overriding `aria-label` values from visible brand links in both apps. Their accessible names now include the rendered labels. Lighthouse reports no label-content/name mismatch on audited routes. Commit `9bc92ef`.
- **Candidate filters:** extracted the repeated status/stage select markup into a small local `CandidateFilter` and supplied hidden semantic labels. Browser verification confirmed both controls remain discoverable by accessible name and continue to update their existing query parameters. The select-name audit now has zero failures. Candidate Accessibility rose from 92 to 96 at this step. Commit `3fa8dd8`.
- **Candidate text contrast:** adjusted the candidate eyebrow and motto colors to measured values above the required contrast threshold. Candidate Accessibility rose from 96 to 100 with zero remaining contrast failures. Commit `298c604`.
- **Backoffice favicon:** added the existing TrackFlow favicon at the backoffice's root `app/favicon.ico` convention. `/favicon.ico` responds 200; Lighthouse console errors cleared and Best Practices rose from 96 to 100. Commit `6acc68b`.

## Code Analysis and Refactoring Decision

The audit identified three duplication candidates in [AUDIT.md](AUDIT.md): identical `StateMessage` components in both applications; nearly identical session-provider flows with app-specific API clients; and the repeated status/stage filter controls in the candidate page. The filter controls were extracted locally because they shared one rendering contract and were directly implicated in a measured accessibility issue. The cross-app candidates are documented but deferred: extracting them would require a shared-package/auth-client boundary change, which is broader than this targeted audit and has no demonstrated Lighthouse performance benefit.

The `core-web-vitals` and `performance` skills were installed for GitHub Copilot and reviewed before corrections. Their measurement-before-change and equivalent-rerun guidance was followed. Cloudflare's `web-perf` skill was not installed: no Cloudflare deployment was established, and that skill requires Chrome DevTools MCP, which was unavailable in this session. Lighthouse CLI was the documented lab fallback.

## Method and Limitations

- Lighthouse 12.8.2, Chrome 154.0.8037.95; production `next build`/`next start` outputs.
- Categories: Performance, Accessibility, Best Practices, SEO. Same URLs and presets before and after; three runs per target/mode; category medians reported.
- Targets: `http://localhost:3100/` desktop and mobile; `http://localhost:3100/candidates` desktop; `http://localhost:3101/backoffice/inventory/products` desktop.
- Protected routes used an isolated Chrome profile, synthetic token, and temporary in-memory API with 40 fictional rows. The API returned no production/account data and is not part of the repository. Candidate and inventory pages were confirmed rendered before measurement.
- Lighthouse's default device/network/CPU simulation was retained in both rounds. Machine load, external font/network conditions, caches, and Lighthouse simulation can affect lab timings.
- CrUX/field data is unavailable for localhost. The report does not represent real-user p75 or prove a production deployment outcome.

## Verification

- Production builds passed for both Next.js apps.
- Website lint with auto-fix, typecheck, and Jest passed: 171 tests.
- Backoffice lint with auto-fix, typecheck, and Jest passed: 214 tests.
- Browser checks verified the two candidate filter accessible names and query behavior, and confirmed both protected audit pages rendered synthetic data.
- Final Lighthouse runs report 100 for all four categories on all audited targets. Desktop/mobile report screenshots and raw HTML/JSON reports are stored under `audit/before/` and `audit/after/`.

## Artifact Index

- [Baseline analysis and root causes](AUDIT.md)
- `audit/before/`: canonical baseline HTML/JSON triplicates and four report screenshots.
- `audit/after/`: canonical `*-final-01` through `*-final-03` HTML/JSON triplicates and four report screenshots.
- The screenshots show Lighthouse report views; the paired JSON files are the machine-readable source for scores and metrics.
