# ScamMind AI

Brand-protection workspace. You describe your brand (name, aliases, official social handles, official apps, logo); ScamMind scans public
sources for look-alike social profiles and fake apps, scores every suspect with a reason for every point, excludes your own official assets,
and prepares evidence-backed reports. **A person reviews every detection and approves every report; nothing is ever sent or taken down
automatically.**

* `backend/` FastAPI + SQLAlchemy 2 + Alembic API, detection engine, collectors, RQ worker (this is the product backend)
* `frontend/` React + TypeScript + Vite app (10 screens + evaluation view)
* `docs/` decisions, API contract, collectors, dependencies and licenses, Definition-of-Done audit
* **[User guide](docs/USER_GUIDE.md)**: install, sign in, every page with sample inputs, scoring, credits and troubleshooting (written for non-developers as well as the operator). What is verified on real data and what is not: **[docs/FEATURE_STATUS.md](docs/FEATURE_STATUS.md)**.

## Architecture

```
                       browser
                          |
                 https (your reverse proxy / managed ingress)
                          |
        +-----------------+------------------+
        |                                    |
  frontend (nginx, static SPA)         api (FastAPI / uvicorn)  <-- scheduler loop (ONE process)
  /config.js sets API_URL                    |    \
                                             |     \__ enqueue scan job (RQ)
                                  SQL        |                 |
                          +------------------+          +------v------+
                          |                             |   redis     |
                   +------v------+                      +------+------+
                   | PostgreSQL  |<----- results ------- worker (RQ, same image as api)
                   +-------------+                             |
                                                               v
        DATA_DIR volume (logos, evidence snapshots, reports) shared by api + worker

  worker / api  --->  collectors (fallback chain):  Tavily  ->  SearXNG (optional)  ->  iTunes  ->  Google Play (optional)
                      sample collector (no network) when data mode = sample
  scan = collect -> de-duplicate -> allowlist exclusion -> name match -> logo / text signals -> score -> save (human review)
```

Without Redis the API runs scans in an in-process thread and everything works on one machine with SQLite.

## Run locally without Docker

Needs Python 3.11+ and Node 22+. SQLite is used by default (no database to install).

```powershell
# 1. server  (Windows PowerShell; on Linux/macOS use python3 -m venv .venv && source .venv/bin/activate)
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
copy .env.example .env          # then edit backend/.env (see "First run")
.\.venv\Scripts\python -m scripts.migrate
.\.venv\Scripts\python -m uvicorn app.main:create_app --factory --reload --port 8000

# 2. frontend (second terminal)
cd frontend
npm install
npm run dev                     # http://localhost:5173
```

Sign in with the `ADMIN_EMAIL` / `ADMIN_PASSWORD` from `backend/.env`, open `/setup` to create the brand, then run a scan.
With no live source configured the app is in **sample mode**: detections are fictional, every page shows a "Sample data" badge, and nothing
contacts a real platform. (The server also reads a root `.env` if present; do not put the Docker `.env` values there for a non-Docker run.)

## Run with Docker Compose

Needs Docker with the compose plugin. Services: `api`, `worker`, `db` (PostgreSQL 17), `redis` (7.2), `frontend`.

```bash
cp .env.example .env            # fill in the five REQUIRED values (names only are committed)
python -c "import secrets; print(secrets.token_urlsafe(48))"    # use for JWT_SECRET
python -c "import secrets; print(secrets.token_hex(24))"        # use for POSTGRES_PASSWORD and REDIS_PASSWORD (hex is URL-safe)
docker compose up --build
# API http://localhost:8000/docs   readiness http://localhost:8000/health   frontend http://localhost:5173
```

Compose refuses to start if a required value is missing. The api applies migrations on start (advisory-locked, safe with replicas);
the worker starts after the api is healthy. For local development with db/redis reachable from your host:
`docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build`.

**Status of the Docker files:** written and reviewed, **never built or run** (no Docker on the authoring machine). The API entrypoint
and migration script were exercised directly against PostgreSQL 18.1; the images, nginx template and compose file were not. Expect to fix
small things on the first `docker compose up --build`, and tell us what broke.

## Environment variables (names only; set them in `backend/.env` or `.env`)

Server (`backend/app/config/config.py`; names are case-insensitive, blank = not set):

| Name | Required | Default | Purpose |
|---|---|---|---|
| `JWT_SECRET` | **yes** (16+ chars) | none | Signs access/refresh tokens. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Changing it signs everyone out. |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | yes on first run | none | The first admin is created from these at startup if no such user exists; an existing password is never overwritten. |
| `ADMIN_NAME` | no | `Administrator` | Display name of that admin. |
| `DATABASE_URL` | no (yes in production) | SQLite file in `backend/data` | `postgresql+psycopg://user:password@host:5432/db` for PostgreSQL. |
| `DATA_DIR` | no | `backend/data` | Logos, evidence snapshots, report files. Must be writable and shared by api and worker. Back it up. |
| `APP_ENV` | no | `local` | `local`, `test` or `production`. `production` makes a missing/old migration show as degraded in `/health`; `test` disables the scheduler. |
| `LOG_LEVEL` | no | `INFO` | JSON logs with secret redaction. |
| `CORS_ORIGINS` | yes in production | `http://localhost:5173` | Comma-separated browser origins allowed to call the API (your frontend's https origin). |
| `ACCESS_TOKEN_MINUTES` / `REFRESH_TOKEN_DAYS` | no | `15` / `7` | Token lifetimes. `JWT_ALGORITHM` (default `HS256`) also exists. |
| `LOGIN_MAX_FAILURES` / `LOGIN_WINDOW_SECONDS` | no | `5` / `900` | Login rate limit per email+IP (in-memory, see Security). |
| `API_RATE_LIMIT_PER_MINUTE` | no | `600` | General API rate limit per IP and token (0 = off; in-memory, single process; see `docs/SECURITY.md`). |
| `LOGO_MAX_BYTES` | no | `2097152` | Max brand logo upload. |
| `URL_CHECK_TIMEOUT_SECONDS`, `URL_CHECK_MAX_REDIRECTS`, `URL_CHECK_MAX_BYTES` | no | `5`, `3`, `65536` | SSRF-safe reachability checker for official accounts. |
| `REDIS_URL` | no | unset | Enables the RQ queue (`redis://:password@host:6379/0`). Unset = in-process thread runner. Treated as a secret. |
| `SCAN_EXECUTOR` | no | `auto` | `auto` (rq if `REDIS_URL`, else thread), `rq`, `thread`, `inline` (tests). |
| `SCAN_QUEUE_NAME`, `SCAN_JOB_TIMEOUT_SECONDS`, `SCAN_STALE_MINUTES` | no | `scans`, `900`, `30` | Queue name, job timeout, age after which a stuck scan is failed. |
| `SCAN_PREFILTER`, `SCAN_MAX_CANDIDATES` | no | `true`, `500` | Skip hopeless candidates early; cap per scan. |
| `SCAN_LOGO_FETCH`, `SCAN_LOGO_MAX_BYTES`, `SCAN_LOGO_TIMEOUT_SECONDS`, `SCAN_MAX_LOGO_FETCHES` | no | `true`, `2097152`, `5`, `25` | SSRF-safe download of candidate logos (live data only). |
| `SCREENSHOTS_ENABLED` | no | `false` | Playwright screenshot hook (package not installed; untested live). |
| `SCHEDULER_ENABLED` | no | `true` | In-process scheduler loop. Run it in exactly **one** process. |
| `SCAN_INTERVAL_MINUTES` | no | unset | Overrides the Settings-page schedule; `0` = off. |
| `SCHEDULER_POLL_SECONDS` | no | `60` | How often the loop checks. |
| `TAVILY_API_KEY` | no (needed for live) | unset | Primary live source. Secret. |
| `TAVILY_MONTHLY_BUDGET`, `TAVILY_MAX_QUERIES_PER_SCAN`, `TAVILY_SEARCH_DEPTH`, `TAVILY_MAX_RESULTS` | no | `900`, `12`, `basic`, `10` | Credit guard and query shape (free plan = 1000 credits/month). |
| `SEARXNG_BASE_URL`, `SEARXNG_ALLOW_PRIVATE_HOSTS`, `SEARXNG_MAX_QUERIES_PER_SCAN` | no | unset, `false`, `12` | Optional self-hosted search fallback. |
| `ITUNES_COUNTRY`, `ITUNES_CALLS_PER_MINUTE`, `ITUNES_CACHE_TTL_SECONDS`, `ITUNES_MAX_QUERIES_PER_SCAN` | no | `in`, `20`, `3600`, `8` | App Store search (no key). |
| `GOOGLE_PLAY_COUNTRY`, `GOOGLE_PLAY_MAX_QUERIES_PER_SCAN` | no | `in`, `6` | Optional `google-play-scraper` package (not installed by default; license/terms unverified). |
| `SOURCE_SLOW_THRESHOLD_MS`, `COLLECTOR_TIMEOUT_SECONDS`, `REACHABILITY_CHECKS_PER_SCAN` | no | `4000`, `15`, `0` | Collector timing and optional reachability checks of result URLs. |
| `SCAM_LLM_ENABLED`, `SCAM_LLM_PROVIDER`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `HUGGINGFACE_API_TOKEN`, `HUGGINGFACE_MODEL` | no | `false`, `gemini`, unset, `gemini-3.5-flash-lite`, unset, unset | Optional hosted AI text check (never for sample data). Keys are secrets. See docs/USER_GUIDE.md. |
| `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_TRACES_SAMPLE_RATE` | no | unset, `APP_ENV`, `0.0` | Optional error reporting; needs `pip install -r backend/requirements-extra.txt`. DSN is a secret. |

Container-only (docker-compose.yml / entrypoint / frontend image):

| Name | Required | Default | Purpose |
|---|---|---|---|
| `POSTGRES_PASSWORD`, `REDIS_PASSWORD` | **yes** | none | Passwords for the compose db and redis (URL-safe characters only: they are embedded in connection URLs). |
| `POSTGRES_USER`, `POSTGRES_DB` | no | `scammind`, `scammind` | Compose database user and name. |
| `PUBLIC_API_URL` | no | `http://localhost:8000` | The API origin the browser calls. Becomes the frontend container's `API_URL` (written to `/config.js` at start and used in the CSP). |
| `API_PORT`, `FRONTEND_PORT` | no | `8000`, `5173` | Host ports (bound to 127.0.0.1). |
| `RUN_MIGRATIONS` | no | `true` | api entrypoint runs `scripts.migrate` first. `false` on extra replicas. |
| `PORT`, `FORWARDED_ALLOW_IPS` | no | `8000`, `127.0.0.1` | uvicorn port and the proxy addresses whose `X-Forwarded-*` headers are trusted. |
| `INSTALL_EXTRAS` | no | `false` | Build arg: install `requirements-extra.txt` (Sentry) into the image. |
| `VITE_API_URL` (frontend build) | no | `http://127.0.0.1:8000` | Build-time fallback API address; the runtime `API_URL` wins. |
| `TEST_DATABASE_URL` (tests only) | no | unset | Runs the test suite on PostgreSQL (throwaway database only). |

## First run

1. `copy backend\.env.example backend\.env` (or `cp .env.example .env` for Docker). The file `backend/.env` is gitignored; never commit real values.
2. Fill in `ADMIN_EMAIL`, `ADMIN_PASSWORD` and `JWT_SECRET`:
   `python -c "import secrets; print(secrets.token_urlsafe(48))"`
3. Apply migrations (`python -m scripts.migrate`, or the compose api does it) and start the server and frontend.
4. Sign in, open `/setup`, create the brand: name, aliases, official handles/apps, logo (PNG, JPEG or WebP, at least 256 px, roughly square, 2 MB max). The official handles and package ids are your **allowlist**: only an exact match is excluded from detections.
5. **Going live is your step.** Add `TAVILY_API_KEY=...` to `backend/.env` (or `.env` for Docker), then run the one-call check yourself (it costs 1 credit and is never run by the build):
   `cd backend && .\.venv\Scripts\python -m scripts.live_check` (see `docs/COLLECTORS.md`).
6. Settings > Account (admin) switches the data mode: **Auto** (default: live if a live source is configured, else sample), **Live**, **Sample**. The header shows a "Sample data" badge whenever the data is sample.
7. Run a scan from the Dashboard, review detections, create a report draft, approve it.

## Health, canary and checks

* `GET /health` (public): overall `ok | degraded | down` and one word per component (database, migration, collectors, redis, worker, data_dir). 200 for ok/degraded, **503 only if the database is down**. Cheap, makes no third-party calls.
* `GET /health/live`: the process answers (container liveness).
* `GET /api/health/details` (admin): reasons, ages, migration revision, worker count, free disk.
* **Canary:** `cd backend && python -m scripts.canary` or admin `POST /api/health/canary` plants a fictional fake ("Zephyrine Vault Bank") in an in-memory database, runs the real pipeline plus the sample collector, and reports pass/fail for: fake profile flagged, fake app stored for review, official assets excluded, unrelated business not flagged, score explained. It proves the **pipeline logic** (name matching, allowlist, scoring) still works after a change. It does **not** prove that Tavily/iTunes/etc. are reachable or return real data (use `/health` and the Source Health page, and one real scan, for that). It never touches your database or the network.

## Deploy to a managed host (generic)

Components: managed PostgreSQL, managed Redis, a container host for three containers (`api`, `worker`, `frontend`), a TLS-terminating proxy/ingress, and a persistent volume for `DATA_DIR`.

1. **Build and push** the two images (`backend/Dockerfile`, `frontend/Dockerfile`) to your registry. CI builds them without pushing; a publish job template is commented in `.github/workflows/ci.yml`.
2. **Database:** create a PostgreSQL database and user; set `DATABASE_URL=postgresql+psycopg://...` (add `?sslmode=require` if your provider needs it).
3. **Redis:** set `REDIS_URL` (use `rediss://` for TLS). The worker is idle without it.
4. **Secrets:** put `JWT_SECRET`, `ADMIN_PASSWORD`, `TAVILY_API_KEY`, `REDIS_URL`, `DATABASE_URL`, `SENTRY_DSN` in the platform's secret store, not in images, compose files or the repo. Rotate `JWT_SECRET` if it ever leaks (everyone is signed out).
5. **Migrations:** run once per release as a pre-deploy job: `docker run ... scammind-server migrate`. Set `RUN_MIGRATIONS=false` on the api replicas (safe either way: an advisory lock serialises concurrent runs). Roll back application code first; migrations 0001-0004 have downgrades, but avoid downgrading a database that holds real data without a backup.
6. **Processes:** api (1+ replicas) and worker (1+) from the same image (`api` / `worker` commands). **Exactly one process runs the scheduler**: `SCHEDULER_ENABLED=true` on one api instance, `false` everywhere else.
7. **DATA_DIR:** mount the same persistent volume at `/data` for api and worker (or a shared filesystem if they run on different hosts). **Back it up** (it holds evidence snapshots with their SHA-256, report files and the logo): a nightly snapshot of the volume plus your database backups.
8. **HTTPS and CORS:** terminate TLS at the proxy. Set `CORS_ORIGINS` to the exact https origin(s) of the frontend and the frontend's `API_URL` to the public https API origin. Set `FORWARDED_ALLOW_IPS` to the proxy's address so client IPs (login rate limit, audit) are real. The frontend image sends HSTS and a CSP that allows only the API origin and Google Fonts.
9. **Probes:** liveness `GET /health/live`; readiness `GET /health` (treat 503 as not ready; `degraded` is still serving).
10. **Observability:** optional Sentry (`INSTALL_EXTRAS=true` build arg + `SENTRY_DSN`); logs are JSON on stdout.

## Security notes

* **Rate limiter is in memory and per process.** Login throttling (5 failures per email+IP per 15 minutes) resets on restart and is not shared between replicas; put a rate limit at the proxy as well. A Redis-backed limiter is not built.
* **Refresh tokens** are stored server-side and rotated on every use; re-using a rotated token revokes all of that user's refresh tokens (theft detection). Side effect: two browser tabs refreshing at the same instant can sign both out. Access tokens live 15 minutes; they are kept in browser storage (not httpOnly cookies), so an XSS bug would expose them: the CSP and no-inline-script build are the mitigation.
* **SSRF guard:** every outbound fetch that uses a URL from data (reachability checks, candidate logos, SearXNG base URL) resolves DNS, refuses private/loopback/link-local/metadata addresses, limits redirects, size and content type, and re-checks after redirects. `SEARXNG_ALLOW_PRIVATE_HOSTS` is an explicit operator opt-in for a private SearXNG.
* Passwords use bcrypt (72-byte limit enforced). Secrets are redacted from logs; `Settings` holds keys as secret strings; `/health` never returns URLs or exception text.
* Official-asset exclusion is by exact handle/URL/package id only: the brand name alone never excludes anything.
* Roles: `viewer` read-only, `analyst` reviews and scans, `admin` also settings, data mode, health details and canary.
* The app sends nothing to anyone: notifications and takedown e-mails are stored preferences only (`delivery: not_implemented`).

## PRE-DEMO CHECKLIST

Tick each item on the machine you will demo from, the day before and again 30 minutes before.

- [ ] **Health green:** `GET /health` returns `ok` (or you understand every `degraded` item, e.g. Redis not configured on a one-box demo). Open `/api/health/details` as admin and read it.
- [ ] **Canary passes:** `python -m scripts.canary` exits 0 (or admin `POST /api/health/canary` shows `passed: true`). Run it after every code or config change.
- [ ] **Data mode is what you intend:** Settings > Account shows Live or Sample on purpose. For a sample-data demo say so out loud.
- [ ] **Sample badge visible when expected:** in sample mode the header shows "Sample data" and the detail panels are badged; in live mode it is absent. If it shows the wrong way, stop.
- [ ] **Credits meter:** Source Health shows the Tavily meter and remaining credits are enough for the scans you plan (one scan costs up to `TAVILY_MAX_QUERIES_PER_SCAN`, default 12). Run `scripts.live_check` yourself once, earlier the same day, if going live.
- [ ] **Official assets present:** Brand Profile lists the official handles and apps for the demo brand with verified status; the allowlist count is right. (Without them nothing can be excluded.)
- [ ] **A scan has been run** against the demo data and finished (`completed`, or `partial` with a source reason you can explain). Results are visible on the Dashboard and the Social/Apps pages.
- [ ] **Human approval flow rehearsed:** open a detection, confirm/dismiss, mark one as official, create a report draft, preview it, approve it. Nothing is sent anywhere; say so.
- [ ] **Look-alike Lab** run: "Run test cases" shows the expected pass count.
- [ ] **Accounts:** the admin password is not the one in any example; a viewer account exists if you want to show read-only access.
- [ ] **Backups / rollback plan:** a copy of the database and `DATA_DIR` taken before the demo; know the previous image tags and how to switch back; keep the last known-good `docker compose` `.env`. If something breaks mid-demo: switch the data mode to Sample (admin) and continue on fictional data.
- [ ] **Known limitations told, not hidden:** sample data is fictional; live results depend on Tavily coverage and credits; Google Play needs an optional package; no PDF export (printable HTML/CSV); notifications are not delivered; one brand per organisation; logo similarity needs a logo and live icon URLs; the login rate limiter is per process.
- [ ] **Network:** a stable connection; the UI loads Manrope from Google Fonts (falls back to a system font offline).

## Tests

```powershell
cd backend;   .\.venv\Scripts\python -m pytest -q          # SQLite, no network
             $env:TEST_DATABASE_URL="postgresql+psycopg://user@host:5432/throwaway"; .\.venv\Scripts\python -m pytest -q   # same suite on PostgreSQL
             .\.venv\Scripts\python -m ruff check .
cd frontend; npm run lint; npm test; npm run build; npm run e2e:install; npm run test:e2e
```

More: `backend/README.md`, `frontend/README.md`, `docs/API.md`, `docs/COLLECTORS.md`, `docs/DECISIONS.md`, `docs/DEPENDENCIES.md`, `docs/DONE_AUDIT.md`, `docs/SECURITY.md`.
