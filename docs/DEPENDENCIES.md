# Dependencies (backend/)

Licenses read from installed package metadata (`importlib.metadata`) in `backend/.venv` on 2026-10-13.
Direct dependencies are listed in `backend/requirements.txt` / `requirements-dev.txt`; the rest are transitive.

## Needs review

| Package | Version | License | Why flagged |
|---|---|---|---|
| psycopg | 3.3.6 | LGPL-3.0-only | Weak copyleft. Used unmodified as a normal import (PostgreSQL driver); generally acceptable, but legal should confirm for distribution. Alternative: none permissive for Postgres except `pg8000` (BSD-3). |
| psycopg-binary | 3.3.6 | LGPL-3.0-only | Same as above (bundled libpq and compiled extension). |
| certifi | 2026.7.22 | MPL-2.0 | File-level weak copyleft; only matters if we modify certifi files. Pulled in by httpx. |
| colorama | 0.4.6 | (License field empty; classifier says BSD License) | Metadata unclear but upstream is BSD-3-Clause. Windows-only dev/test dependency of pytest/click. |
| pillow | 12.3.0 | MIT-CMU | Permissive (HPND-style); listed only because the identifier is uncommon. |
| email-validator | 2.3.0 | Unlicense | Public-domain dedication; some legal teams prefer a named license. |
| python-dateutil | 2.9.0.post0 | "Dual License" (BSD and Apache-2.0 per its classifiers) | Transitive (rq -> croniter -> python-dateutil). Either license is permissive; the metadata `License` field just says "Dual License". |

No GPL or AGPL packages were found.

## Added for the extension (work packages E1-E4), read from installed metadata on 2026-10-14

| Package | Version | License | Direct? | Notes |
|---|---|---|---|---|
| tldextract | 5.4.0 | BSD-3-Clause | yes (`requirements.txt`) | Used with `suffix_list_urls=()` and `cache_dir=None`: only the Public Suffix List snapshot shipped inside the package, no download, no cache file. The snapshot goes stale; refresh by upgrading the package. A TLD missing from it (`.test`, a very new gTLD) is treated as a one-label suffix by `engine/domain_match.py`. |
| RapidFuzz | 3.14.6 | MIT | yes (`requirements.txt`) | Additional similarity signal for domains only; the home-grown name matcher is unchanged. |
| idna | 3.20 | BSD-3-Clause | yes (`requirements.txt`) | Already installed through httpx; now a declared direct dependency (IDN/punycode). |
| requests | 2.34.2 | Apache-2.0 | no (tldextract) | Pulled in by tldextract. NOT used by this app and never called by tldextract here (no list download). |
| requests-file | 3.0.1 | Apache-2.0 ("Apache 2.0") | no (tldextract) | Same. |
| filelock | 4.1.1 | MIT | no (tldextract) | Same. |
| ImageHash | 4.3.2 | BSD-2-Clause ("2-clause BSD License") | yes, OPTIONAL (`requirements-extra.txt`) | Installed in the dev venv for the later image work (E6); nothing imports it yet. |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | no (ImageHash) | Optional chain only. |
| scipy | 1.18.1 | BSD-3-Clause (classifier; the License field holds the long text) | no (ImageHash) | Optional chain only (large wheel). |
| PyWavelets | 1.10.0 | MIT AND BSD-3-Clause | no (ImageHash) | Optional chain only. |

No GPL or AGPL packages were added. Google Safe Browsing is called with plain `httpx` (no Google client library).

## All installed packages

| Package | Version | License | Direct? |
|---|---|---|---|
| alembic | 1.20.0 | MIT | yes |
| annotated-doc | 0.0.5 | MIT | no |
| annotated-types | 0.8.0 | MIT | no |
| anyio | 4.15.1 | MIT | no |
| bcrypt | 5.0.0 | Apache-2.0 | yes |
| certifi | 2026.7.22 | MPL-2.0 | no |
| click | 8.5.0 | BSD-3-Clause | no (rq dependency) |
| croniter | 6.2.4 | MIT | no (rq dependency, added in Phase 4) |
| colorama | 0.4.6 | BSD (classifier) | no |
| dnspython | 2.9.0 | ISC | no |
| email-validator | 2.3.0 | Unlicense | yes |
| fastapi | 0.143.0 | MIT | yes |
| greenlet | 3.5.6 | MIT AND PSF-2.0 | no |
| h11 | 0.16.0 | MIT | no |
| httpcore | 1.0.9 | BSD-3-Clause | no |
| httptools | 0.9.0 | MIT | no |
| httpx | 0.28.1 | BSD-3-Clause | yes |
| idna | 3.20 | BSD-3-Clause | no |
| iniconfig | 2.3.1 | MIT | no |
| Mako | 1.4.3 | MIT | no |
| MarkupSafe | 3.0.4 | BSD-3-Clause | no |
| opentelemetry-api | 1.45.1 | Apache-2.0 | no |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | no |
| pillow | 12.3.0 | MIT-CMU | yes |
| pip | 24.2 | MIT | no |
| pluggy | 1.6.0 | MIT | no |
| psycopg | 3.3.6 | LGPL-3.0-only | yes |
| psycopg-binary | 3.3.6 | LGPL-3.0-only | yes (extra) |
| pydantic | 2.14.1 | MIT | yes |
| pydantic-settings | 2.15.0 | MIT | yes |
| pydantic_core | 2.50.1 | MIT | no |
| Pygments | 2.21.0 | BSD-2-Clause | no |
| PyJWT | 2.15.1 | MIT | yes |
| pytest | 8.4.2 | MIT | dev |
| python-dateutil | 2.9.0.post0 | Dual License (classifiers: BSD and Apache Software License) | no (croniter dependency, added in Phase 4) |
| python-dotenv | 1.2.4 | BSD-3-Clause | no |
| redis | 6.4.0 | MIT | yes (Phase 4) |
| rq | 2.12.0 | BSD-2-Clause | yes (Phase 4) |
| six | 1.17.0 | MIT | no (python-dateutil dependency, added in Phase 4) |
| python-multipart | 0.0.32 | Apache-2.0 | yes |
| PyYAML | 6.0.3 | MIT | no |
| ruff | 0.17.0 | MIT | dev |
| SQLAlchemy | 2.0.54 | MIT | yes |
| starlette | 1.7.0 | BSD-3-Clause | no |
| typing-inspection | 0.4.4 | MIT | no |
| typing_extensions | 4.16.0 | PSF-2.0 | no |
| tzdata | 2026.5 | Apache-2.0 | no |
| uvicorn | 0.54.0 | BSD-3-Clause | yes |
| watchfiles | 1.3.0 | MIT | no |
| websockets | 17.2 | BSD-3-Clause | no |

## Phase 2 (detection engine)

No package was added to `requirements.txt`. The engine uses only the standard library, Pillow and pydantic (already present). pHash/dHash, the DCT, Damerau-Levenshtein and DP word segmentation are implemented in `backend/app/core/engine/`; `imagehash` and `numpy` were not needed.

Optional packages, lazily imported, NOT installed and NOT in requirements (the engine works without them):

| Package | License (from package metadata) | Used by | Notes |
|---|---|---|---|
| open_clip_torch + torch | MIT (open_clip) / BSD-3-Clause (torch), from upstream project pages, NOT verified from metadata because not downloaded | `engine/logo_similarity.py` `ClipSimilarity` (optional CLIP embedder) | Heavy (hundreds of MB). Each pretrained model weight set has its own license; check before use. Unverified, do not install without a metadata check. |


## Phase 3 (collectors)

No package was added to `requirements.txt`. Everything uses `httpx` (already a direct dependency; its `MockTransport` is used in tests, so no `respx`).

Optional package, lazily imported, NOT installed and NOT in requirements (`backend/app/core/collectors/google_play.py`):

| Package | License | Used by | Notes |
|---|---|---|---|
| google-play-scraper 1.2.7 (import name `google_play_scraper`) | **MIT** (read from the installed wheel metadata) | `GooglePlayCollector` (search), installed via `requirements-extra.txt` | It scrapes Google Play pages (no official search API), so review Google's terms of use before relying on it. Without it the source reports `not_installed` and the app works. Tests inject a fake search function. |

External services (no package): Tavily Search API (needs `TAVILY_API_KEY`; plan terms apply), Apple iTunes Search API (no key, about 20 calls per minute), optional self-hosted SearXNG (AGPL-3.0 software run by the operator as a separate service, not linked or bundled; legal may want to note it).


## Phase 4 (scan pipeline, background worker)

Added to `backend/requirements.txt` after reading the licenses from the installed package metadata (`importlib.metadata`, 2026-10-13):

| Package | Version | License (from metadata) | Why |
|---|---|---|---|
| rq | 2.12.0 | BSD-2-Clause (`License-Expression`; classifier "BSD License") | Redis queue + worker for scans (`python -m app.worker`). Imported lazily, so the app and the tests run without it being used. |
| redis | 6.4.0 | MIT (`License-Expression`; classifier "MIT License") | Redis client used by rq and by the job dispatcher. Lazy import. |
| croniter | 6.2.4 | MIT | Pulled in by rq (scheduled jobs; we do not use rq's scheduler). |
| python-dateutil | 2.9.0.post0 | Dual: BSD and Apache-2.0 (classifiers; the `License` field only says "Dual License") | Pulled in by croniter. |
| six | 1.17.0 | MIT | Pulled in by python-dateutil. |

No GPL or AGPL packages were added. `fakeredis` was NOT used (the tests inject fake `redis` / `rq` modules instead). Playwright (optional screenshot hook) is NOT installed and NOT in requirements; its license is unverified because it was never downloaded.

**Not verified:** a real Redis server and a real RQ worker run. Only the dispatch logic (queue name, function path, job id, fallback to a thread when Redis is unreachable) is tested, against fake modules.


## Frontend (Phase 5)

Licenses read from `node_modules/*/package.json` after `npm install` (2026-10-13, 123 packages audited in `frontend/node_modules`); `npm audit` reported 0 vulnerabilities.

Runtime dependencies (unchanged by Phase 5): react 19.3.0 (MIT), react-dom 19.3.0 (MIT), react-router-dom 7.18.4 (MIT), lucide-react 1.55.0 (ISC), d3-geo 3.1.1 (ISC; used only by the orphaned pages' map).

Dev dependencies added in Phase 5 (all direct, all `devDependencies`, none ships to users):

| Package | Version | License | Used for |
|---|---|---|---|
| vitest | 5.0.4 | MIT | unit tests (`npm test`) |
| jsdom | 29.1.1 | MIT | DOM for the unit tests |
| @testing-library/react | 16.3.3 | MIT | component and hook tests |
| @testing-library/jest-dom | 7.0.1 | MIT | DOM matchers |
| @testing-library/user-event | 14.6.7 | MIT | simulated typing and clicks |
| @playwright/test | 1.64.0 | Apache-2.0 | browser smoke tests (`npm run test:e2e`) |

Licenses across the whole installed tree: MIT 97, ISC 6, Apache-2.0 8, BSD-3-Clause 2, BSD-2-Clause 2, MIT-0 2, BlueOak-1.0.0 1, CC0-1.0 1, MPL-2.0 2. All of these are permissive except the one flagged below.

### Needs review (Frontend)

| Package | License | Why flagged |
|---|---|---|
| lightningcss 1.33.0 (+ lightningcss-win32-x64-msvc) | MPL-2.0 | File-level weak copyleft. It was already in the tree before Phase 5 (a dependency of Vite) and is a build tool, not shipped in the bundle. Only matters if we modify lightningcss files. |
| Playwright browser download (Chromium, ffmpeg, winldd) | Chromium: BSD-3-Clause and many bundled third-party licenses; ffmpeg build: LGPL; not verified file by file | Downloaded by `npx playwright install chromium` into the user's profile (`%LOCALAPPDATA%\ms-playwright`), used only to run tests, never committed or shipped. Licenses of the binaries are NOT verified here; the npm packages themselves (`playwright`, `playwright-core`) are Apache-2.0. |
| lru-cache 11.5.3 | BlueOak-1.0.0 | Permissive but an uncommon identifier (transitive, via jsdom). |
| @csstools/* 6.x / 1.x | MIT-0 | Public-domain-like, permissive (transitive, via jsdom). |
| mdn-data 2.27.1 | CC0-1.0 | Data package (transitive, via jsdom); public-domain dedication. |

No GPL or AGPL package was found in the frontend tree.


## Phase 6 (deployment readiness)

- **psycopg / psycopg-binary 3.x (LGPL-3.0-only):** already in `backend/requirements.txt` since Phase 1 and already flagged above. Phase 6 makes it load-bearing (PostgreSQL is the production database and was exercised against a real PostgreSQL 18.1). Same review note applies: unmodified normal import, container image distributes it, legal should confirm.
- **sentry-sdk 2.72.0 (MIT):** license read from the wheel metadata of the downloaded file (`License-Expression: MIT`, `License-File: LICENSE`) on 2026-10-13. It is in the new `backend/requirements-extra.txt` (optional; NOT in `requirements.txt`, not installed in `.venv`, not needed by the tests, which use a fake module). Install with `pip install -r requirements-extra.txt` or build the image with `--build-arg INSTALL_EXTRAS=true`. Its transitive dependencies (certifi MPL-2.0 already listed, urllib3 MIT) were not separately audited.
- **pgvector:** NOT added. Documented optional for future embedding similarity.
- **Container base images (not Python/npm packages, licenses not audited):** `python:3.12-slim` (Debian + PSF), `node:22-alpine`, `nginxinc/nginx-unprivileged:1.27-alpine` (BSD-2-Clause nginx), `postgres:17-alpine` (PostgreSQL License), `redis:7.2-alpine` (BSD-3-Clause; pinned to 7.2 because, to my recollection (unverified), Redis 7.4+ switched to RSALv2/SSPL, and 8.x added AGPLv3: **check which Redis tag you pull and consider `valkey/valkey` (BSD-3-Clause) if that matters to legal**).
- **CI actions** (checkout, setup-python, setup-node, upload-artifact, docker/*) are pinned by major version; they are GitHub-owned or Docker-owned actions.
- **Frontend:** no new npm packages in Phase 6.


## E5-E7 (text providers, images, registry)

Licenses read from the installed metadata (`importlib.metadata`) on 2026-10-14:

| Package | Version | License | Where | Notes |
|---|---|---|---|---|
| google-genai | 2.30.0 | Apache-2.0 | `app/core/providers/text/gemini.py`, lazy import, `requirements-extra.txt` | ~11 MB; brings google-auth, tenacity, websockets, requests, distro, sniffio |
| huggingface_hub | 2.2.0 | Apache-2.0 | `app/core/providers/text/huggingface.py`, lazy import, `requirements-extra.txt` | ~7 MB; brings httpx2, hf-xet, fsspec, tqdm, pyyaml, click |
| ImageHash | 4.3.2 | BSD-2-Clause | `engine/image_hashes.py::ImageHashSimilarity`, optional | **Heavy:** needs numpy 2.5.4 (BSD-3-Clause and others, ~54 MB), scipy 1.18.1 (BSD, ~135 MB), PyWavelets 1.10.0 (MIT / BSD-3-Clause, ~9 MB): about 200 MB installed. So the default image code implements pHash, dHash, a Haar low-pass wHash and a colour hash locally over Pillow; ImageHash stays optional |

The Anthropic option and its optional `anthropic` package were removed in E5 (the owner does not use that API). No GPL/AGPL package was added.
