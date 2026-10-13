# Definition-of-Done audit (end of Phase 6)

Date: 2026-10-13. Everything below was run on the authoring machine (Windows 11, Python 3.12.7, Node 24, PostgreSQL 18.1 started as a
throwaway local cluster on port 55432 with trust auth, deleted afterwards). **Not run anywhere:** Docker builds, `docker compose config`,
hadolint, GitHub Actions, Python 3.11, a real Redis backend/RQ worker, any live third-party API. Numbers are copied from the actual runs.

## (a) All tests pass

| Check | Command | Result |
|---|---|---|
| Server, SQLite | `cd backend; python -m pytest -q` | **819 passed, 5 skipped** (the 5 skipped are PostgreSQL-only tests, skipped without `TEST_DATABASE_URL`). Phase 5 had 768; Phase 6 added 56 server tests. |
| Server, real PostgreSQL 18.1 | `TEST_DATABASE_URL=postgresql+psycopg://...; python -m pytest -q` | **824 passed, 0 skipped** in 281 s (same suite, every in-memory test engine became its own schema; the 5 PostgreSQL-only tests run too) |
| Lint, server | `python -m ruff check .` | All checks passed |
| Frontend unit | `npm test` (Vitest) | 62 passed in 7 files (3 new for the runtime API URL) |
| Frontend lint / types / build | `npm run lint`, `npm run build` | oxlint: 0 errors (warnings from earlier phases remain, exit 0); `tsc -b` + Vite build OK |
| Browser e2e | `npm run test:e2e` (real API on SQLite, sample collector, Chromium) | 14 passed (13 smoke + 1 screenshot capture), 58 s |
| Canary | `python -m scripts.canary` | PASS, 7 of 7 checks (~0.3 s) |
| API entrypoint on PostgreSQL | `sh docker-entrypoint.sh api` with PostgreSQL schema, then `/health`, `/health/live`, login, `/api/health/details`, `POST /api/health/canary` | all as expected (`ok`, migration `0004`, canary `passed: true`) |
| Concurrent migrations | 2 processes via `scripts.migrate` and a 3-thread test on PostgreSQL | one migrated, the rest waited and no-op'd |
| YAML syntax | python `yaml.safe_load` on `docker-compose.yml`, `docker-compose.dev.yml`, `.github/workflows/ci.yml` | parse OK; services/jobs and `depends_on` targets exist |

**Not run / unverified:** `docker build` of either image, `docker compose config` and `up` (no Docker), hadolint, nginx config syntax
(`nginx -t`), the nginx template/envsubst flow (the runtime-config script itself was run in a POSIX shell and rejects bad `API_URL`
values), the GitHub workflows (cannot run locally), Python 3.11 (CI matrix runs it), PostgreSQL 17 (the compose/CI version; local was 18.1),
real Redis/RQ (`check_worker` and `check_redis` are tested against fakes only), Sentry with the real SDK (fake module only).

## (b) The 10 required name cases (brand "Acme Bank")

`backend/tests/core/engine/test_name_match.py`: all 10 pass.

| Candidate | Expected | Test id |
|---|---|---|
| acrnebank | flagged | `test_required_flagged[acrnebank]` |
| Acme Bank Support | flagged | `test_required_flagged[Acme Bank Support]` |
| acme-bank | flagged | `test_required_flagged[acme-bank]` |
| A C M E Bank | flagged | `test_required_flagged[A C M E Bank]` |
| acm3bank | flagged | `test_required_flagged[acm3bank]` |
| Acme Bnak | flagged | `test_required_flagged[Acme Bnak]` |
| acmebank_official | flagged | `test_required_flagged[acmebank_official]` |
| Acme Bakery | not flagged | `test_required_not_flagged[Acme Bakery]` |
| Acme Hotels | not flagged | `test_required_not_flagged[Acme Hotels]` |
| Acme Weather | not flagged | `test_required_not_flagged[Acme Weather]` |

The same 12 cases also run end-to-end through the Look-alike runner (`tests/api/test_screens_api.py`, `DEFAULT_CASES` in `core/pipeline/lookalike.py`).

## (c) Official assets never appear in any detection list

- `tests/core/pipeline/test_scan_flow.py::test_official_assets_never_listed_and_are_excluded` (detections with every filter, marked_official, false-positives, dashboard rows, excluded list)
- `tests/core/pipeline/test_scan_pipeline.py::test_allowlist_is_exact_not_by_name`, `::test_adding_official_account_closes_existing_detection`
- `tests/core/engine/test_scoring_pipeline.py::test_allowlist_official_assets_never_produce_a_result`
- `tests/core/collectors/test_base_sample.py::test_sample_engine_proof_official_excluded_lookalikes_flagged_negatives_not`
- New in Phase 6: `tests/services/test_canary.py::test_full_sample_scan_finds_canary_lookalike_and_excludes_canary_official` and the canary's `official_assets_excluded` check, which fails if the allowlist regresses (`test_canary_fails_when_allowlist_regresses`).

## (d) A failed source never breaks a scan

- `tests/core/pipeline/test_scan_pipeline.py::test_failed_source_gives_partial_scan_and_keeps_other_results`
- `tests/core/pipeline/test_scan_pipeline.py::test_every_source_unavailable_is_failed_with_clear_message` (the only case that ends `failed`: nothing at all to analyse, with a clear message)
- `tests/core/collectors/test_selection_orchestrator.py::test_failing_collector_does_not_stop_the_others_and_status_is_recorded`
- `tests/core/collectors/test_selection_orchestrator.py::test_cached_results_served_with_age_when_source_goes_down_and_backoff_applies`
- `tests/core/pipeline/test_scan_pipeline.py::test_scan_crash_is_recorded_as_failed_without_secrets`
- Phase 6: a failing collector state degrades `/health` but never makes it `down` (`tests/api/test_health_readiness.py::test_collectors_live_with_key_ok_then_stored_failure_degrades_without_leaks`, `::test_all_live_collectors_failing_still_not_down`).

## (e) Frontend matches the PDF design: PARTIALLY VERIFIED

What exists: Phase 5 kept the layout, classes, copy and CSS of the 10 designed pages and only rewired data. `frontend/e2e/visual.spec.ts` captures
a 1440 px screenshot of every page (`e2e/screenshots/`, git-ignored, regenerated by `npm run test:e2e`; re-run in this phase: 11 pages captured).
**The comparison with `ScamMind AI Screens.pdf` is a manual, by-eye step; there is no automated pixel diff, and this audit did not repeat it.** Phase 6
changed no UI (only an extra `<script src="/config.js">` and the API-URL lookup).

Known differences (from `docs/FRONTEND_CONTRACT.md` section 17, still true):
- Wording changed on purpose: "PDF report" is "Printable report" (no PDF library); "Daily digest" is "Weekly digest", rows say "(not delivered yet)"; Source Health back-off text comes from the API; "Description overlap NN%" shows "+N points"; App Store detail gained an "Evidence kept" block; the logo copy says PNG/JPEG/WebP (server refuses SVG).
- Not changed from the old code and different from the PDF: KPI card icons, hero glow, the score ring/bars under "Why it scored", default "Google Play" tab on the Apps page.
- Inert controls: sidebar search, bell, Support alert, brand switcher; "Forgot password" and "Import from store listing URL" only show an honest message.
- Added screens/controls not in the PDF: `/evaluation`, the Data mode switch, "Sample data" badge, credit meter, loading/empty/error states.
- `<title>` in `frontend/index.html` contains a mojibake dash (cosmetic, pre-existing).

## (f) Docs present

`docs/FRONTEND_CONTRACT.md`, `docs/SPEC_FROM_REFERENCE.md`, `docs/API.md` (section 19 added), `docs/DEPENDENCIES.md` (Phase 6 section added): all present. Also `COLLECTORS.md`, `DECISIONS.md` (Phase 6 section added) and this file.

## (g) No code, names or structure copied from the reference ZIP: SATISFIED BY PROCESS, NOT BY DIFF

The ZIP is not available to this audit, so no diff or similarity scan was possible, and grepping for "reference-style identifiers" is not meaningful
without it. The evidence that exists is about how the work was done, not a measurement:
- `docs/SPEC_FROM_REFERENCE.md` is a specification written in our own words; the build was driven from that spec and the design PDF, not from reference source.
- The detection engine (normalisation, edit distance, name matching, scoring, logo similarity), collectors, pipeline and health/canary code were written independently in this repository; every algorithm has its own tests and decisions are recorded in `docs/DECISIONS.md`.
- Package choices and licenses are recorded in `docs/DEPENDENCIES.md`; third-party code enters only through declared dependencies.
- Phase 6 code (health, canary, Sentry hook, migration 0004, Docker, CI, README) was written from the task text alone.
If a hard guarantee is required, someone who has the ZIP should run a similarity tool (for example a token-based clone detector) against `backend/` and `frontend/src/`; that has NOT been done.

## Items not satisfied or open

1. Docker images, compose file, nginx template, GitHub workflows: written, **never run** (see (a)).
2. Frontend-to-PDF match is manual and was not re-done in this phase (e).
3. Live mode (Tavily key, real iTunes/Google Play calls, logo downloads) has never been exercised; run `scripts.live_check` yourself.
4. Redis/RQ worker never run against a real Redis server; `/health` worker and redis checks tested with fakes.
5. Python 3.11 and PostgreSQL 17 not run locally (CI will).
6. Login rate limiter is in memory per process; the evaluation cache is per process; one brand per organisation; no PDF export; notifications not delivered.
7. Redis image licensing (7.2 pinned for BSD licensing; 7.4+/8.x licensing is from memory, unverified) and `psycopg` LGPL-3.0 need a legal look (`docs/DEPENDENCIES.md`).
8. Reference-ZIP similarity: process evidence only, no diff (g).


---

# DigitalTrust extension: definition-of-done audit (final pass, 2026-10-15)

Run on the authoring machine (Windows, SQLite only; no live network, no user keys).

| Check | Result |
|---|---|
| Server `python -m pytest -q` | 1010 passed, 5 skipped (PostgreSQL-only; PostgreSQL not run in this pass). The suite ran twice: first run 3 failures in `test_synthetic_evaluate.py` caused by the new fan-page / authorized-reseller negatives, fixed by updating those tests (see below); re-run of that file green, rest of the suite unchanged |
| `python -m ruff check .` | All checks passed |
| Frontend `tsc --noEmit`, `npm run build` | OK |
| Frontend `npm test` (Vitest) | 68 passed (2 new for Investigations) |
| `npm run test:e2e` | 14 passed (nav labels and one chip selector updated) |

## Met
- Risk engine v1/v2 with severity, per-signal present / clean / unavailable and provenance (E2), shown in the detail panel.
- Web category (look-alike domains, optional Safe Browsing), brand registry v2 (domains, logos, publishers, description, verification), manual entry and CSV/JSON import with dry run and per-row errors (E7), now with UI.
- Investigations workflow with `resolution` (migration 0007), audited, persisted, filterable.
- Settings shows provider status (never keys) and Safe Browsing configured yes/no; admin can choose the text provider.
- Evaluation: confusion matrix, FPR, per-group and per-category numbers, dataset name/version/size, algorithm version, SYNTHETIC banner, hard negatives (fan pages, authorized resellers, unrelated).
- Security: general rate limiter (in memory), restrictive CORS (tested), `docs/SECURITY.md`.

## Not met or caveats
- **Authorized resellers are flagged** by the name matcher (15 of 15 synthetic cases; FPR 3.5% overall). Only the allowlist clears them. The old test asserted precision 1.0; it now asserts only authorized-reseller names may be false positives.
- ML classifier: not built (deferred until labelled analyst data exists). Evaluation numbers are synthetic and optimistic; there is no real-world precision/recall.
- Rate limiter is per process and in memory. No tenant isolation (single tenant), no SSO/MFA.
- Not exercised with real keys: Gemini, Hugging Face, Google Safe Browsing, Tavily. Not run: PostgreSQL this pass (the 0007 migration is a plain nullable column), Docker, CI.
- No visual comparison against the PDF was redone; new pages reuse existing styles and were only smoke-tested through unit tests and the e2e walk (which does not open `/web` or `/investigations`).
- Web Threats and Investigations have no dedicated e2e test; Investigations has Vitest coverage, the resolution API has pytest coverage.
