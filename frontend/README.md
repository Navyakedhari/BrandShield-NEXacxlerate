# ScamMind AI frontend

React + TypeScript + Vite. It talks to the FastAPI server in `../backend`.
Screens: `/login`, `/setup`, `/dashboard`, `/brand`, `/social`, `/apps`, `/lookalike`, `/sources`, `/reports`, `/settings`, plus `/evaluation` (linked from Settings > Account).
Contract and status: `../docs/FRONTEND_CONTRACT.md` (section 17 is the Phase 5 status), API: `../docs/API.md` (section 18 is authoritative).

## Run

```powershell
# terminal 1: API (see ../backend/README.md for the first-time setup and .env)
cd backend
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m uvicorn app.main:create_app --factory --reload --port 8000

# terminal 2: frontend
cd frontend
npm install
npm run dev          # http://localhost:5173
```

- The API address comes from `VITE_API_URL` (default `http://127.0.0.1:8000`). The API allows the page origin through `CORS_ORIGINS` (default `http://localhost:5173`).
- There are no secrets in the frontend. Sign in with the admin created from `ADMIN_EMAIL` / `ADMIN_PASSWORD` in `backend/.env`, open `/setup` to create the brand, then run a scan.
- With no live collector configured (no `TAVILY_API_KEY`) the server is in **sample mode**: detections are fictional, every page shows a "Sample data" badge, and nothing contacts a real platform. Settings > Account (admin) switches Live / Sample / Auto.

## Check

```powershell
npm run build        # type-check (tsc -b) + production build
npm test             # Vitest + Testing Library (API client, hooks, login errors, route guard, optimistic rollback, source health)
npm run e2e:install  # once: downloads the Chromium build used by Playwright
npm run test:e2e     # Playwright smoke tests (needs backend/.venv, see below)
cd ..\server; .\.venv\Scripts\python -m pytest    # backend suite
```

### Browser smoke tests

`npm run test:e2e` starts two processes itself and stops them afterwards:

1. the real API through `backend/scripts/e2e_server.py` (temporary SQLite database, sample collector, stubbed URL check, **no network**, `backend/.env` is never read, no Tavily key), on port 8765;
2. the Vite dev server on port 5174.

The admin and viewer passwords and the JWT secret are generated per run and passed only through the environment of those child processes; they are not written to disk or printed. The tests sign in, create the brand, scan in sample mode and walk every page asserting key elements and zero console errors.
`e2e/visual.spec.ts` also saves a screenshot of each page to `e2e/screenshots/` (git-ignored) for comparison with the design PDF.
Override the Python used for the API with `E2E_PYTHON`, the ports with `E2E_API_PORT` / `E2E_WEB_PORT`.
