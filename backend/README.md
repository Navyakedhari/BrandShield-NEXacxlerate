# ScamMind AI server (Phase 1: skeleton and data model)

FastAPI + SQLAlchemy 2 + Alembic. PostgreSQL in production, SQLite for local and tests.
Design decisions: `../docs/DECISIONS.md`. API contract: `../docs/API.md`. Licenses: `../docs/DEPENDENCIES.md`.

## Run

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
copy .env.example .env        # then fill in JWT_SECRET (16+ chars), ADMIN_EMAIL, ADMIN_PASSWORD
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m uvicorn app.main:create_app --factory --reload --port 8000
```

Secrets come only from environment variables or `backend/.env` (gitignored). `.env.example` lists names only.
For PostgreSQL set `DATABASE_URL=postgresql+psycopg://user:password@host:5432/dbname` (not exercised yet in Phase 1).
The admin user is created on startup from `ADMIN_EMAIL` / `ADMIN_PASSWORD` if it does not exist; an existing password is never overwritten.

## Test

```powershell
cd backend
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\python -m ruff check .
```

Tests use SQLite in memory and never touch the network (the URL checker is injected).

## Endpoints (Phase 1)

| Method and path | Role |
|---|---|
| GET `/health`, `/api/health` | public (503 if DB is down) |
| POST `/api/auth/login`, `/api/auth/refresh`, `/api/auth/logout` | public |
| GET `/api/me`, `/api/auth/me` | any signed-in user |
| GET/POST `/api/brands`, GET/PUT `/api/brands/{id}` | read: any; write: analyst, admin |
| POST/GET `/api/brands/{id}/logo` | upload: analyst, admin; read: any |
| GET/POST/DELETE `/api/brands/{id}/accounts` | read: any; write: analyst, admin |
| GET/POST/DELETE `/api/brands/{id}/apps` | read: any; write: analyst, admin |
| GET `/api/audit`, `/api/audit/export.csv` | analyst, admin |

Errors are always `{"detail": ...}` (string, or a list of `{loc,msg,type}` for 422).

## Phase 3: collectors with a fallback chain

See `../docs/COLLECTORS.md` (how each collector works, what to provide, first live check). New endpoints:
GET `/api/sources`, PATCH `/api/sources/{id}`, POST `/api/sources/{id}/retry`, GET/PUT `/api/settings/data-mode` (details in `../docs/API.md`, section 9).
Tests never touch the network: HTTP is mocked and real network/DNS access is blocked by a test guard; fixtures are hand-written (`tests/fixtures/README.md`).

## Phase 4: scan pipeline, background worker, screens API

Everything the 10 screens need is documented in `../docs/API.md` section 18. Key points:

- A scan runs in the API process (thread) when `REDIS_URL` is unset. With `REDIS_URL` set it is queued on RQ; start a worker in a second terminal:
  `.\.venv\Scripts\python -m app.worker` (from `backend/`). Real Redis/RQ runs are not covered by the automated tests.
- The scheduler loop starts with the API unless `SCHEDULER_ENABLED=false` (never under `APP_ENV=test`). `SCAN_INTERVAL_MINUTES` (0 = off) overrides the Settings page.
- New migration: `alembic upgrade head` (0003).
- Nothing is sent or reported automatically; humans approve detections and reports.

## Phase 6: deployment readiness

- Container image: `Dockerfile` (API and RQ worker share it; entrypoint `docker-entrypoint.sh api|worker|migrate`). Migrations: `python -m scripts.migrate` (advisory-locked on PostgreSQL, safe with several replicas).
- `/health` is a readiness check (database, migration, redis, worker, data dir, collectors); `/health/live`; admin `/api/health/details`; admin `POST /api/health/canary`. See `../docs/API.md` section 19.
- Canary self-check: `python -m scripts.canary` (exit 0 = pass). Pipeline logic only; no network.
- Optional Sentry: `pip install -r requirements-extra.txt`, set `SENTRY_DSN` (scrubbed before send).
- Tests on PostgreSQL: set `TEST_DATABASE_URL` to a throwaway database (each test gets its own schema).
- Whole-system README (env table, deploy steps, pre-demo checklist): `../README.md`.
