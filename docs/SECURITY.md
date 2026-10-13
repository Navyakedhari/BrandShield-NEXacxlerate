# Security notes

Scope: what the server does today, what a production deployment must add, and what is NOT implemented. Short on purpose.

## Authentication and roles
- Email + password (bcrypt). Short-lived JWT access token plus a rotating refresh token (a rotated refresh token is never accepted twice). `JWT_SECRET` and the admin password come from the environment; the server refuses to start without them.
- Roles: `viewer` (read), `analyst` (scan, review, edit the registry, import), `admin` (also users, data mode, provider choice, brand archive). Enforced in dependencies (`require_writer`, `require_admin`) on every write route; tests cover 403 for viewers.
- Login failures are limited per IP and email (`LOGIN_MAX_FAILURES`, `LOGIN_WINDOW_SECONDS`).

## Rate limiting
- General limiter on every `/api/*` request: `API_RATE_LIMIT_PER_MINUTE` (default 600, 0 = off), counted per client IP and per bearer token, answered with `429` and `Retry-After`. `/health*` is exempt.
- **In memory, single process.** With several workers or replicas each counts separately and a restart resets it. For production put a reverse-proxy limit (nginx `limit_req`) or a Redis-backed limiter in front. Behind a proxy the client IP is the proxy's unless `FORWARDED_ALLOW_IPS` is set correctly.

## CORS
`CORS_ORIGINS` is an explicit comma-separated list (default `http://localhost:5173`), no wildcard, credentials off (tokens travel in the `Authorization` header, not cookies). A test asserts an unknown origin gets no CORS headers.

## Audit
Every mutating action (status, resolution, registry edits, imports, provider and data-mode changes, logins) writes an append-only `audit_log` row with actor, action, target and detail; detections also keep an event log (the timeline). Passwords and keys are never written. Export: `GET /api/audit/export.csv`.

## Tenant isolation (production requirement, NOT implemented)
The app is single-tenant: one workspace, one active brand, no organisation column on rows. Before serving more than one customer you must add a tenant id to every table, scope every query and every file path (`DATA_DIR`) by it, and test cross-tenant access. Until then run one deployment per customer.

## SSRF and outbound requests
- Candidate URLs (scans, manual entry, imports) are never opened. Domain checks send the URL string to Google Safe Browsing only, and only when a key is set.
- Fetches the app does make (official account/URL checks, image fetch, snapshots) go through `url_safety` / `SafeFetcher`: http(s) only, DNS resolved and private, loopback, link-local and metadata addresses refused, redirects re-checked, size and time caps. Tests run with live network blocked.

## Prompt injection
Candidate text (bios, descriptions) goes to the optional hosted model only as quoted data under a fixed instruction; the reply must match a strict JSON schema; the output can only raise a score by a bounded amount and never triggers an action. It is labelled "an opinion, not evidence". Regression tests are in `tests/core/providers/test_text_providers.py`. This reduces, but cannot remove, the chance that a hostile bio skews an opinion; a person confirms every detection.

## Secrets
Keys (`GEMINI_API_KEY`, `HUGGINGFACE_API_TOKEN`, `GOOGLE_SAFE_BROWSING_API_KEY`, `TAVILY_API_KEY`, `SENTRY_DSN`, `JWT_SECRET`) are read from the environment or the untracked `backend/.env`. They are redacted from logs, never returned by any endpoint (`GET /api/providers` says configured yes/no only) and never stored in the database. Never commit `.env`.

## Uploads and imports
- Images: PNG/JPEG/WebP by magic bytes, size and pixel caps, decompression-bomb guard, re-encoded (metadata dropped), stored under a hash name; the original filename is never used; served with a fixed type and `nosniff`.
- CSV/JSON import: size and row caps (`IMPORT_MAX_BYTES`, `IMPORT_MAX_ROWS`), strict schema (unknown columns refused), UTF-8/UTF-16 only, text cleaned and stored inert (a leading `= + - @` is neutralised so exports cannot become spreadsheet formulas), dry run first, per-row errors.

## Not implemented
Tenant isolation, SSO/MFA, a cross-process rate limiter, token revocation beyond refresh rotation, encryption at rest (use disk or database encryption), malware scanning of uploads, a WAF, automated dependency scanning in CI, an independent security review. Image-hash similarity and the hosted model are hints, not proof.
