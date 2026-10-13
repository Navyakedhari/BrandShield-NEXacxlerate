# Feature status: what works with real data

Verified on 13 Oct 2026 by running the app end to end (API, browser, scripts) on a throwaway database. This file says honestly
what was exercised, with which kind of data, and what was not.

**Data labels used in the tables**

| Label | Meaning |
|---|---|
| REAL | A live source answered (Tavily search, Apple iTunes Search, real HTTP reachability checks). |
| SAMPLE | The built-in sample collector (fictional "Acme Bank" candidates, generated images). No network. |
| MOCK-ONLY | Only covered by the automated tests with mocked services (`pytest`, 834 passed); not run for real. |
| n/a | The feature does not depend on collected data (login, settings, validation ...). |

**Status**: PASS = behaved as documented. PARTIAL = works but with a gap or limit you should know. FAIL = does not work.

## How it was tested

* A second API on `127.0.0.1:8010` and a second Vite server on `localhost:5174`, each pointed at throwaway SQLite databases
  (`SCAN_EXECUTOR=thread`, `TAVILY_MAX_QUERIES_PER_SCAN=8`). The normal dev database and ports were not touched.
* Roles: the admin from `backend/.env`, plus a viewer and an analyst inserted directly into the throwaway database (see defect D13: the app has no way to create users).
* Browser checks with Playwright (Chromium) on every page, watching the console.
* Live run: one real, well-known consumer brand as the subject (not named here), its official handles and its iOS bundle id (confirmed from the live iTunes lookup). Results are unreviewed candidates, not accusations; no real account is named in this repository, nothing was reported or sent anywhere.
* Two defects were fixed with regression tests and committed separately (D1, D2). The test suite passes (834 passed, 5 skipped) and `ruff` is clean.

## Feature table

| # | Feature | Status | Data | Evidence (what was run) | Notes / limits |
|---|---|---|---|---|---|
| **1. Auth** | | | | | |
| 1.1 | Login, wrong-password message | PASS | n/a | `POST /api/auth/login` 200 with tokens; wrong password and unknown email both 401 "Incorrect email or password" (same text, no user hint). UI shows the same message. | Reserved e-mail domains such as `.test` are rejected with 422 (D13). |
| 1.2 | Login rate limit | PASS | n/a | 5 failures for one email+IP, the 6th answers 429 "Too many failed attempts. Try again later." (API and UI); even the correct password is refused while blocked. | In-memory, per process, resets on restart. |
| 1.3 | Refresh, rotation, logout | PASS | n/a | Refresh 200 and rotates; re-using the old refresh token 401 and revokes the whole family; after logout the refresh token is 401. | The short-lived access token still works after logout until it expires (D14). |
| 1.4 | Sign-out in the UI | PASS | n/a | User menu > Sign out: lands on `/login`, browser storage keys removed, `/dashboard` redirects to `/login`. | |
| 1.5 | Roles viewer / analyst / admin | PASS | n/a | Viewer: reads OK, scan/review/settings/health 403, UI buttons disabled with a "Viewer" notice. Analyst: scans, reviews, brand edits, audit read OK; settings write, data mode, health details, canary, Look-alike test-case add/delete 403. Admin: everything. | |
| 1.6 | Creating more users | FAIL (not built) | n/a | No endpoint and no screen. Only the admin from `ADMIN_EMAIL`/`ADMIN_PASSWORD` is created at start. Viewer and analyst rows had to be inserted into the database by script. | See D13. |
| **2. Setup wizard and Brand Profile** | | | | | |
| 2.1 | Setup wizard (3 steps) | PASS | n/a | Name + aliases + logo saved in step 1 (logo errors shown inline); step 2 lists official assets; step 3 shows source status and "Launch" starts a scan. | Step 2 is read-only: assets are added on Brand Profile. Launch scans immediately, before any official asset exists (D5). |
| 2.2 | Brand create / update | PASS | n/a | Create 201; second brand 409 "A brand already exists"; update 200. Empty name 422; alias over 80 characters 422; duplicate aliases collapse case-insensitively. | One brand per workspace. |
| 2.3 | Domain validation | PASS | n/a | `https://x.example/x` and `bad_domain` rejected with a message; `ACMEBANK.in ` is normalised to `acmebank.in`. | In the UI a bad domain chip is accepted and the whole save then fails (D11). |
| 2.4 | Logo upload rules | PASS | n/a | 100x100 PNG 422 "at least 256 x 256"; 600x300 422 "roughly square"; text renamed to .png 415; GIF 415; 2.3 MB file 413 "larger than 2 MB"; 300 px JPEG and 512 px PNG accepted; viewer 403. The UI checks the same rules before uploading. | The type is decided from the file bytes. The stored copy is re-encoded. |
| 2.5 | Official accounts | PASS | REAL (reachability) | Valid handle 201; `@` stripped; duplicate 409; `ab` and `bad handle!` 422; unknown platform (`tiktok`) 422; a handle that answers 404 is saved as `unreachable` with the warning "The URL answered with HTTP 404". | Only Instagram, X, Facebook, LinkedIn, YouTube. No Telegram. "Verified" only means the URL answered: Instagram answers 200 for any name. |
| 2.6 | Hyphenated LinkedIn / YouTube handles | PASS (fixed) | n/a | `acme-bank` was rejected with 422 (D2). Fixed and tested: LinkedIn slugs (up to 100 chars) and YouTube handles may contain hyphens; Instagram, X, Facebook still reject them. | |
| 2.7 | Official apps | PARTIAL | n/a | App with publisher added 201; duplicate package 409; `1bad.pkg` and `single` rejected with a clear message; missing publisher 422; unknown store 422. | The UI adds one store per app, so the same id on both stores gives 409 (D4). Numeric App Store ids cannot be entered (D3). "Import from store listing URL" only shows "not available yet". |
| 2.8 | Profile health score | PASS | n/a | 45% with name/aliases/keywords only; 90% after logo, accounts, app; 100% when every check passes; each missing item has a reason. | Weights: aliases 20, keywords 10, logo 20, domains 15, accounts 15, apps with publisher 10, URLs reachable 10. |
| **3. Scans** | | | | | |
| 3.1 | Scan in sample mode | PASS | SAMPLE | 26 candidates, 5 official excluded, 17 detections (12 social, 5 apps; bands 6 high, 4 medium, 7 low), finished in under 1 s. | |
| 3.2 | Scan in live mode | PASS (after fix) | REAL | Tavily + iTunes: 92 candidates (89 unique), 1 official excluded, 25 evaluated, 19 detections, 6 cleared, about 45 s. | The first live scan failed with "database is locked" (D1, fixed). |
| 3.3 | Progress polling | PASS | both | `GET /api/jobs/{id}` showed `running` 3%, then "Analysed 50 of 89 candidates", then `done` 100%. The dashboard follows it. | |
| 3.4 | One scan at a time | PASS | SAMPLE | Second `POST /api/scan` while one runs: 409 with the running job id. | |
| 3.5 | Partial status | PARTIAL | REAL | When Tavily was down (bad key) or Google Play missing the scan still finished as `partial` with reasons. With every source down: `failed`, "No data source was available". | A scan is also labelled `partial` just because sources were slow (D7). |
| 3.6 | Scan history | PASS (API only) | both | `GET /api/scans` lists scans with counts, timings and per-source reports. | No history screen in the UI; the Dashboard shows the last scan. |
| 3.7 | Scheduler | PASS | SAMPLE | Enabled by default. With a brand and no scans, a scan started by `scheduler` ran about 10 s after the brand was created (poll 5 s in the test; default poll 60 s). Settings > schedule saved (hourly) and `next_scan_at` computed. | Credit-aware stretching and pausing is MOCK-ONLY. See D5. |
| **4. Collectors and Source Health** | | | | | |
| 4.1 | Tavily live | PASS | REAL | 8 queries per scan = 8 credits (cap respected: counter 8 after scan 1, 16 after scan 2). Quoted queries such as `"<brand>" support`, `"<brand>" official`, `"<brand>" helpline`, `"<brand>" rewards`, then each alias with official/support. Results parsed into platform + handle / package id (Facebook handles, X profile, Google Play package id, APK mirror slug). Domain-restricted to social, store and APK sites. | Look-alike spelling queries come after the alias queries, so with a cap of 8 to 12 and several aliases they are never run (D8 note). Facebook result titles are post headlines (D8). |
| 4.2 | iTunes live | PASS | REAL | 73 apps from 8 searches (country `in`), publisher, bundle id, track id and icon URL parsed; spaced at about 20 calls/min so a scan takes 18 to 20 s. | |
| 4.3 | SearXNG | PASS (unavailable state) | REAL (failure) / MOCK-ONLY (search) | Not configured: listed as inactive "SEARXNG_BASE_URL not set". Pointed at a dead address: `unavailable (unreachable)` and the scan carried on. | A working SearXNG instance was not available to test. |
| 4.4 | Google Play | PARTIAL | REAL (failure) | Adapter reports `unavailable (not_installed)` with a clear reason in the scan report and does not break the scan. | Source Health shows the Google Play row from Tavily's status ("Responding slowly") and hides the not-installed reason (D7). Real Google Play search is MOCK-ONLY. |
| 4.5 | Sample collector | PASS | SAMPLE | Official echoes, strong, medium and weak look-alikes, hard negatives, apps; generated logo variants so logo comparison runs. All rows labelled sample. | |
| 4.6 | Data mode Live / Sample / Auto | PASS | both | `PUT /api/settings/data-mode`: sample (no network), live, auto. Auto resolved to **live** because a Tavily key was present ("Auto: live because configured: Tavily key"). Non-admin 403, bad value 422. | Auto resolving to sample with no key is MOCK-ONLY. A key in `.env` makes Auto live, so the first scan costs credits. |
| 4.7 | Source Health page | PASS | both | 8 sources with status, message, last sync and counts; summary tiles; "When a source is unavailable" steps; recent source events; credit meter. | |
| 4.8 | Retry | PARTIAL | REAL | `POST /api/sources/{id}/retry` runs the collectors again ignoring back-off: Google Play retry 202 with both reports. Online source: 409 "Nothing to retry". | The button is shown for **slow** as well as unavailable sources and a Tavily retry spends credits with no confirmation (D6). |
| 4.9 | Monitor toggle | PASS | n/a | `PATCH /api/sources/{id}` and the checkbox toggle and persist; a switched-off platform is skipped. | |
| 4.10 | Credit meter and budget guard | PASS | REAL | Meter 0 then 8 then 16 then 32 of 900. With `TAVILY_MONTHLY_BUDGET=32`: "Tavily monthly budget reached (32 of 32). No call was made." and the counter did not move. | The counter is stored per database. Tavily's own usage page can lag. |
| 4.11 | Failure back-off | PASS | REAL | Invalid key (401, not billed): `unavailable (invalid_key)`, cached results labelled, next scan "Waiting to retry (back-off)", `consecutive_failures` counting, scan still completed. | Back-off steps 1, 5, 15 minutes, 1 h, 6 h are from the code; the 5/15 min steps were not waited out. |
| **5. Detection logic** | | | | | |
| 5.1 | Allowlist exclusion | PARTIAL | SAMPLE + REAL | Exact handle/package matches are removed before scoring (sample: 5 of 26; live: the official iOS app returned by iTunes was excluded by bundle id). Mark as official and a re-scan keep it excluded. | The same official iOS app returned by Tavily (numeric id only) is **not** excluded and shows up as a detection (D3). |
| 5.2 | Name matching | PASS | SAMPLE + REAL | Characters swaps, digits, homoglyphs, spacing, transposed/missing letters, added words all flagged; other businesses ("Acme Bakery") not flagged. More than 40 names tried in the Look-alike Lab (see the user guide). | `Acne Bank` is flagged (one letter away): expect some false positives. |
| 5.3 | Scoring, reasons, bands | PASS | both | Every detection lists reasons whose points add up to the score (social max: name 34, logo 28, added words 12, bio 28, account 18; apps: name 24, icon 30, publisher 22, description 17, store 10; cap 100). Bands 85+ high, 70-84 medium, below 70 low. | Live run: 19 detections, all **low** (21 to 51). |
| 5.4 | Logo comparison | PARTIAL | SAMPLE: generated images. REAL: App Store icons only | Real run: 12 App Store icons fetched through the SSRF-safe path and compared (similarity 0 to 100); 13 candidates from Tavily were "logo unavailable: the source gave no profile picture / icon URL". | Tavily never returns profile pictures, so on live data social profiles are never logo-compared. Sample logos are generated, labelled "generated sample". |
| 5.5 | Bio / description analysis | PARTIAL | SAMPLE (strong) / REAL (snippet only) | Sample bios trigger signals (KYC lure, DM contact, reward lure ...). Live: the search-result snippet is analysed as the "bio"; no live candidate triggered a bio signal. | No real profile bios are collected. The official description is never passed to the scorer, so description-overlap points cannot occur (D9). |
| 5.6 | Cleared / false-positive list | PASS | both | Sample: 4 cleared candidates; live: 6 (names that only partly resemble the brand). `GET /api/detections/false-positives` and the False-positive guard card. | A *dismissed* detection is a human decision, not a cleared one; it stays in the list tagged Dismissed. |
| **6. Detections UI and API** | | | | | |
| 6.1 | List, filters, sort, pagination | PARTIAL | SAMPLE | API: category, type, platform, band, status (repeatable), source, free-text `q`, `min_score`, date range, 5 sorts, `limit` (max 200) and `offset`, tab counts by type/platform/status/band all correct. | The UI offers only the type tabs, platform chips and score-sorted list (max 200, no paging). The sidebar "Search detections" box does nothing (D10). |
| 6.2 | Detail panel, reasons, evidence, SHA-256 | PASS | SAMPLE + REAL | Panel shows official vs suspect, reasons with points, evidence URL, capture time and SHA-256. `GET .../evidence` returns `hash_verified: true`; after editing one byte of the stored snapshot it returned `false`; restored, `true` again. | Screenshots are off by default ("No screenshot"). |
| 6.3 | Review actions | PASS | SAMPLE + REAL | Confirm, keep reviewing, dismiss (with note) and mark as official work from the UI and API; invalid status 422; viewer 403; a marked-official detection cannot be changed (409). Each writes an audit row. | Marking a third-party APK listing official is refused on purpose. |
| 6.4 | Re-scan respects decisions | PASS | SAMPLE + REAL | After confirm / dismiss / reviewing / mark official, a re-scan kept every status (live: 2 protected, 21 updated, 0 new). | |
| 6.5 | Mark as official allowlists the asset | PASS | SAMPLE + REAL | The handle or package id is added to official accounts/apps (allowlist count 5 to 6, apps 1 to 2 to 3), shown under "Excluded as official", and excluded on the next scan. | Handles over 30 characters are refused. |
| 6.6 | Excluded list | PASS | both | `GET /api/detections/excluded`: 7 items with reason (`exact_handle`, `package_id`, `bundle_id`) and rule. | |
| **7. Dashboard** | | | | | |
| 7.1 | Numbers match the database | PASS | SAMPLE | Dashboard 16 total (11 social, 5 apps), bands 6/4/7 and by-platform counts equal direct SQL counts of open (new, reviewing, confirmed) detections; official assets 6 = 5 accounts + 1 app. | Dashboard counts exclude dismissed ones; the Social page list includes them (D15). |
| 7.2 | Sample badge | PASS | both | "Sample data" badge and footer note on every page in sample mode; absent on every page in live mode; reports carry a sample banner. | |
| **8. Look-alike Lab** | | | | | |
| 8.1 | Live matcher | PASS | n/a | More than 40 names tried (the 12 built-in plus about 30 more, listed in the user guide), with normalisation steps, match %, technique, reason. | The matcher ignores the platform when checking the allowlist. |
| 8.2 | Test-case runner | PASS | n/a | 12 of 12 pass with the standard Acme Bank profile. | A case such as `acmebank_official` fails if you register that handle as official. |
| 8.3 | Add / delete cases | PASS | n/a | Admin add 201, duplicate 409, delete 200 (unknown 404); analyst and viewer 403; run count updates (13 cases). | There is no "add case" box in the UI; use the API. |
| **9. Reports** | | | | | |
| 9.1 | Draft, preview, approve | PASS | SAMPLE | Draft 201 (HTML or CSV), preview list and printable preview, approve 200 once (second 409), viewer 403. UI: Generate report > Preview / Approve / Download / As CSV. | No PDF (422 with a message): print the HTML page to PDF. |
| 9.2 | CSV and HTML export | PASS | SAMPLE | CSV has reasons and evidence hashes; formula-looking handles are prefixed with an apostrophe; HTML sent as an attachment with a strict CSP. | |
| 9.3 | Nothing is sent | PASS | n/a | The code path only stores the draft and writes an audit row; no mail, webhook or upload exists. | |
| 9.4 | HTML escaping | PASS | SAMPLE | A report title `<script>alert(1)</script>` and a detection named `<img src=x onerror=alert(1)>` were rendered as text (`&lt;...`) in the HTML and CSV; CSP `default-src 'none'`. | |
| **10. Settings** | | | | | |
| 10.1 | Schedule, platforms, sensitivity, words, rules | PASS | n/a | All saved and still there after a reload (hourly, sensitivity 60, extra word, Telegram off). Out-of-range sensitivity 422; bad schedule 422; viewer 403. | Sensitivity only moves the bar for weaker evidence; a flagged name is always reviewed (see user guide). |
| 10.2 | Notifications | PASS (stored only) | n/a | Preferences and an https webhook URL are stored; `delivery: "not_implemented"`; non-https webhook 422. | Nothing is delivered. The UI says so. |
| 10.3 | Data mode switch | PASS | both | Account tab: Live / Sample / Auto; saved, audited. | |
| 10.4 | Audit export button | PASS | n/a | Account tab > Export Audit Log downloads `audit-log.csv`. | |
| **11. Evaluation and LLM** | | | | | |
| 11.1 | Evaluation page | PASS | synthetic | Per-technique precision/recall/F1 on generated names (753 true positives, 45 missed, overall recall 94.4%), the labelled look-alike tests and reviewer precision. A banner says synthetic numbers are optimistic. | Real precision only comes from reviewer decisions. |
| 11.2 | Optional AI scam check | PASS (off) | n/a | Off by default: no setting in the UI, `llm_checked: 0` on every scan, no AI key set, app works normally. | Enabling it (`SCAM_LLM_ENABLED=true` plus a Gemini key) is MOCK-ONLY. |
| **12. Audit log** | | | | | |
| 12.1 | Entries for mutations | PASS | n/a | 21 action types recorded (logins and failures, brand, logo, account, app, scan, detection decisions, report, settings, data mode, source toggle, canary, Look-alike cases). | |
| 12.2 | CSV export and filters | PASS | n/a | `GET /api/audit/export.csv`, `GET /api/audit` with actor/action/date filters; viewers 403. | No audit screen: only the CSV button and the API. Analysts can read it. |
| **13. Health and canary** | | | | | |
| 13.1 | `/health`, `/health/live`, `/api/health/details` | PASS | n/a | `ok`/`degraded` with one word per component; details (admin only) show migration `0004`, free disk, collector state. `degraded` appears when the collectors have not run yet. | |
| 13.2 | Canary endpoint and script | PASS | in-memory | `POST /api/health/canary` and `python -m scripts.canary`: 7 of 7 checks pass, exit code 0, about 150 ms. | Proves the pipeline logic only, not live sources. |
| **14. Pages** | | | | | |
| 14.1 | Every page loads without console errors | PASS | both | Login, Setup, Dashboard, Brand Profile, Social, App Store, Look-alike Lab, Source Health, Reports, Settings, Evaluation loaded in sample and live mode: no console errors or warnings, other than the expected 401/403/409/422 responses from the actions under test and the 404s below. | Before a brand exists the Dashboard logs two 404s (`/api/dashboard`, `/api/scan/status`). Screenshots are in `docs/img/`. |
| 14.2 | Server down message | PASS | n/a | With the API stopped the login page says "Cannot reach the server. Check your connection and try again." | An origin not in `CORS_ORIGINS` (for example `127.0.0.1` when only `localhost` is allowed) fails the same way. |

**Counts:** 65 rows. PASS 56 (a few with a qualifier such as "fixed", "API only" or "stored only"), PARTIAL 8, FAIL 1 (creating more users is not built). Nothing core to the detection flow failed once the SQLite lock bug (D1) was fixed.

## Live-run summary

Subject: one large consumer streaming brand, with 5 official handles (Instagram, X, Facebook, LinkedIn, YouTube) and its iOS app (bundle id confirmed from a live iTunes lookup). The Android package id was deliberately left out because it could not be confirmed from a live source. The official app icon from the iTunes API was used as the logo.

| Item | Result |
|---|---|
| Tavily credits | **32 spent in total, 2 over the 30 I allowed myself.** Scan 1 (8), scan 2 (8), a deliberate Retry of one source (8), and an unintended second Retry (8) because Instagram showed "slow", not "online", so the button ran a full query batch (D6). Application counter and the usage reported inside Tavily's responses both said 32. Tavily's own `/usage` call still showed 0 of 1000 a few minutes later (lag). A first scan that crashed on the SQLite lock spent 0. Bad-key and budget-guard tests spent 0. |
| Queries | 8 per scan, all brand-name plus lure word or alias plus official/support. No look-alike-spelling queries were reached. |
| iTunes | 8 searches per scan, country `in`, 73 apps each time, about 18 s per scan, no key. |
| Candidates | 92 found (89 unique): 73 App Store apps from iTunes, 19 from Tavily (9 Facebook, 1 X, 4 Google Play, 4 App Store, 1 APK mirror; non-profile URLs are dropped). 0 Instagram, 0 LinkedIn, 0 Telegram results. 63 were pre-filtered (no name resemblance), 25 evaluated. |
| Detections | 19: App Store 12, Facebook 5, Google Play 1, third-party APK 1. Types: 14 fake apps, 5 fake brand pages. 0 scam accounts, 0 fake profiles. |
| Bands | **High 0, medium 0, low 19** (scores 21 to 51). |
| Cleared (false-positive list) | 6 (name partly resembles the brand, no other signal). |
| Real impersonation-like candidates | **None seen.** The App Store hits were companion or statistics apps "for <brand>" by other publishers plus the brand's own other apps (same publisher, other app ids); the Facebook hits were news or article pages whose headline mentions the brand; the Google Play and APK hits were the brand's own genuine Android listing (not allowlisted because I left the id out) and an APK mirror page. |
| False-positive observations | (1) The brand's own iOS app, returned by Tavily with only a numeric id, was flagged (D3). (2) Other apps by the brand's own publisher (for example its creator and artist apps) scored because only the listed bundle id is official; they must be added or marked official. (3) Facebook names come from result headlines (D8). (4) A genuine company that is one letter away from the brand is flagged by design. |
| Logo comparison | Worked only for App Store icons (12 compared). Everything from Tavily had no image URL. |
| Failure handling | Bad key, budget exhausted, missing Google Play package, dead SearXNG address: each shown as unavailable with a reason, the scan still finished. |

What this says about Tavily discovery: it is a general web search limited to social and store domains. It surfaces pages that *mention* a brand. It did not find fake Instagram, X, LinkedIn or Telegram profiles at all for a large brand, so a quiet result is **not** proof that no impersonator exists. iTunes search is solid for the App Store but does not cover Google Play or APK sites.

## Defects found

| ID | Severity | Status | Summary and how to reproduce |
|---|---|---|---|
| D1 | High | **Fixed** (commit "Release the SQLite write lock before a collector runs") | First live scan on SQLite: Tavily reported `internal_error`, log "database is locked". Cause: the collector state row was created and left uncommitted while the Tavily credit counter wrote through another connection. Repro (before the fix): new database, key set, run a live scan. Test: `tests/core/collectors/test_orchestrator_sqlite_lock.py` (fails without the fix). |
| D2 | Medium | **Fixed** (commit "Allow hyphens in LinkedIn slugs and YouTube handles") | `POST /api/brands/1/accounts {"platform":"linkedin","handle":"acme-bank"}` returned 422, so a normal LinkedIn company slug could not be allowlisted. Tests added in `tests/api/test_accounts.py`. |
| D3 | Medium | **Fixed** (2026-10-14: optional numeric App Store id on the official app, plus per-scan learning from the iTunes copy; tests in `tests/api/test_official_app_store_id.py`) | The brand's own App Store app is flagged when it arrives from Tavily. Repro: register the iOS **bundle id** as official, run a live scan; the iTunes copy is excluded but the Tavily copy (URL gives only the numeric id) becomes a detection. Workaround: open that detection and press Mark as official (it stores the numeric id). Suggested fix: when an iTunes candidate with the same track id is excluded, exclude the Tavily copy too, or store the track id with the official app. |
| D4 | Medium | **Fixed** (2026-10-14: the same package on the second store is added instead of 409; API only, the form is unchanged) | An app on both stores cannot be registered in the UI: the form sends one store, the second add (same package id) returns 409 "already an official app". The API accepts `stores: ["google_play","app_store"]` in one call. A numeric App Store id is rejected by the validator. |
| D5 | Medium | **Fixed** (2026-10-14: the scheduler waits for the first official account or app; Auto=live is unchanged but explained) | Surprising defaults: the scheduler is on by default and starts a scan about a minute after the brand exists (before any official handle is added), and Data mode "Auto" is live whenever a Tavily key is in `.env`. With a key the first automatic scan spends credits. Repro: set the key, create a brand, wait. Mitigation: set `SCHEDULER_ENABLED=false` or switch to Sample before onboarding. |
| D6 | Medium | **Fixed** (2026-10-14: a paid retry needs confirmation and shows the credit cost (UI dialog, API `?confirm=true`)) | The Source Health "Retry now" button appears for *slow* sources and runs a whole Tavily query batch (8 to 12 credits) with no confirmation. I spent 8 credits by accident this way. |
| D7 | Low | Open | A source row shows Tavily's status for a platform that also has another collector, so Google Play's `not_installed` is hidden ("Responding slowly"); and a scan is called `partial` / "finished with some sources unavailable" when sources are merely slow. Tavily's 8 sequential queries take about 20 s, above the 4 s threshold, so a normal live scan always looks "slow". Raise `SOURCE_SLOW_THRESHOLD_MS` to about 30000 to calm it. |
| D8 | Medium | Open | Facebook candidates are named after the search-result **title** (a post or article headline) rather than the page name, so news pages that mention the brand get name-matched and flagged. Also, look-alike spelling queries are only reached after about two queries per alias, so with the default cap of 12 they are normally never run. |
| D9 | Low | Open | Official description text is never given to the scorer, so "copied description" signals (apps and social) cannot fire on live data. |
| D10 | Low | Open | Decorative controls: the sidebar "Search detections" box and the bell do nothing; "Support" opens an alert with a placeholder address; "Forgot password?" says it is not available. |
| D11 | Low | Open | Brand Profile: an invalid domain chip is accepted on screen, then Save fails for the **whole** form, and after a reload the other unsaved edits (industry, keywords) are gone. |
| D12 | Low | Open | The UI has no pagination, status filter, band filter or sort control though the API has them (the list is capped at 200). |
| D13 | Low | Open | No way to create viewer or analyst users from the app. E-mail addresses on reserved domains (`.test`, `.local`) are rejected at login (422). |
| D14 | Low | Open | Access tokens keep working after logout until they expire (60 minutes in the tested `.env`, 15 by default). |
| D15 | Low | Open | Dashboard totals count new, reviewing and confirmed only; the Social and App pages also list dismissed items, so the numbers differ by the dismissed count. |
| D16 | Info | Open | The Look-alike Lab checks the allowlist without the platform, and treats an alias as "the official name" (shows Official for the alias itself). Built-in case `acmebank_official` fails if you register that handle. |
| D17 | Info | Open | Mark as official refuses handles over 30 characters although LinkedIn slugs may now be up to 100. |
| D19 | Low | Open | Settings > Detection sensitivity: the label reads "Send to review when risk score is at least 75" but the real minimum score is 100 minus the slider (25 at 75). Reproduce: slider at 75, `GET /api/settings` shows `threshold: 25`. |
| D18 | Info | Open | The credit counter is per database. A second database (or another machine) using the same key has its own counter. |

## What is still unverified

* A real Google Play search (the optional package is not installed and its licence is unreviewed) and a working SearXNG instance.
* Redis / RQ worker mode, PostgreSQL, Docker images and the production deployment notes. Only SQLite with the in-process thread runner was run.
* Browser screenshots of a suspect profile (the Playwright screenshot hook is off and the package is not installed).
* The optional AI scam check when enabled; e-mail, digest and webhook delivery (not built).
* Credit-aware scheduler stretching/pausing on live data, and back-off steps beyond the first (the 5 and 15 minute waits were not sat through).
* Silent token refresh in the browser after the access token expires, and the two-tabs-refresh-at-once sign-out the docs warn about.
* Whether fake Instagram, X, LinkedIn or Telegram profiles exist for the test brand: Tavily returned none, so the social side of detection has only been exercised on sample data and unit tests.
* Mobile layout and accessibility. Only a 1360 px wide desktop browser was used.
* The `Acme Bank` sample brand shows how the product behaves; sample scores say nothing about real accuracy.
