# Collectors (Phase 3)

A collector fetches raw candidates (accounts, pages, apps) from one provider and returns normalized
`Candidate` objects. The orchestrator runs them in a chain. **A failed source never blocks a scan.**
Code: `backend/app/core/collectors/`.

## How it fits together

| Piece | File | Job |
|---|---|---|
| Contract | `base.py` | `Candidate`, `SourceUnavailable(reason, message, retry_after)`, URL to handle conversion (via `engine/urls.py`), title cleaning (" \| Instagram", "(@x) • Instagram photos and videos", "- Google Play" ...), non-profile URLs dropped |
| Tavily | `tavily.py`, `credits.py` | Primary live search. Quoted queries x lure words, aliases, look-alike variants (`variants.py`). `include_domains` = social + store + APK mirror domains (only platforms still monitored). `exact_match`. Persistent monthly credit counter (table `api_usage`) with a hard budget guard |
| SearXNG | `searxng.py` | Optional fallback for social profiles. Needs `SEARXNG_BASE_URL` and `json` enabled in the instance |
| iTunes | `itunes.py`, `util.py` | App Store search, no key. Spaced to ~20 calls/min, responses cached with a TTL |
| Google Play | `google_play.py` | Optional package behind an adapter; `not_installed` when absent |
| Sample | `sample.py`, `sample_images.py` | Deterministic synthetic candidates, every one `source="sample"`: official echoes, strong / medium / weak look-alikes, hard negatives, app look-alikes. With an uploaded brand logo they carry GENERATED image bytes (`Candidate.inline_image`: near copy, resized, recoloured, cropped or unrelated) so the normal logo comparison runs with no network; the pipeline honours those bytes only for `source="sample"`. Details: DECISIONS.md "Sample logo variants" |
| Selection | `selection.py` | Data mode (`live\|sample\|auto`) and the ordered chain with reasons |
| Orchestrator | `orchestrator.py` | Runs the chain, records `sources` status, caches last good results, back-off |

### Data mode

Stored in the `settings` table (`data_mode`, default `auto`), changed by an admin through `PUT /api/settings/data-mode` (audited).
`auto` = live when a Tavily key, a SearXNG base URL or the google-play package is present, otherwise sample. In `live`, a missing key is shown as
`unavailable (no_key)`; sample data is never mixed in. Live chain order: tavily, searxng (only if set), itunes, google_play.

### Failure handling and cache

| Situation | Reason code | Behaviour |
|---|---|---|
| No `TAVILY_API_KEY` | `no_key` | unavailable, re-checked every scan, no network call |
| 401 | `invalid_key` | unavailable, back-off |
| 429 (+ `Retry-After`) | `rate_limited` | unavailable (or `slow` with partial results mid-scan), next retry honours Retry-After |
| 432 / 433 | `quota` | unavailable, back-off |
| Budget would be exceeded | `budget` | **no call is made**; partial results kept if it happens mid-scan |
| 5xx, timeout, connection error | `unreachable`, `timeout` | unavailable, back-off |
| Bad JSON / wrong shape | `bad_response` | unavailable |
| SearXNG 403 | `json_disabled` | message explains: set `search: formats: [html, json]` in the instance `settings.yml` |
| SearXNG base URL private | `unsafe_base_url` | refused unless `SEARXNG_ALLOW_PRIVATE_HOSTS=true` |
| Package missing | `not_installed` | unavailable |

Back-off after consecutive failures: 1 min, 5 min, 15 min, 1 h, 6 h (cap); resets on the first success. While a source is unavailable the last good results are
returned marked `stale` with their age (`cached_age_seconds` on `GET /api/sources`). A manual retry (`POST /api/sources/{id}/retry`) ignores back-off.
Status `slow` = latency above `SOURCE_SLOW_THRESHOLD_MS` (default 4000) or partial results.

### Tavily credits

basic search = 1 credit, advanced = 2 (`TAVILY_SEARCH_DEPTH`). `TAVILY_MONTHLY_BUDGET` (default 900) is a hard guard checked before every call; the counter lives in
the database so it survives restarts and is per calendar month (UTC). Usage reported by Tavily is stored too and the larger number is used. Per-scan cap:
`TAVILY_MAX_QUERIES_PER_SCAN` (default 12 queries = 12 credits at basic). The meter is returned as `tavily_credits` by `GET /api/sources`.

## Fixtures are hand-written

`backend/tests/fixtures/` holds **hand-written** responses based on the public API documentation, **not captured from live services**. Nothing in the build or the
tests contacts Tavily, Apple or SearXNG (a test guard blocks real network access). After you add keys, capture real responses (below) and replace the fixtures;
then run `pytest tests/collectors`. Real responses may contain fields not in the fixtures; every field is treated as optional.

## What you must provide

| What | Where | Required? |
|---|---|---|
| `TAVILY_API_KEY` | `backend/.env` | Yes for live social search. Without it live mode reports Tavily as `no_key` and auto mode stays on sample |
| `TAVILY_MONTHLY_BUDGET`, `TAVILY_MAX_QUERIES_PER_SCAN`, `TAVILY_SEARCH_DEPTH` | `backend/.env` | No (defaults 900, 12, basic) |
| `SEARXNG_BASE_URL` (+ `SEARXNG_ALLOW_PRIVATE_HOSTS=true` if it is on a private network) | `backend/.env` | No, optional fallback; the instance must enable `json` format |
| `ITUNES_COUNTRY` (default `in`) | `backend/.env` | No |
| `google-play-scraper` package | `pip install` in the venv | No, optional; **license unverified**, see `docs/DEPENDENCIES.md` before installing |

Restart the server after editing `.env`. Names only are in `backend/.env.example`; never commit `.env`.

## First live check (run it yourself)

The build did not run this. It sends **one** Tavily search (1 credit) and prints the status without printing the key.

```powershell
cd backend
.\.venv\Scripts\python -m alembic upgrade head                      # once, so the credit counter can be stored
.\.venv\Scripts\python -m scripts.live_check                        # status only
.\.venv\Scripts\python -m scripts.live_check --save tavily_real.json   # also keep the raw response to use as a fixture
```

Expected: `HTTP status: 200`, `Results returned: <n>`, `Credits reported by Tavily: 1`. 401 means a wrong key, 432/433 a plan limit, 429 rate limited.
Then start the server, sign in as admin, open Source Health (or `GET /api/sources`) and use "Retry" on Instagram to run a real collector pass; the credit meter should move.
Do the same for iTunes by retrying Apple App Store (no key needed). If anything looks different from the fixtures, send the saved raw response so the fixtures and parsers can be corrected.
