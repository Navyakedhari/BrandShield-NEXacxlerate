# ScamMind AI: API Specification (derived from the frontend contract)

Source of truth for screens: `ScamMind AI Screens.pdf` (10 screens). Frontend facts: `docs/FRONTEND_CONTRACT.md`.
This started as a **proposed** contract. Phases 1, 3 and 4 have since built most of it: sections titled "Phase N implementation notes" (section 9 for sources, **section 18 for everything in Phase 4**) are authoritative and override anything older in this file when they differ. Legend used on every endpoint:

- **[CALLED]** = the frontend already calls this URL today (in `frontend/src/api/*.ts`).
  Important: of the 10 routed pages only Login calls the API. Most [CALLED] endpoints below are called only by the **orphaned** old-product pages or by `api/client.ts` itself. Each is labelled accordingly.
- **[NEW]** = needed by the PDF screens, not called by any frontend code today (the 10 pages currently use `localStorage`).
- **[EXISTS]** = a router of the same path already exists in `backend/app/routers` (name only; shape is old-product and may differ). See section 16.
- Fields marked `sample` follow the sample-data rules in section 2.

---

## 1. Conventions

- Base URL: `VITE_API_URL` (default `http://127.0.0.1:8000`). All paths start with `/api`.
- JSON, UTF-8. Timestamps ISO-8601 UTC with `Z` (`"2026-10-12T11:58:00Z"`). The UI formats them (e.g. "12 Oct, 11:58 AM"). Durations as integers.
- `brand_id`: collection endpoints accept optional `?brand_id=`; default is `me.default_brand_id`. Single brand per org assumed (open question).
- Pagination (list endpoints that can grow): `?limit=50&offset=0` (max limit 200); response includes `total`.
- Enum wire values are **lowercase snake_case**; the UI maps them to labels (tables in section 3).
- IDs: integers for brands/accounts/apps/sources/reports; detections use string ids `det_<n>` or integers (pick one; examples use integers). Frontend store today uses strings like `det-1`; any opaque id works.

### Auth

Bearer JWT. `Authorization: Bearer <access_token>` on every call except those marked anonymous. Short-lived access token (frontend copy: "Sessions use short-lived tokens"), refresh token rotates.
Roles: `admin | analyst | viewer`. Viewers: GET only. Review actions (status, mark official, scan, brand edits, settings, reports) need `analyst` or `admin`.

### Error format (what `api/client.ts` parses)

```json
{ "detail": "Incorrect email or password" }
```
or FastAPI validation (HTTP 422):
```json
{ "detail": [ { "loc": ["body","email"], "msg": "value is not a valid email address", "type": "value_error.email" } ] }
```
The client uses `detail` if string, else joins `detail[].msg` with `"; "`. **Every** error must carry `detail`. Status codes: 400 bad input, 401 missing/expired token (client auto-refreshes once, then redirects to `/login`), 403 role, 404, 409 conflict (duplicate), 413 file too large, 415 bad file type, 422 validation, 429 rate limit, 502/503 upstream.
Network failure (no HTTP response) is handled client-side as "Cannot reach the server...".

---

## 2. Sample-data rules

- Every record that is seeded demo content has `"source": "sample"`. Real records have `"source": "live"`. Applies to: detections, sources, source events, official accounts/apps (seeded Acme assets), reports/exports, test cases, audit entries.
- Org-level `data_mode` in Settings: `"live" | "sample"`.
  - `sample`: GET lists return only sample records; scan endpoints produce sample records and never contact real platforms; every list/aggregate response includes `"data_mode": "sample"` so the UI shows the footer "Sample data for the fictional brand Acme Bank. Not real detections."
  - `live`: only `source: "live"` records returned; footer hidden. Sources that cannot be reached report `unavailable` (never fall back silently to sample).
- Aggregate responses (dashboard, reports preview) carry `"data_mode"` and `"includes_sample": bool`.
- Seed (fictional brand Acme Bank) is the 11 detections, 8 sources, 5 accounts, 2 apps, 5 events, 12 test cases listed in the contract; reproduced in section 15 for the seeder.

---

## 3. Enums

| Name | Wire values | UI label |
|---|---|---|
| `role` | admin, analyst, viewer | |
| `detection.type` | `fake_profile`, `fake_brand_page`, `scam_account`, `fake_app` | Fake profile, Fake brand page, Scam account, Fake app |
| `detection.category` | `social`, `apps` | derived: `fake_app` => apps |
| `platform` | `instagram`, `facebook`, `x`, `telegram`, `linkedin`, `youtube`, `google_play`, `app_store`, `third_party_apk` | Instagram, Facebook, X, Telegram, LinkedIn, YouTube, Google Play, App Store (source name: "Apple App Store"), Third-party APK (source name: "Third-party APK sites") |
| detection platforms | all except `youtube` (youtube only appears for official accounts) | |
| official account platform | instagram, x, facebook, linkedin, youtube | |
| `detection.status` | `new`, `reviewing`, `confirmed`, `dismissed`, `marked_official` (+ internal `cleared`, never listed) | New, Reviewing, Confirmed, Dismissed, Marked official |
| `risk_band` | `high` (score >= 85), `medium` (70 to 84), `low` (< 70) | High / Medium / Low. **Proposed**; PDF only shows "High". |
| `account.status` | `verified`, `unreachable` | Verified, Unreachable |
| `source.status` | `online`, `slow`, `unavailable` | Online, Slow, Unavailable |
| `source.kind` | `social`, `store` | |
| `scan.scope` | `all`, `social`, `apps` | |
| `job.state` | `queued`, `running`, `done`, `error` (kept for the UI). The richer `scan.status` is `queued`, `running`, `completed`, `partial`, `failed` | |
| `lookalike.verdict` | `flagged`, `not_flagged`, `official` | Flagged, Not flagged, Official |
| `export.format` | `csv`, `html` (printable). `pdf` is refused with 422: no PDF library | CSV data, Printable report |
| `data_mode` | `live`, `sample` | |
| `scan_schedule` | `off`, `5m`, `15m`, `1h`, `1d` (`off` added in Phase 4) | Every 5 minutes, Every 15 minutes, Hourly, Daily |

---

## 4. Auth

### POST /api/auth/login  [CALLED: LoginPage via `authApi.login`] [EXISTS]  anonymous
Request:
```json
{ "email": "admin@company.com", "password": "..." }
```
Response 200:
```json
{
  "access_token": "eyJ...", "refresh_token": "eyJ...", "token_type": "bearer", "expires_in": 900,
  "user": { "id": 1, "name": "Harini", "email": "admin@company.com", "role": "admin", "active": true, "default_brand_id": 1 }
}
```
Errors: 401 `{"detail":"Incorrect email or password"}`, 429 rate limited. The frontend must show this message on the Login page (today it swallows it and starts an offline session).
`persist` (keep me signed in) is client-only (localStorage vs sessionStorage); the server does not need it. The client expects `user.default_brand_id` in the login payload (it reads `ApiUser.default_brand_id`).

### POST /api/auth/refresh  [CALLED: client.ts on any 401] [EXISTS]  anonymous
Request `{ "refresh_token": "..." }`. Response: same shape as login (new access and refresh tokens, rotate). 401 on invalid/expired => client logs out.

### POST /api/auth/logout  [CALLED: `authApi.logout`, not triggered by the current Header] [EXISTS]  anonymous
Request `{ "refresh_token": "..." }`. Response `{ "ok": true, "detail": "" }`. Revokes the refresh token. Audit entry `auth.logout`.

### GET /api/me  [CALLED: `authApi.me`, `brandId()`] [EXISTS]
Response: `{ "id":1,"name":"Harini","email":"admin@company.com","role":"admin","active":true,"default_brand_id":1 }`. Header chip should use this instead of the hard-coded "Harini / admin@company.com".

### POST /api/auth/forgot  [CALLED: `authApi.forgot`, no page uses it] [EXISTS]  anonymous
Request `{ "email": "..." }` Response `{ "ok": true, "detail": "If the account exists, a reset link was sent." }` (always 200).

### POST /api/auth/reset  [CALLED: `authApi.reset`, no page] [EXISTS]  anonymous
Request `{ "token": "...", "new_password": "..." }` (min 8). Response `{ "ok": true, "detail": "Password updated" }`. No reset UI exists yet.

### POST /api/auth/register  [NEW for Setup page: "New organization? Set up your brand profile"] [EXISTS in backend, old shape]  anonymous
Creates the organization, first admin user and an empty brand so the Setup wizard can run.
Request:
```json
{ "name": "Harini", "email": "admin@company.com", "password": "...", "organization": "Acme Bank" }
```
Response 201: same token payload as login (user.default_brand_id set to the new brand). 409 if email exists.

---

## 5. Brand profile and assets (Brand Profile page, Setup page)

### GET /api/brands/{brand_id}  [CALLED only by orphan InvestigatePage via `GET /api/brands` list] [EXISTS]
Response:
```json
{
  "id": 1, "name": "Acme Bank", "industry": "Banking and financial services",
  "aliases": ["Acme","AcmeBank","Acme Bank India"],
  "keywords": ["acme","acmebank","#AcmeBank"],
  "domains": ["acmebank.com","acmebank.in"],
  "logo": { "url": "/api/brands/1/logo", "format": "png", "width": 512, "height": 512, "size_bytes": 86016, "square": true, "meets_requirements": true },
  "official_accounts": [ /* see 5.3 */ ],
  "official_apps": [ /* see 5.5 */ ],
  "profile_health": { "percent": 86, "message": "Good coverage. Fix one unreachable URL to reach 100%.",
    "checks": [ {"key":"name_aliases","label":"Brand name and aliases","ok":true},
                {"key":"logo","label":"Official logo meets requirements","ok":true},
                {"key":"domains","label":"Official domains added","ok":true},
                {"key":"apps_publisher","label":"Apps include publisher names","ok":true},
                {"key":"urls_reachable","label":"YouTube URL could not be reached","ok":false} ] },
  "allowlist": { "accounts": 5, "apps": 2, "domains": 2 },
  "official_assets": 7,
  "updated_at": "2026-10-12T09:12:00Z", "source": "sample"
}
```
`GET /api/brands` [CALLED by orphan Investigate] returns the list `[{id,name}]` (frontend fallback shows `[{id:1,name:"Acme Bank"}]`).
`profile_health` and `allowlist` are computed server-side (currently hard-coded in `BrandProfilePage.tsx`). `official_assets = accounts + apps`, shown in the sidebar and the dashboard KPI.

### PUT /api/brands/{brand_id}  [NEW for Brand Profile "Save brand profile"] [EXISTS]
Request (identity section only):
```json
{ "name":"Acme Bank","industry":"Banking and financial services",
  "aliases":["Acme","AcmeBank"],"keywords":["acme","#AcmeBank"],"domains":["acmebank.com"] }
```
Response: full brand object (5 above). Rules: name required (1 to 120), de-duplicate lists, domains validated and lowercased, `#` allowed in keywords. Audit `brand.updated` (the Source Health feed shows "Brand profile saved, allowlist refreshed"). Applies from the next scan.

### POST /api/brands  [NEW for Setup step 1] [EXISTS]
Request `{ "name":"Acme Bank","aliases":["Acme","AcmeBank","Acme Bank India"] }` Response 201 brand object. (Setup sends only name and aliases.)

### Logo upload

`POST /api/brands/{brand_id}/logo`  [NEW; the frontend "Replace logo" and Setup "Replace" buttons are `alert()` stubs]
multipart/form-data, field `file`. Accepted: PNG or SVG, max 2 MB (2,097,152 bytes), square. Recommended 512x512 px. Validation errors:
- 413 `{"detail":"Logo is larger than 2 MB"}`; 415 `{"detail":"Logo must be a PNG or SVG"}`; 422 `{"detail":"Logo must be square"}` (or accept and return `meets_requirements:false`).
Response:
```json
{ "url": "/api/brands/1/logo", "format": "png", "width": 512, "height": 512, "size_bytes": 86016, "square": true, "meets_requirements": true, "sha256": "..." }
```
Server derives perceptual hash for logo/icon matching. `GET /api/brands/{brand_id}/logo` returns the image (auth required, or signed URL for `<img>`).
`DELETE` not needed.

### 5.3 Official social accounts

`GET /api/brands/{brand_id}/accounts` [NEW]  Response:
```json
{ "total": 5, "items": [
  { "id": 1, "platform": "instagram", "handle": "@acmebank", "url": "instagram.com/acmebank", "status": "verified", "last_checked_at": "2026-10-12T09:12:00Z", "source": "sample" },
  { "id": 2, "platform": "x", "handle": "@AcmeBank", "url": "x.com/AcmeBank", "status": "verified", "source": "sample" },
  { "id": 3, "platform": "facebook", "handle": "acmebank", "url": "facebook.com/acmebank", "status": "verified", "source": "sample" },
  { "id": 4, "platform": "linkedin", "handle": "company/acmebank", "url": "linkedin.com/company/acmebank", "status": "verified", "source": "sample" },
  { "id": 5, "platform": "youtube", "handle": "@acmebank", "url": "youtube.com/@acmebank", "status": "unreachable", "source": "sample" } ] }
```
`POST /api/brands/{brand_id}/accounts` [NEW] request `{ "platform":"instagram", "handle":"acmebank" }`. Server normalises the handle (prefix `@` except facebook and linkedin) and builds the URL (`instagram.com/`, `x.com/`, `facebook.com/`, `linkedin.com/company/`, `youtube.com/@`), then verifies reachability to set `status` (`verified` or `unreachable`; the frontend today always says Verified). 201 returns the item. 409 if platform+handle already exists. Audit `account.added`.
`DELETE /api/brands/{brand_id}/accounts/{id}` [NEW] 200 `{"ok":true}`. Audit `account.removed`. Matching is by exact handle and URL, never name alone.

### 5.5 Official apps

`GET /api/brands/{brand_id}/apps` [NEW] items:
```json
{ "id": 1, "name": "Acme Bank Mobile", "package_id": "com.acmebank.mobile", "publisher": "Acme Bank Ltd", "stores": ["google_play","app_store"], "status": "verified", "source": "sample" }
{ "id": 2, "name": "Acme UPI", "package_id": "com.acmebank.upi", "publisher": "Acme Bank Ltd", "stores": ["google_play"], "status": "verified", "source": "sample" }
```
`POST /api/brands/{brand_id}/apps` [NEW] request `{ "name":"Acme Pay","package_id":"com.acme.pay","publisher":"Acme Bank Ltd","stores":["google_play"] }` (name, package_id, publisher required; stores subset of `google_play`, `app_store`; the UI today sends one store). 201 item; 409 duplicate package_id. Audit `app.added`.
`DELETE /api/brands/{brand_id}/apps/{id}` [NEW]. Audit `app.removed`.
`POST /api/brands/{brand_id}/apps/import` [NEW; button "Import from store listing URL" is a stub] request `{ "url": "https://play.google.com/store/apps/details?id=com.acmebank.mobile" }` response `{ "name":"...","package_id":"...","publisher":"...","stores":["google_play"],"icon_url":"..." }` (prefill only, not saved). 422 for unsupported URL, 502 if store unreachable.

---

## 6. Scans (Dashboard "Run scan now", Social "Scan social platforms now", Apps "Scan app stores now", Setup step 3)

### POST /api/scan  [NEW for these pages; old `POST /api/scan` exists] [EXISTS]
Request:
```json
{ "scope": "all", "brand_id": 1 }
```
`scope`: `all` (Dashboard, Setup), `social` (Social page), `apps` (App Store page). 202:
```json
{ "job_id": "job_8f2c1a" }
```
409 if a scan for that brand+scope is already running (`{"detail":"A scan is already running","job_id":"job_8f2c1a"}`). In `data_mode=sample` the job produces sample results only.

### GET /api/jobs/{job_id}  [NEW for these pages; EXISTS]  (poll every ~1 s until `done` or `error`)
```json
{
  "id": "job_8f2c1a", "kind": "scan", "scope": "social", "state": "running", "progress": 60,
  "message": "Checking Telegram", 
  "steps": [ {"source":"instagram","state":"done","checked":214,"new_detections":2},
             {"source":"telegram","state":"running","checked":41,"new_detections":0} ],
  "result": null
}
```
On `done`:
```json
{ "id":"job_8f2c1a","state":"done","progress":100,"message":"Scan finished",
  "result": { "sources_checked": 8, "sources_online": 6, "accounts_or_apps_checked": 486, "new_detections": 2, "partial_sources": ["telegram"], "unavailable_sources": ["app_store"], "finished_at": "2026-10-12T11:58:00Z" } }
```
The UI messages to drive today's alerts: "Scan complete! 8 sources queried." (Dashboard) and "{n} accounts analyzed. {k} new threats." (Social). A source being slow/unavailable does **not** fail the job (PDF: "The scan still finishes").

### GET /api/scan/status  [NEW]
`{ "last_scan_at":"2026-10-12T11:58:00Z","last_full_sync_at":"2026-10-12T12:05:00Z","next_scan_at":"2026-10-12T12:10:00Z","schedule":"5m","running_job_id":null }`
Feeds Dashboard hero line ("Last scan 12 Oct 2026, 11:58 AM · 6 of 8 sources online" with sources counts from /sources) and Source Health "Next scan 12:10".

---

## 7. Detections (Social page, App Store page, drill-downs)

### GET /api/detections  [NEW]
Query: `category=social|apps`, `type=fake_profile|fake_brand_page|scam_account|fake_app`, `platform=<slug>`, `status=<status>` (repeatable), `min_score`, `q` (name/handle), `sort=score_desc` (default, risk) | `found_desc`, `from`, `to` (ISO date, by `found_at`), `limit`, `offset`, `brand_id`.
Response (list rows are summaries; counts drive the tab labels "All 6 / Fake profiles 3 / ..." and "All stores 5 / Google Play 3 / ..."):
```json
{
  "total": 6, "data_mode": "sample",
  "counts": { "all": 6, "by_type": {"fake_profile":3,"fake_brand_page":1,"scam_account":2,"fake_app":0},
              "by_platform": {"instagram":2,"facebook":1,"x":2,"telegram":1} },
  "items": [
    { "id": 1, "category": "social", "type": "scam_account", "platform": "instagram",
      "name": "Acme Bank Support Care", "handle": "@acrne_bank_support", "package_id": null, "publisher": null,
      "score": 94, "risk_band": "high", "status": "new",
      "summary": "“rn” reads as “m”, added word “support”",
      "found_at": "2026-10-12T11:58:00Z", "stale": false, "source": "sample" }
  ]
}
```
Notes: `name` + `handle`/`package_id` replace the frontend's `name` + `handleOrPackage`. Dashboard "Recent detections" uses `sort=found_desc&limit=6`; "Needs review" uses `status=new&status=reviewing&min_score=85&limit=5`. Cached rows (App Store unreachable) carry `"stale": true, "cached_at": "..."`. Social tab default must be sorted by score desc (PDF claims "Sorted by risk").

### GET /api/detections/{id}  [NEW]
Detail for the right-hand panel. Social example:
```json
{
  "id": 1, "category": "social", "type": "scam_account", "platform": "instagram",
  "name": "Acme Bank Support Care", "handle": "@acrne_bank_support", "profile_url": "instagram.com/acrne_bank_support",
  "score": 94, "risk_band": "high", "status": "new", "found_at": "2026-10-12T11:58:00Z",
  "tags": ["“rn” reads as “m”","Added word: support","Logo 91% match","Bio asks for KYC"],
  "summary": "...",
  "official": { "name":"Acme Bank","handle":"@acmebank","bio":"Official page of Acme Bank. We will never ask for your PIN, OTP or KYC details over DM.","followers":"2.4M","joined":"2014","link":"acmebank.com","on_allowlist":true },
  "suspect": { "bio":"Official Acme Bank support. DM us your KYC details to avoid account block.","followers":"214","joined":"12 days ago","link":"acme-kyc-update.in","logo_match_percent":91 },
  "reasons": [ {"signal":"Name similarity","detail":"“acrne” reads as “acme” (rn → m)","points":34},
               {"signal":"Added word","detail":"“support”","points":10},
               {"signal":"Logo match","detail":"91% match with the official logo","points":25},
               {"signal":"Bio content","detail":"Asks for KYC details in DMs","points":17},
               {"signal":"Account signals","detail":"12 days old, 214 followers","points":8} ],
  "evidence": { "screenshot_url":"/api/detections/1/evidence/screenshot","page_url":"instagram.com/acrne_bank_support","captured_at":"2026-10-12T11:58:00Z","sha256":"9f3a8b27c1d4e5f60718293a4b5c6d7e8f901a2b3c4d5e6f7a8b9c0d1e2f3a4b" },
  "history": [ {"at":"2026-10-12T11:58:00Z","actor":"system","from":null,"to":"new"} ],
  "source": "sample"
}
```
Reasons: the frontend renders `label` as `"{signal} · {detail}"` and `+{points}`; sum of points equals `score` in the seed. The frontend currently stores a single string `label`; the split is recommended but a single `label` is acceptable if the owner prefers.
App variant adds (instead of `official`/`suspect` bios):
```json
{ "type":"fake_app","platform":"google_play","name":"Acme Bank Support Help","package_id":"com.acme.support.help","publisher":"Quick Tools Studio",
  "publisher_differs": true, "official_publisher":"Acme Bank Ltd",
  "official": { "name":"Acme Bank Mobile","package_id":"com.acmebank.mobile","publisher":"Acme Bank Ltd","stores":["google_play","app_store"],"downloads":"10M+","on_allowlist":true },
  "suspect": { "stores":["google_play"],"downloads":"500+","released":"6 days ago","icon_match_percent":93 },
  "description": { "overlap_percent":88, "snippet":"Secure mobile banking support from Acme Bank. Check balance, transfer funds and raise a support ticket.", "matched_phrases":["secure mobile banking","check balance","transfer funds"] } }
```
`publisher_differs` replaces the frontend's always-on banner (today shown even if the publisher matches).
`GET /api/detections/{id}/evidence/screenshot` [NEW] returns the stored image (auth). `GET /api/detections/{id}/evidence` [NEW] optional full bundle (URL, listing details, hashes).

### POST /api/detections/{id}/status  [NEW]  (four review buttons; person required, no auto takedown)
Request:
```json
{ "status": "confirmed", "note": "optional" }
```
`status` one of `reviewing` ("Keep reviewing"), `confirmed` ("Confirm impersonation" / "Confirm fake app"), `dismissed` ("Dismiss as false positive"). `new` cannot be set by a user. Response: updated detail object (7 above) plus `"audit_id": 123`. Writes an audit entry (`detection.status_changed`, target = handle or package id, detail `from -> to`) and updates dashboard counts. 409 if the detection is already `marked_official` and the transition is not allowed (define rule). Viewer role gets 403.

### POST /api/detections/{id}/mark-official  [NEW]  ("Mark as official")
Request `{ "note": "optional", "publisher": "Acme Bank Ltd" }` (publisher only used for apps; frontend defaults to 'Acme Bank Ltd'). Effects: detection `status = marked_official`; a new allowlist record is created (account for `social`: platform + handle + url from evidence, `status:"verified"`; app for `apps`: name, package_id, publisher, `stores:[platform]`). Response:
```json
{ "detection": { /* detail object */ }, "allowlist_entry": { "kind": "account", "id": 6 }, "official_assets": 8 }
```
Audit `detection.marked_official` + `account.added`/`app.added`. Future scans skip it (exact handle/package match).

Optional [NEW]: `POST /api/detections/{id}/reopen` (undo to `reviewing`); the UI has no undo today.

### GET /api/detections/excluded  [NEW]  ("Excluded as official")
Query `category=social|apps`. Matches the allowlist items that scans skipped, not scored:
```json
{ "items": [ { "kind":"account","platform":"instagram","label":"@acmebank on Instagram","status":"Official · not flagged","reason":"exact_handle" },
             { "kind":"app","label":"Acme Bank Mobile · com.acmebank.mobile","sub":"Acme Bank Ltd · Google Play, App Store","reason":"package_id_publisher" } ] }
```
Social page shows 5 (all accounts incl. YouTube); Apps page shows 2 (apps). Report stat "Official, excluded 7" = this total.

### GET /api/detections/false-positives  [NEW]  ("False-positive guard")
Query `category`. Look-alike names cleared automatically (never entered the queue):
```json
{ "items": [
  { "name":"Acme Bakery","platform":"facebook","kind":"page","category":"Bakery","logo_match_percent":6,"reason":"Different category, logo 6% match","status":"not_flagged","source":"sample" },
  { "name":"Acme Hotels","platform":"instagram","category":"Hospitality","logo_match_percent":4,"status":"not_flagged" },
  { "name":"@acme_weather","platform":"x","category":"Weather app","logo_match_percent":3,"status":"not_flagged" },
  { "name":"Acme Weather","package_id":"com.acme.weather","publisher":"Acme Corp","icon_match_percent":8,"description_match_percent":4,"status":"not_flagged" },
  { "name":"Acme Fitness Tracker","package_id":"com.acmefit.app","publisher":"Acme Fit Pvt Ltd","icon_match_percent":5,"description_match_percent":2,"status":"not_flagged" } ] }
```
UI shows the social ones on Social page, app ones on Apps page: filter via `category`.

---

## 8. Dashboard

### GET /api/dashboard  [CALLED by `dashboardApi` (orphaned, old shape); NEW shape needed by the live page] [EXISTS, old shape]
Query `brand_id`. Response:
```json
{
  "data_mode": "sample", "brand": {"id":1,"name":"Acme Bank"},
  "last_scan_at": "2026-10-12T11:58:00Z", "sources_online": 6, "sources_total": 8,
  "kpis": {
    "total_detections":      { "value": 11, "delta_week": 4, "trend": [4,6,8,7,9,10,11] },
    "social_impersonations": { "value": 6,  "delta_week": 2, "trend": [2,3,3,4,5,5,6] },
    "suspicious_apps":       { "value": 5,  "delta_week": 3, "trend": [1,2,2,3,3,4,5] },
    "official_assets":       { "value": 7 }
  },
  "by_type":     [ {"type":"fake_app","count":5,"percent":45}, {"type":"fake_profile","count":3,"percent":27}, {"type":"scam_account","count":2,"percent":18}, {"type":"fake_brand_page","count":1,"percent":10} ],
  "by_platform": [ {"platform":"google_play","count":3}, {"platform":"instagram","count":2}, {"platform":"x","count":2}, {"platform":"facebook","count":1}, {"platform":"telegram","count":1}, {"platform":"app_store","count":1}, {"platform":"third_party_apk","count":1} ],
  "needs_review": [ { "id":1,"name":"@acrne_bank_support","platform":"instagram","type":"scam_account","found_at":"2026-10-12T11:56:00Z","risk_band":"high" } ],
  "recent": [ { "id":1,"name":"@acrne_bank_support","summary":"“rn” reads as “m”, added word “support”","platform":"instagram","type":"scam_account","score":94,"status":"new","found_at":"2026-10-12T11:58:00Z" } ],
  "top_detection": { "id":1,"name":"@acrne_bank_support","platform":"instagram","score":94,"on_allowlist":false,"tags":["“rn” reads as “m”","Added word: support","Logo 91% match","Bio asks for KYC"] },
  "sources": [ {"id":1,"name":"Instagram","status":"online"}, {"id":4,"name":"Telegram","status":"slow"}, {"id":7,"name":"Apple App Store","status":"unavailable"} ]
}
```
Percentages sum 100 (45+27+18+10 in the PDF). "Recent detections" shows the first 6 (PDF). Donut center = `total_detections.value`. `by_platform` max for bar widths is computed by the UI (today hard-coded 4). `needs_review` relative time ("2 minutes ago") computed client-side from `found_at`. Dashboard click-through links to Social or Apps pass `?detection=<id>` (new UI behaviour).
Today's `GET /api/dashboard?brand_id=` response (`kpis[], states[], categories[], campaigns[], alerts[], recent[], ai_summary`) is the old product's and does not match.

---

## 9. Sources and Source Health

### GET /api/sources  [NEW]
```json
{
  "data_mode": "sample",
  "summary": { "online": 6, "slow": 1, "unavailable": 1, "total": 8, "next_scan_at": "2026-10-12T12:10:00Z", "schedule": "5m", "last_full_sync_at": "2026-10-12T12:05:00Z", "retry_policy": "1m,5m,15m,hourly" },
  "items": [
    { "id": 1, "slug": "instagram", "name": "Instagram", "kind": "social", "description": "Public profile and page search",
      "status": "online", "monitored": true, "message": "Working normally.", "last_sync_at": "2026-10-12T11:58:00Z",
      "checked": 214, "checked_unit": "profiles", "checked_of": null, "detail": "214 profiles checked", "retry_in_minutes": null, "stale_since": null, "source": "sample" },
    { "id": 4, "slug": "telegram", "name": "Telegram", "kind": "social", "description": "Public channels and groups",
      "status": "slow", "monitored": true, "message": "Rate limited. Partial results are kept. Retrying automatically in 4 minutes.",
      "last_sync_at": "2026-10-12T11:31:00Z", "checked": 41, "checked_of": 120, "detail": "41 of about 120 checked", "retry_in_minutes": 4 },
    { "id": 7, "slug": "app_store", "name": "Apple App Store", "kind": "store", "description": "Store search and listing details",
      "status": "unavailable", "monitored": true, "message": "The store could not be reached. Showing cached results from 9:40 AM. They are marked as stale on the app page.",
      "last_sync_at": "2026-10-12T09:40:00Z", "detail": "Cached results shown", "stale_since": "2026-10-12T09:40:00Z" }
  ]
}
```
Remaining seeded sources: facebook (96 pages checked, 11:55), x (138 profiles, 11:57), linkedin (22 pages, 11:50), google_play (310 apps, 11:58), third_party_apk (58 listings, 11:40, message "Working normally. Coverage is limited to known mirror sites."). `slug` equals the platform enum. Used by Dashboard mini card (first 6), Social sources bar (kind=social), Apps sources bar (kind=store), Source Health.
Query `kind=social|store` optional.

### POST /api/sources/{id}/retry  [NEW]  ("Retry now", shown when status is slow or unavailable; also the App Store "Retry" link)
Response 202:
```json
{ "job_id": "job_41aa", "source_id": 4 }
```
Poll `GET /api/jobs/{job_id}`; on done the source status/`last_sync_at`/`detail` update and an event is appended. 409 if source is not retryable (online) or a retry is running. In `sample` mode the retry may flip status per a script or stay the same.

### PATCH /api/sources/{id}  [NEW]  ("Monitor" checkbox)
Request `{ "monitored": false }` Response: the source object. Monitored sources are the ones scanned; this is the same switch as `Settings > monitored platforms` and they must stay in sync (today they are two separate local copies). Audit `source.monitor_toggled`.

### GET /api/sources/events  [NEW]  ("Recent source events")
Query `limit=5`.
```json
{ "items": [
  { "id": 1, "at": "2026-10-12T11:58:00Z", "source": "instagram", "message": "Instagram scan finished, 2 new detections", "level": "info", "data_source": "sample" },
  { "id": 2, "at": "2026-10-12T11:31:00Z", "source": "telegram",  "message": "Telegram rate limited, partial results kept", "level": "warning" },
  { "id": 3, "at": "2026-10-12T11:42:00Z", "source": "google_play","message": "Google Play scan finished, 1 new detection" },
  { "id": 4, "at": "2026-10-12T09:41:00Z", "source": "app_store",  "message": "Apple App Store unreachable, using cached results", "level": "error" },
  { "id": 5, "at": "2026-10-12T09:12:00Z", "source": null,         "message": "Brand profile saved, allowlist refreshed" } ] }
```
(In this object `source` is the platform slug, so the sample/live marker is named `data_source`.) Keep separate from the user audit log (section 12).

### Phase 3 implementation notes (collectors; implemented in `backend/app/api/sources.py`)

These endpoints exist now. Where this differs from the proposal above, **this section wins**.

**GET /api/sources** (any signed-in role; `?kind=social|store`). Same item fields as above plus:
`next_retry_at` (ISO or null), `cached_at`, `cached_age_seconds` (age of the last good result being shown when the source is unavailable), `collectors` (names of the collectors that cover this platform in the current mode), `stale_since`. `retry_in_minutes` is derived from `next_retry_at`. Top level adds:
`requested_data_mode` (`live|sample|auto`), `data_mode` (the **effective** mode, `live|sample`), `summary.retry_policy` = `"1m,5m,15m,1h,6h"` (back-off, capped at 6 h, resets on success), and the credit meter:
```json
"tavily_credits": { "provider": "tavily", "month": "2026-10", "used": 12, "budget": 900, "remaining": 888, "reported_used": 12 }
```
`used` is the larger of our own counter and the usage Tavily reports. A row that has never been checked in the current mode is shown as `online` with message "Not checked yet." (or `unavailable` with the reason when no live collector covers it); rows written under the other data mode are not presented as current. `next_scan_at` and `schedule` were `null` in Phase 3; since Phase 4 they carry the scheduler values (section 18.2).

**PATCH /api/sources/{id}** `{ "monitored": bool }` (analyst, admin). Returns the item. Audit `source.monitor_toggled` (`monitored=true|false`) only when the value changes. Unmonitored platforms are not queried and their candidates are dropped.

**POST /api/sources/{id}/retry** (analyst, admin). Runs the collectors that cover this platform now, ignoring back-off. **Synchronous in Phase 3** (Phase 4 turns it into a job): returns 202 with
`{ "job_id": "retry_<hex>", "source_id": 4, "state": "done", "status": "online|slow|unavailable", "source": { ...item }, "reports": [ { "collector","status","reason","message","latency_ms","candidates","from_cache","cached_age_seconds","next_retry_at","consecutive_failures","in_backoff","warnings","data_source" } ] }`.
409 when the source is online (nothing to retry), not monitored, no brand profile exists yet, or no collector is active for it. Audit `source.retry`. `GET /api/jobs/{job_id}` does not exist yet.

**GET /api/settings/data-mode** (any signed-in role) and **PUT /api/settings/data-mode** (admin only) `{ "data_mode": "live|sample|auto" }`. Response of both:
```json
{ "data_mode": "auto", "effective_mode": "live", "reason": "Auto: live because configured: Tavily key.",
  "chain": [ { "name": "tavily", "active": true, "configured": true, "reason": "Key present." }, { "name": "searxng", "active": false, "configured": false, "reason": "SEARXNG_BASE_URL not set: optional fallback skipped." } ] }
```
Default `auto`. 422 on any other value. Audit `settings.data_mode_changed` (`auto -> sample`). `auto` = live when at least one opt-in live collector is configured (Tavily key, SearXNG base URL or the optional google-play package), otherwise sample. This is stored under the `data_mode` key of the `settings` table; the general `GET/PUT /api/settings` (section 11) will read and write the same key.

Source `status` meaning: `online` = last call fine; `slow` = latency above `SOURCE_SLOW_THRESHOLD_MS` (default 4000) or partial results (a query was rate limited mid-scan); `unavailable` = the collector raised `SourceUnavailable` (reasons: `no_key`, `invalid_key`, `quota`, `budget`, `rate_limited`, `unreachable`, `timeout`, `bad_response`, `not_installed`, `json_disabled`, `unsafe_base_url`, `not_configured`, `internal_error`).

---

## 10. Look-alike Lab

### POST /api/lookalike/test  [NEW]  ("Test a name")
Request:
```json
{ "name": "acrnebank-refund", "brand_id": 1, "platform": null }
```
Server applies the Settings look-alike rules (section 11) and impersonation words, skips official handles/package IDs first, then evaluates. Response:
```json
{
  "input": "acrnebank-refund",
  "verdict": "flagged",
  "brand": "Acme Bank",
  "match_percent": 94,
  "headline": "Flagged as a look-alike of Acme Bank",
  "explanation": "This name matches the brand after normalization and is not on your allowlist. It goes to the review queue with these reasons.",
  "reasons": [ {"rule":"character_swaps","label":"Character swap (rn → m)"}, {"rule":"added_words","label":"Added word: refund"} ],
  "normalization": [
    { "step": 1, "label": "You typed", "value": "acrnebank-refund" },
    { "step": 2, "label": "Lower-cased", "value": "acrnebank-refund" },
    { "step": 3, "label": "Look-alike characters folded", "value": "acmebank-refund" },
    { "step": 4, "label": "Spaces and symbols removed", "value": "acmebankrefund" },
    { "step": 5, "label": "Brand and extra words", "value": "acmebank + refund" } ],
  "on_allowlist": false,
  "edit_distance": 0
}
```
`verdict`: `flagged | not_flagged | official`. For `not_flagged` the headline/explanation should say why (e.g. "Acme Bakery is three edits away" / "Extra word is not a known impersonation word"); for `official` it matched an allowlist handle/package (e.g. `Acme Bank`, `@acmebank`). Pure function, no side effects, safe to call on every keystroke with debounce; `name` max 200 chars, rate limited. Rules from the PDF: only one edit from the brand counts; extra words must be known impersonation words; official handles/package IDs skipped before name check; name alone never decides in real scans (logo, bio, publisher are scored too).

### GET /api/lookalike/test-cases  [NEW]
```json
{ "items": [
  {"id":1,"name":"acrnebank","expected":"flagged"}, {"id":2,"name":"Acme Bank Support","expected":"flagged"}, {"id":3,"name":"acme-bank","expected":"flagged"},
  {"id":4,"name":"A C M E Bank","expected":"flagged"}, {"id":5,"name":"acm3bank","expected":"flagged"}, {"id":6,"name":"Acme Bnak","expected":"flagged"},
  {"id":7,"name":"acmebank_official","expected":"flagged"}, {"id":8,"name":"Acme Bakery","expected":"not_flagged"}, {"id":9,"name":"Acme Hotels","expected":"not_flagged"},
  {"id":10,"name":"Acme Weather","expected":"not_flagged"}, {"id":11,"name":"Acme Bank","expected":"official"}, {"id":12,"name":"@acmebank","expected":"official"} ] }
```
Also `POST /api/lookalike/test-cases` `{ "name":"...","expected":"flagged" }` and `DELETE /api/lookalike/test-cases/{id}` [NEW, optional; the UI has no editor today].

### POST /api/lookalike/test-cases/run  [NEW]  (PDF: "Run on this page with the same logic as above")
Runs every case through the same engine as `/lookalike/test` with current rules.
```json
{ "total": 12, "passed": 12, "failed": 0,
  "items": [ { "id":1,"name":"acrnebank","expected":"flagged","result":"flagged","pass":true }, ... ],
  "ran_at": "2026-10-12T12:00:00Z" }
```
Drives "12 of 12 pass" and the table (currently hard-coded literals; there is no run button yet). Results must come from real evaluation, not echo `expected`.

---

## 11. Settings

### GET /api/settings  [NEW]   /   PUT /api/settings  [NEW]  (the page has no Save button today; one must be added)
Response and PUT body (PUT may be partial):
```json
{
  "data_mode": "sample",
  "monitoring": {
    "schedule": "5m",
    "sources": { "instagram": true, "facebook": true, "x": true, "telegram": true, "linkedin": true, "google_play": true, "app_store": true, "third_party_apk": true },
    "sensitivity": 75,
    "rules": { "character_swaps": true, "digits_and_symbols": true, "added_words": true, "spacing_and_separators": true, "transposed_or_missing": true },
    "impersonation_words": ["support","helpline","official","refund","rewards","kyc"]
  },
  "notifications": { "email_alerts": true, "high_risk_immediate": true, "weekly_digest": true, "webhook_url": "" },
  "account": { "organization": "Acme Brand Security Workspace", "user": { "name":"Harini","email":"admin@company.com","role":"admin" } },
  "updated_at": "2026-10-12T09:12:00Z"
}
```
Validation: `schedule` in `5m|15m|1h|1d`; `sensitivity` integer 50 to 95 (slider bounds; default 75); words lowercased, 1 to 30 chars, max ~50, no duplicates; webhook must be https URL or empty. Admin only. Audit `settings.updated` with changed keys. `data_mode` change (live/sample) is **admin only** and audited; it is a new control with no UI today (suggest a switch in Settings, Monitoring or Account tab). Notifications fields exist in the frontend store (`emailAlerts, highRiskImmediate, weeklyDigest, webhookUrl`) but the UI shows different labels ("risk scores above 85", "Daily digest at 9:00 AM", "Slack"); owner to reconcile. Old backend: `GET/PUT /api/settings/integrations` [EXISTS] is a different thing (third-party API keys).
Note: `sources` map here and `PATCH /api/sources/{id} {monitored}` are the same state.

### POST /api/settings/sensitivity-preview  [NEW]  (live text "11 of the 11 current detections would be sent to review at this level")
Request `{ "sensitivity": 75 }` Response `{ "would_review": 11, "total": 11, "message": "A balanced setting for most teams." }`. `message` suggestion by range (balanced around 75).

---

## 12. Reports and exports (CSV + printable HTML/PDF)

### GET /api/reports/preview  [NEW]  (right-hand "Brand Protection Report" card, honours the form filters)
Query: `from=2026-10-06&to=2026-10-12` (or `range=7d|30d|qtd`), `types=fake_profile,fake_brand_page,scam_account,fake_app` (comma list; omitted = all), `status=new|reviewing|confirmed` (optional; the form offers All, New only, Reviewing only, Confirmed only), `include_evidence=true`.
```json
{
  "title": "Brand Protection Report", "brand": "Acme Bank", "from": "2026-10-06", "to": "2026-10-12", "data_mode": "sample", "includes_sample": true,
  "stats": { "detections": 11, "social": 6, "apps": 5, "confirmed": 2, "official_excluded": 7 },
  "rows": [
    { "n": 1, "id": 1, "name": "@acrne_bank_support", "platform": "instagram", "type": "scam_account", "score": 94, "status": "new" },
    { "n": 2, "id": 5, "name": "Acme Bnak Mobile", "platform": "google_play", "type": "fake_app", "score": 93, "status": "confirmed" },
    { "n": 3, "id": 2, "name": "Acme Bank Support Help", "platform": "google_play", "type": "fake_app", "score": 91, "status": "reviewing" } ]
}
```
Rows sorted by score desc (PDF), `name` = handle for social, app display name for apps (PDF; the current UI prints package ids). The PDF's full 11-row order: 94, 93, 91, 91, 90, 89, 88, 87, 85, 82, 76.

### POST /api/reports  [NEW shape; old `POST /api/reports` EXISTS for investigations/campaigns]  ("Generate report")
Request:
```json
{ "from":"2026-10-06","to":"2026-10-12","types":["fake_profile","fake_brand_page","scam_account","fake_app"],
  "status":null,"include_evidence":true,"format":"pdf" }
```
Response 201:
```json
{ "id": 17, "filename": "acme-bank-detections-2026-10-12.pdf", "format": "pdf", "size_bytes": 244000, "created_by": "Harini",
  "created_at": "2026-10-12T12:03:00Z", "from":"2026-10-06","to":"2026-10-12","includes_sample": true,
  "download_url": "/api/reports/17/download", "preview_url": "/api/reports/17/preview", "source": "sample" }
```
Synchronous for small data; if long, return 202 with `job_id` (poll `/api/jobs/{id}`). Audit `report.generated`. CSV columns: `id,name,handle_or_package,platform,type,status,score,found_at,publisher,url,reasons` (+ `evidence_sha256,evidence_url,captured_at` when `include_evidence`).

### GET /api/reports  [NEW shape; list EXISTS]  ("Recent exports")
Query `limit=10`. Items as above (`filename`, `format`, `size_bytes`, `created_by`, `created_at`). PDF seed rows: `acme-bank-detections-2026-10-05.pdf` PDF 238 KB Harini 5 Oct; `acme-bank-weekly-2026-09-28.csv` CSV 12 KB Harini 28 Sep; `acme-bank-detections-2026-09-21.pdf` PDF 191 KB Harini 21 Sep (all `source:"sample"`).

### GET /api/reports/{id}/download  [NEW usage; EXISTS]  (button "Download")
Returns the file (`application/pdf` or `text/csv`, `Content-Disposition: attachment; filename="..."`). Frontend helper `downloadFile(path, filename)` sends the bearer token and uses `blob()`; error body must be JSON `{detail}`.

### GET /api/reports/{id}/preview  [CALLED by orphan ReportsApi (`getReportPreview`, `text:true`); EXISTS]
Returns printable HTML (`text/html`) of the saved report (A4 print CSS, header "Brand Protection Report", stats, table, evidence if included). The UI has no print button today; this is the "printable HTML" path (browser Print to PDF). Also `GET /api/reports/preview.html?<same query as /reports/preview>` [NEW] returns the same HTML for the unsaved form state.

Existing old-product report endpoints `POST /api/reports/{id}/approve`, `GET /api/campaigns/{id}/report` are not part of the PDF flow.

---

## 13. Audit log

### GET /api/audit  [NEW usage; EXISTS]  (no audit screen in the PDF; Settings > Account "Export Audit Log" button and the Login promise "Every review action is written to the audit log")
Query `from`, `to`, `actor`, `action`, `limit`, `offset`.
```json
{ "total": 5, "items": [
  { "id": 91, "ts": "2026-10-12T11:58:00Z", "actor": "Harini", "actor_id": 1, "action": "detection.status_changed", "target": "@acrne_bank_support", "detail": "new -> confirmed", "source": "sample" },
  { "id": 90, "ts": "2026-10-12T09:12:00Z", "actor": "Harini", "action": "brand.updated", "target": "Acme Bank", "detail": "allowlist refreshed" } ] }
```
`GET /api/audit/export.csv` [NEW] returns CSV (`ts,actor,action,target,detail`). Events to write (all produced by actions already in the UI): `auth.login`, `auth.logout`, `brand.updated`, `logo.replaced`, `account.added|removed`, `app.added|removed`, `detection.status_changed`, `detection.marked_official`, `scan.started|finished`, `source.retry`, `source.monitor_toggled`, `settings.updated`, `report.generated`, `data_mode.changed`. The frontend phrases used today: "Updated detection status to \"X\"", "Added to allowlist (marked official)", "Added official account", "Removed official account", "Added official app", "Removed official app". Append-only; viewers cannot read (admin and analyst only).

---

## 14. Evaluation (precision / recall)

**Not in the PDF and not in the frontend code today.** Requested for the build; proposed from data the app already has (reviewer decisions) and the Look-alike test cases.

### GET /api/evaluation  [NEW]
Query `from`, `to`, `type` (optional), `platform` (optional).
```json
{
  "data_mode": "sample", "period": { "from":"2026-09-13","to":"2026-10-12" },
  "reviewed": { "confirmed": 2, "dismissed": 1, "marked_official": 0, "pending": 8 },
  "detection": {
    "true_positives": 2, "false_positives": 1, "precision": 0.667,
    "definition": "precision = confirmed / (confirmed + dismissed)"
  },
  "lookalike_tests": { "total": 12, "passed": 12, "flagged_expected": 7, "not_flagged_expected": 3, "official_expected": 2,
                       "true_positives": 7, "false_negatives": 0, "false_positives": 0, "true_negatives": 3,
                       "precision": 1.0, "recall": 1.0, "ran_at": "2026-10-12T12:00:00Z" },
  "by_type": [ { "type": "fake_app", "confirmed": 1, "dismissed": 0, "precision": 1.0 } ],
  "by_threshold": [ { "sensitivity": 75, "would_review": 11, "precision": 0.667 } ]
}
```
Recall of live detection needs ground truth that does not exist in the app; only the Look-alike test cases (labelled) can supply recall unless a labelled set is added. Owner to confirm scope (see Open questions).
`POST /api/evaluation/run` [NEW] re-runs the look-alike suite and returns the same object (202 + job if slow).

---

## 15. Seed data to port from the frontend (`data/store.ts`) into the sample seeder

11 detections (`INITIAL_DETECTIONS`; all `source:"sample"`, brand Acme Bank):

| id | type | platform | name | handle / package | score | status | found (2026) |
|---|---|---|---|---|---|---|---|
| 1 | scam_account | instagram | Acme Bank Support Care | @acrne_bank_support | 94 | new | 12 Oct 11:58 |
| 2 | fake_app | google_play | Acme Bank Support Help | com.acme.support.help (Quick Tools Studio) | 91 | reviewing | 12 Oct 11:42 |
| 3 | fake_brand_page | facebook | Acme Bank Official Helpline | AcmeBankOfficialHelpline | 88 | reviewing | 12 Oct 10:55 |
| 4 | fake_profile | x | Acme Bank India | @acmebank.india | 91 | confirmed | 11 Oct 19:30 |
| 5 | fake_app | google_play | Acme Bnak Mobile | com.mobileappshub.acmebnak (Mobile Apps Hub) | 93 | confirmed | 11 Oct 16:12 |
| 6 | fake_profile | telegram | A C M E Bank | acme bank | 82 | new | 11 Oct 09:05 |
| 7 | scam_account | instagram | Acme Bank Rewards | @acme_bank_rewards | 90 | new | 12 Oct 08:30 |
| 8 | fake_app | third_party_apk | Acme Bank Mobile Lite | com.acme.bank.lite.apk (Unknown mirror) | 89 | new | 12 Oct 07:15 |
| 9 | fake_app | google_play | AcmeBank Rewards UPI | com.finrewards.acmebank (FinRewards Dev) | 87 | new | 11 Oct 23:20 |
| 10 | fake_app | app_store | Acme Bank KYC Update | com.kycupdate.acmeapp (App Utilities Lab) | 85 | new | 11 Oct 14:40 |
| 11 | fake_profile | x | Acme Bnk | @acmebnk | 76 | reviewing | 11 Oct 13:15 |

Each carries `reasons[]` with points summing to the score, `tags[]`, `evidence {url, timestamp, sha256}`, and per-type extras (`followers`, `joined`, `bioSnippet`, `logoMatchPercent` for social; `downloads`, `released`, `descriptionSnippet`, `descriptionKeywords`, `overlapPercent`, `iconMatchPercent` for apps). Full values in `frontend/src/data/store.ts` (`INITIAL_DETECTIONS`, `INITIAL_ACCOUNTS`, `INITIAL_APPS`, `INITIAL_SOURCES`, `INITIAL_AUDIT_LOG`, `INITIAL_SETTINGS`, `INITIAL_BRAND`). Note the frontend sample has det-4's mapping to "x" with type fake_profile and the dashboard donut count of 5 fake apps / 3 fake profiles / 2 scam accounts / 1 fake brand page, which matches this table.
Dates: the frontend seed is "12 Oct 2026" relative; the seeder should generate timestamps relative to "now" if the demo must look fresh.

---

## 16. Endpoint index

| Method and path | Screen | Frontend status | Existing backend router |
|---|---|---|---|
| POST /api/auth/login | Login | CALLED (live page) | auth |
| POST /api/auth/refresh | all | CALLED (client.ts) | auth |
| POST /api/auth/logout | Header | CALLED helper, not wired | auth |
| GET /api/me | all | CALLED (helper) | auth |
| POST /api/auth/forgot, /reset | Login (forgot link) | CALLED helpers, no UI | auth |
| POST /api/auth/register | Setup | NEW | auth (register) |
| GET /api/health | none | CALLED at import | admin |
| GET/POST /api/brands, GET/PUT /api/brands/{id} | Brand, Setup | NEW (list CALLED by orphan) | brands |
| POST/GET /api/brands/{id}/logo | Brand, Setup | NEW | none |
| GET/POST/DELETE /api/brands/{id}/accounts | Brand | NEW | none |
| GET/POST/DELETE /api/brands/{id}/apps, POST .../apps/import | Brand | NEW | none |
| POST /api/scan, GET /api/jobs/{id}, GET /api/scan/status | Dashboard, Social, Apps, Setup | NEW (exist, old use) | scan |
| GET /api/dashboard | Dashboard | CALLED by orphaned module, shape NEW | dashboard (old shape) |
| GET /api/detections, GET /api/detections/{id} | Social, Apps, Dashboard | NEW | investigations/entities (old) |
| POST /api/detections/{id}/status, /mark-official | Social, Apps | NEW | none |
| GET /api/detections/excluded, /false-positives | Social, Apps | NEW | none |
| GET /api/sources, PATCH /api/sources/{id}, POST /api/sources/{id}/retry, GET /api/sources/events | Sources, Dashboard, Social, Apps | NEW | none |
| POST /api/lookalike/test, GET/POST/DELETE /api/lookalike/test-cases, POST /api/lookalike/test-cases/run | Look-alike | NEW | none |
| GET/PUT /api/settings, POST /api/settings/sensitivity-preview | Settings | NEW | admin has /settings/integrations only |
| GET /api/reports/preview, POST /api/reports, GET /api/reports, GET /api/reports/{id}/download, /preview | Reports | list/preview/download CALLED by orphan module (old shape); rest NEW | reports (old shape) |
| GET /api/audit, GET /api/audit/export.csv | Settings > Account | NEW usage | admin (/audit) |
| GET /api/evaluation, POST /api/evaluation/run | none (no screen) | NEW | none |

### Existing `backend/app/routers` (names only, for reuse vs rebuild)
- `auth.py`: login, refresh, logout, register, forgot, reset, me
- `brands.py`: list, get, create, update, `/{id}/monitor` (old monitor page)
- `dashboard.py`: `/api/dashboard` (old shape)
- `scan.py`: `POST /api/scan`, `GET /api/jobs/{id}`
- `reports.py`: list, create, get, preview (HTML), download, approve
- `admin.py`: health, audit, settings/integrations, users
- `investigations.py`, `entities.py`, `evidence.py`, `campaigns.py`, `apps.py` (APK analyze), `watchlist.py` (+alerts), `simulator.py`: **old product only**, no PDF screen uses them.
Not present at all: accounts, official apps, logo upload, detections review actions, excluded and false-positive lists, sources and retry, look-alike lab, settings (monitoring), evaluation.

## 17. Open questions (owner decisions)

1. Reuse existing FastAPI app (and its DB, auth, jobs) and add the missing routers, or rebuild? Old-product tables (investigations, campaigns) are unused by the 10 screens.
2. `data_mode` semantics and who may toggle it; is sample the default for new orgs?
3. Risk-band thresholds (proposed 85 / 70).
4. Is an audit screen and an evaluation screen in scope? Neither is in the PDF.
5. Setup flow auth: register-first or open onboarding?
6. Detection id format and whether `marked_official` is reversible.
7. Evidence storage (screenshot capture method, hash of the image bytes) and retention.
8. Single brand per org (sidebar chevron implies switching).
9. Notifications: align the UI copy with the data model (`high_risk_immediate` threshold 85?, daily vs weekly digest, webhook).
10. Platform names for the wire enum (`app_store` vs source label "Apple App Store").


---

## 18. Phase 4 implementation notes (authoritative)

Implemented in `backend/app/api/{scans,detections,dashboard,lookalike,settings,reports,evaluation}.py`, logic in `backend/app/services/`. Where this section differs from sections 6 to 14 above, **this section wins**. Roles: `viewer` = GET and pure computations only; `analyst` = scans, detection actions, reports, running look-alike cases/evaluation; `admin` = also settings, data mode, adding/removing look-alike cases. Every error is `{"detail": ...}`. Nothing here ever sends a takedown, report, email or webhook: humans approve, the system records.

### 18.1 Conventions added in Phase 4
- `brand_id` is optional everywhere and defaults to the single brand (404 `No brand profile yet...` when none exists).
- Detection ids are integers. Timestamps ISO-8601 UTC `Z`.
- **Sample honesty:** every list/aggregate response has top-level `data_mode` (`live|sample`, the *effective* mode) and `is_sample`; list responses also have `includes_sample`. Rows carry `source` (`live|sample`). Lists default to rows of the current mode only (in `live` mode sample rows are hidden, never mixed in); `?source=sample|live|all` overrides.
- Detection `status` values: `new | reviewing | confirmed | dismissed | marked_official`. `marked_official` and internal `cleared` rows never appear in `GET /detections` (even with `?status=marked_official`): official assets are only visible through the excluded list.
- Risk band: `high` >= 85, `medium` 70 to 84, `low` < 70 (`risk_band`).

### 18.2 Scans and jobs

**POST /api/scan** (analyst, admin) body `{ "scope": "all|social|apps", "brand_id": 1 }` (both optional). 202:
```json
{ "job_id": "job_19d4f...", "scan_id": 1, "status": "queued", "scan": { /* scan object below */ } }
```
With the in-process or RQ executor the scan usually still shows `queued`/`running`; poll `GET /api/jobs/{job_id}`. 409 `{"detail":"Create the brand profile before scanning."}` when there is no brand. **409 when a scan for the brand is already queued/running:**
```json
{ "detail": "A scan is already running", "job_id": "job_...", "scan_id": 3, "scan": { "status": "running" } }
```
A queued/running scan older than 30 minutes is failed automatically (dead worker) so a new one can start. Audit `scan.started` (actor = user, or `scheduler`).

**Scan object** (`GET /api/jobs/{job_id}`, `GET /api/scans/{id}`, items of `GET /api/scans`, `scan` in responses):
```json
{
  "id": 1, "job_id": "job_19d4f...", "kind": "scan", "brand_id": 1, "scope": "all",
  "status": "partial", "state": "done", "progress": 100,
  "message": "Scan finished with some sources unavailable. Results from the other sources were saved.",
  "steps": [ { "source": "sample", "state": "done", "status": "online", "checked": 17, "new_detections": 0, "message": "Working normally." },
             { "source": "tavily", "state": "error", "status": "unavailable", "checked": 0, "new_detections": 0, "message": "..." } ],
  "result": { "sources_checked": 8, "sources_online": 6, "accounts_or_apps_checked": 17, "new_detections": 9, "official_excluded": 2,
              "partial_sources": ["telegram"], "unavailable_sources": ["app_store"], "finished_at": "2026-10-13T12:00:00Z" },
  "counts": { "candidates_found": 17, "duplicates_removed": 0, "unique_candidates": 17, "official_excluded": 2, "prefiltered": 0, "evaluated": 15,
              "detections_new": 9, "detections_updated": 0, "detections_protected": 0, "cleared_new": 6, "logo_compared": 0, "logo_unavailable": 15,
              "llm_checked": 0, "llm_errors": 0, "official_detections_closed": 0, "truncated": 0 },
  "source_report": [ { "collector": "sample", "status": "online", "reason": null, "message": "Working normally.", "latency_ms": 4, "candidates": 17,
                       "from_cache": false, "cached_age_seconds": null, "next_retry_at": null, "consecutive_failures": 0, "in_backoff": false,
                       "warnings": [], "data_source": "sample" } ],
  "timings": { "collect_ms": 44, "process_ms": 106, "total_ms": 165 },
  "errors": [ "tavily: no_key: ..." ],
  "started_by": "Harini", "data_mode": "sample", "is_sample": true,
  "created_at": "...Z", "started_at": "...Z", "finished_at": "...Z"
}
```
- `status`: `queued | running | completed | partial | failed`. `partial` = some source unavailable or returned partial results (everything the other sources found is saved). `failed` = internal crash, or **every** active source unavailable with nothing to analyse. `state` is the old job vocabulary (`completed`/`partial` => `done`, `failed` => `error`). `started_by` is a user name or `scheduler`. `errors` are short summaries with secrets redacted.
- `counts.detections_protected` = existing detections with a human decision that a re-scan left untouched (only evidence/last_seen/score history updated).

**GET /api/jobs/{job_id}**: the scan object (404 unknown). **GET /api/scans** `?limit=20&offset=0&brand_id=` -> `{ "total", "items": [scan] }` newest first. **GET /api/scans/latest** -> `{ "scan": scan | null }`. **GET /api/scans/{id}**.

**GET /api/scan/status** (any role):
```json
{ "last_scan_at": "...Z", "last_scan_status": "completed", "last_full_sync_at": "...Z", "next_scan_at": "...Z", "schedule": "1d",
  "running_job_id": null, "data_mode": "sample", "is_sample": true,
  "scheduler": { "enabled": true, "schedule": "1d", "interval_minutes": 1440, "interval_source": "setting", "effective_interval_minutes": 1440,
                 "stretched_for_credits": false, "paused_for_credits": false, "next_scan_at": "...Z", "reason": "Next scheduled scan is not due yet." } }
```
`next_scan_at` is `null` when the scheduler is off/disabled. `interval_source` is `env` when `SCAN_INTERVAL_MINUTES` is set (it wins; `0` = off) else `setting`. `GET /api/sources` `summary.next_scan_at` and `summary.schedule` now carry the same values (they were `null` in Phase 3).

**Scheduler credit rule** (also in DECISIONS.md): when Tavily is in the live chain, one scan costs `max queries x credits per query`; one scan is always kept in reserve for manual scans; with less than one scan left above the reserve, scheduled scans pause until the next month (`paused_for_credits`); otherwise the interval is stretched to `days_left x 24h / affordable_scans` when that is longer than configured (`stretched_for_credits`). Manual scans are never blocked by the rule.

`POST /api/sources/{id}/retry` is still synchronous (section 9); it was not turned into a job.

### 18.3 Detections

**GET /api/detections** (any role). Query: `category=social|apps`; `type=` a detection type (`fake_profile|fake_brand_page|scam_account|fake_app`) **or** `social|app|apps` (category shorthand); `platform`; `band=high|medium|low`; `status` (repeatable; `keep_reviewing` accepted for `reviewing`); `source=live|sample|all`; `q` (name / handle / package id / publisher, case-insensitive, wildcards escaped); `min_score`; `from`, `to` (ISO, by `found_at`); `sort=score_desc` (default) `|score_asc|found_desc|found_asc|last_seen_desc`; `limit` (1 to 200, default 50), `offset`; `brand_id`.
```json
{
  "total": 9, "limit": 50, "offset": 0, "data_mode": "sample", "is_sample": true, "includes_sample": true,
  "counts": { "all": 9, "by_type": {"fake_profile":1,"fake_brand_page":2,"scam_account":5,"fake_app":1},
              "by_platform": {"facebook":2,"instagram":2,"x":2}, "by_status": {"new":9}, "by_band": {"low":8,"medium":1} },
  "items": [ { "id": 3, "category": "social", "type": "scam_account", "platform": "x", "name": "...", "handle": "@cmebank", "package_id": null,
               "publisher": null, "profile_url": "https://x.com/cmebank", "score": 70, "risk_band": "medium", "status": "new",
               "summary": "homoglyph (look-alike character), suspicious bio", "tags": ["Homoglyph (look-alike character)", "Suspicious bio"],
               "found_at": "...Z", "last_seen_at": "...Z", "stale": false, "source": "sample" } ]
}
```
**Tab counts:** `counts.all`, `by_type` and `by_platform` use the same filters as the list *except* the tab being counted (a `type` filter does not change `by_type`; a `platform` filter does not change `by_platform`), so "All 9 / Fake profiles 1 / ..." always adds up. `by_status` ignores the `status` filter. Counts cover only listed statuses (`new, reviewing, confirmed, dismissed`).

**GET /api/detections/{id}** (any role; works for any status):
```json
{ "id": 3, "category": "social", "type": "scam_account", "platform": "x", "name": "...", "handle": "@cmebank", "score": 70, "risk_band": "medium",
  "status": "new", "summary": "...", "tags": ["..."], "found_at": "...Z", "last_seen_at": "...Z", "source": "sample",
  "brand_id": 1, "first_seen_at": "...Z",
  "name_match": { "verdict": "flagged", "pct": 96, "techniques": ["homoglyph"], "technique_labels": ["homoglyph (look-alike character)"], "squat_category": "typo",
                  "added_words": [], "impersonation_words": [], "edit_distance": 0, "notes": ["..."] },
  "matched_text": "...", "raw_total": 70, "capped": false, "threshold": 25,
  "reasons": [ { "signal": "name_similarity", "label": "Name similarity", "detail": "Spelled to look like \"Acme Bank\" ...", "points": 33, "max_points": 34 },
               { "signal": "bio_signals", "label": "Bio content", "detail": "kyc_lure (+9): ...", "points": 22, "max_points": 28 } ],
  "official": { "name": "Acme Bank", "handle": "@acmebank", "url": "instagram.com/acmebank", "bio": null, "followers": null, "joined": null, "link": "acmebank.com", "on_allowlist": true },
  "suspect": { "bio": "...", "followers": 323, "following": 3654, "posts": 2, "joined": "2026-10-07", "verified": false, "link": "https://x.com/cmebank", "logo_match_percent": null },
  "logo": { "status": "compared | unavailable", "similarity": 91.2, "reason": "Logo unavailable: the source gave no profile picture URL" },
  "evidence": { "page_url": "...", "captured_at": "...Z", "sha256": "...", "snapshot_url": "/api/detections/3/evidence", "screenshot_url": null, "screenshot_sha256": null, "history": [] },
  "score_history": [ { "at": "...Z", "score": 70, "scan_id": 1 } ],
  "notes": [ { "at": "...Z", "actor": "Harini", "text": "...", "kind": "reviewer | system" } ],
  "history": [ { "at": "...Z", "actor": "system", "kind": "scan | status | note | evidence", "from": null, "to": "new", "note": "Found by scan job_..." } ],
  "extras": { }, "data_mode": "sample", "is_sample": true }
```
- `reasons[].points` sum to `raw_total`; `score = min(100, raw_total)`; `capped` says whether the cap changed anything. Every reason has a `detail` sentence.
- `official` is the brand's own asset on the same platform/store (null fields when no official account exists on that platform; the official page's bio and followers are not collected, so they stay `null` rather than invented). App detections add `publisher_differs`, `official_publisher`, `suspect: { stores, downloads, released, icon_match_percent, rating, rating_count }`, `description: { snippet, overlap_points }` and `official: { name, package_id, publisher, stores, downloads, on_allowlist, official_publishers }`.
- `evidence.sha256` is the SHA-256 of the saved snapshot file. `GET /api/detections/{id}/evidence` returns `{ page_url, captured_at, sha256, hash_verified, snapshot, screenshot_available, screenshot_sha256, history }` (it re-hashes the file: `hash_verified:false` means the file changed or is missing). `GET .../evidence/screenshot` returns the PNG or 404 (screenshots are OFF by default; the Playwright hook is not installed).

**Actions** (analyst, admin; each writes a history event and an audit row with actor and note; body `{ "note": "optional" }`, note max 1000 chars):
- `POST /api/detections/{id}/status` `{ "status": "reviewing|confirmed|dismissed", "note": "" }` (`keep_reviewing`, `confirm`, `dismiss` accepted). Returns the detail object plus `"audit_id"`. 422 for `new`/`cleared`/`marked_official` or unknown values; 409 when the detection is `marked_official`; a no-op (no audit row) when the status is unchanged and no note is given.
- `POST /api/detections/{id}/confirm`, `/keep-reviewing`, `/dismiss`: same as status with that value; body optional.
- `POST /api/detections/{id}/mark-official` `{ "note": "", "publisher": "Acme Bank Ltd" }` -> `{ "detection": {detail}, "allowlist_entry": { "kind": "account|app", "id": 6 }, "official_assets": 8, "audit_id": 12 }`. Adds the asset to `official_accounts` / `official_apps` (verified), writes an `official_matches` row (reason `marked_official`) and sets `marked_official`; it disappears from every detection list and the next scan drops it before scoring. 409 if already marked; 422 for a third-party APK listing, a detection without handle/package, or a handle longer than 30 characters. Audit `detection.marked_official` + `account.added|app.added`. Not reversible through the API.
- Adding an official account/app on the Brand Profile page (`POST /api/brands/{id}/accounts|apps`) also closes any existing detection for exactly that asset (`marked_official`, audited).
- A **re-scan never resets a human decision** (see DECISIONS.md): only `last_seen_at`, score history, evidence and (for a score move of 10+ points) a system note change.

**GET /api/detections/excluded** (any role, `?category=social|apps`): the allowlist, with how often scans met each asset:
```json
{ "total": 2, "data_mode": "sample", "is_sample": true, "items": [
  { "kind": "account", "id": 1, "platform": "instagram", "label": "@acmebank on instagram", "status": "Official · not flagged", "reason": "exact_handle",
    "matched_rule": "Exact official instagram handle.", "times_seen": 2, "last_seen_at": "...Z", "source": "live" },
  { "kind": "app", "id": 1, "platform": null, "label": "Acme Bank Mobile · com.acmebank.mobile", "sub": "Acme Bank Ltd · google_play", "status": "Official · not flagged",
    "reason": "package_id", "matched_rule": "Exact official package id.", "times_seen": 1, "last_seen_at": "...Z", "source": "live" } ] }
```
(`source` here is the origin of the allowlist entry; this list is not filtered by data mode.) A scan also stores per-asset `official_matches` rows (reason `exact_handle|exact_url|package_id|apple_id|bundle_id|marked_official`, `matched_rule`, `times_seen`).

**GET /api/detections/false-positives** (any role, `?category=`, `?source=`): candidates below the threshold whose name partly resembles the brand (status `cleared`), such as "Acme Bakery":
```json
{ "total": 4, "data_mode": "sample", "is_sample": true, "items": [
  { "id": 12, "name": "Acme Hotels", "platform": "facebook", "category": "social", "handle": "acmehotels", "package_id": null, "publisher": null, "score": 2,
    "name_match_percent": 30, "logo_match_percent": null, "icon_match_percent": null, "logo_note": "Logo unavailable: sample data has no images",
    "reason": "Name partly resembles the brand (30%); logo unavailable: ...; score 2 is below the review threshold of 25; no other brand evidence.",
    "status": "not_flagged", "last_seen_at": "...Z", "source": "sample" } ] }
```

### 18.4 Dashboard

**GET /api/dashboard** (any role, `?brand_id=`). Every number is computed from stored rows for the current data mode; "open" = `new + reviewing + confirmed`.
```json
{
  "brand": {"id":1,"name":"Acme Bank"}, "data_mode": "sample", "is_sample": true, "includes_sample": true,
  "kpis": { "total_detections": {"value":9,"delta_week":9,"trend":[0,0,0,0,0,0,9]}, "social_impersonations": {"value":8,"delta_week":8,"trend":[]},
            "suspicious_apps": {"value":1,"delta_week":1,"trend":[]}, "official_assets": {"value":3} },
  "counts": { "by_status": {"new":9,"reviewing":0,"confirmed":0,"dismissed":0}, "by_band": {"high":0,"medium":1,"low":8},
              "by_type": {"fake_profile":1,"fake_brand_page":2,"scam_account":5,"fake_app":1}, "by_category": {"social":8,"apps":1}, "cleared": 6 },
  "by_type": [ {"type":"scam_account","count":5,"percent":56} ],
  "by_platform": [ {"platform":"x","count":2} ], "total_open": 9,
  "needs_review": [ {"id":3,"name":"@cmebank","platform":"x","type":"scam_account","found_at":"...Z","risk_band":"medium","score":70} ],
  "recent": [ {"id":3,"name":"@cmebank","summary":"...","platform":"x","type":"scam_account","score":70,"status":"new","found_at":"...Z","source":"sample"} ],
  "top_detection": {"id":3,"name":"@cmebank","platform":"x","score":70,"risk_band":"medium","on_allowlist":false,"tags":["..."],"source":"sample"},
  "why_flagged": {"detection_id":3,"score":70,"reasons":[{"signal":"name_similarity","label":"Name similarity","detail":"...","points":33}]},
  "sources": [ {"id":1,"slug":"instagram","name":"Instagram","kind":"social","status":"online","monitored":true,"message":"..."} ], "sources_online": 8, "sources_total": 8,
  "last_scan_at": "...Z", "last_scan": { }, "next_scan_at": "...Z", "schedule": { },
  "credits": { "provider":"tavily","month":"2026-10","used":12,"budget":900,"remaining":888,"reported_used":12 }
}
```
`by_type[].percent` adds up to 100. `needs_review` = `new`/`reviewing` by score, max 5. `recent` = newest open, max 6. `delta_week` = open detections first found in the last 7 days; `trend` = cumulative open count at the end of each of the last 7 days (oldest first, last entry = `value`). `last_scan` is a scan object; `schedule` is the `scheduler` object of `GET /scan/status`. `credits` is `null` in sample mode. Lists never contain official assets.

### 18.5 Look-alike Lab

- **POST /api/lookalike/test** (any role; pure) `{ "name": "acrnebank-refund", "brand_id": 1, "platform": null }` (`name` 1 to 200 chars, whitespace collapsed, control characters removed). 200:
```json
{ "input": "acrnebank-refund", "brand": "Acme Bank", "verdict": "flagged", "official_basis": null, "match_percent": 92, "engine_verdict": "flagged",
  "techniques": ["added_word","homoglyph"], "technique_labels": ["added word","homoglyph (look-alike character)"], "squat_category": "combo", "edit_distance": 0,
  "headline": "Flagged as a look-alike of Acme Bank", "explanation": "...",
  "reasons": [ {"rule":"added_words","label":"Added word"}, {"rule":"character_swaps","label":"Homoglyph (look-alike character)"}, {"rule":"impersonation_words","label":"Impersonation word: refund"} ],
  "normalization": [ {"step":1,"label":"You typed","value":"acrnebank-refund"}, {"step":2,"label":"Lower-cased","value":"acrnebank-refund"},
                     {"step":3,"label":"Look-alike characters folded","value":"acrnebank-refund"}, {"step":4,"label":"Spaces and symbols removed","value":"acmebankrefund"},
                     {"step":5,"label":"Brand and extra words","value":"acmebank + refund"} ],
  "on_allowlist": false }
```
`verdict`: `flagged | not_flagged | official`. `official` with `official_basis: "allowlist"` = exact official handle/package; `official_basis: "name"` = the typed text is exactly the brand name/alias (real scans never exclude by name; the explanation says so). The engine's `review` verdict is reported as `not_flagged` (see `engine_verdict`). It uses the Settings rules and impersonation words.
- **GET /api/lookalike/test-cases** -> `{ "total": 12, "items": [ {"id":1,"name":"acrnebank","expected":"flagged","builtin":true,"source":"sample"} ] }` (the 12 defaults are seeded on first use: 7 flagged, 3 not flagged, 2 official).
- **POST /api/lookalike/test-cases** (admin) `{ "name", "expected": "flagged|not_flagged|official" }` -> 201 item; 409 duplicate. **DELETE /api/lookalike/test-cases/{id}** (admin). Audit `lookalike.case_added|case_removed`.
- **POST /api/lookalike/test-cases/run** (analyst, admin; audited as `lookalike.cases_run`):
```json
{ "total": 12, "passed": 12, "failed": 0, "summary": "12 of 12 pass", "ran_at": "...Z",
  "items": [ { "id": 1, "name": "acrnebank", "expected": "flagged", "result": "flagged", "pass": true, "match_percent": 96, "builtin": true } ] }
```
`result` is computed by the same function as `/lookalike/test`, never copied from `expected`.

### 18.6 Settings

**GET /api/settings** (any role) and **PUT /api/settings** (admin, partial body; audit `settings.updated` listing the changed keys, only when something changed):
```json
{
  "data_mode": "auto", "effective_data_mode": "sample",
  "monitoring": { "schedule": "1d",
                  "sources": { "instagram": true, "facebook": true, "x": true, "telegram": true, "linkedin": true, "google_play": true, "app_store": true, "third_party_apk": true },
                  "sensitivity": 75, "threshold": 25,
                  "rules": { "character_swaps": true, "digits_and_symbols": true, "added_words": true, "spacing_and_separators": true, "transposed_or_missing": true },
                  "impersonation_words": ["support","helpline","official","refund","rewards","kyc"] },
  "scheduler": { },
  "notifications": { "email_alerts": false, "high_risk_immediate": false, "weekly_digest": false, "webhook_url": "", "webhook_configured": false, "delivery": "not_implemented" },
  "account": { "organization": "Acme Bank", "user": { "name": "Harini", "email": "admin@company.com", "role": "admin" } },
  "updated_at": "...Z"
}
```
Validation (422): `schedule` in `off|5m|15m|1h|1d` (default `1d`); `sensitivity` 50 to 95 (default 75; `threshold = 100 - sensitivity`); `rules` keys must be the five above; `impersonation_words` lowercased, trimmed, de-duplicated, each max 30 chars, max 50; `sources` keys must be platform slugs (**this map and `PATCH /api/sources/{id}` are the same state**; a change writes `source.monitor_toggled`); `webhook_url` must be `https://` or empty; `data_mode` in `live|sample|auto` (audited as `settings.data_mode_changed`). `webhook_url` is returned to admins only (other roles get `""` plus `webhook_configured`). **Notification settings are stored preferences only; nothing is delivered.** `SCAN_INTERVAL_MINUTES` in the environment overrides `monitoring.schedule`.

**POST /api/settings/sensitivity-preview** (any role) `{ "sensitivity": 75 }` -> `{ "sensitivity": 75, "threshold": 25, "would_review": 9, "total": 15, "data_mode": "sample", "is_sample": true, "message": "A balanced setting for most teams." }` (counts stored candidates of the current mode that the detection rule would send to review at that sensitivity; `total` includes cleared ones).

### 18.7 Reports

Created as **DRAFT**; a person approves; nothing is sent anywhere. Formats `csv` and `html` (printable HTML with print CSS; browser "Print to PDF" produces a PDF). **`format: "pdf"` returns 422: no PDF library is installed.** Rows are snapshotted when the report is created. Every user-controlled string is HTML-escaped in the HTML export (plus a `Content-Security-Policy: default-src 'none'` header and `nosniff`); CSV cells starting with `= + - @ TAB CR` get a leading `'` (formula-injection guard).
- **GET /api/reports/preview** (any role) `?from=&to=&types=fake_app,fake_profile&status=&ids=1,2&include_evidence=true&brand_id=` -> `{ title, brand, from, to, data_mode, is_sample, includes_sample, stats: { detections, social, apps, confirmed, official_excluded }, include_evidence, rows: [ {n,id,name,platform,type,score,status} ] }`. Default selection = confirmed detections of the current mode; `ids` selects specific ones. Rows by score desc. **GET /api/reports/preview.html** takes the same query and returns the printable HTML (unsaved).
- **POST /api/reports** (analyst, admin) `{ "detection_ids": [1,2], "from": "2026-10-01", "to": "2026-10-13", "types": [], "status": null, "include_evidence": true, "format": "html|csv", "title": "..." }` -> 201:
```json
{ "id": 17, "title": "Brand Protection Report", "filename": "acme-bank-detections-2026-10-13.html", "format": "html", "status": "draft", "size_bytes": 8120,
  "created_by": "Harini", "created_at": "...Z", "approved_by": null, "approved_at": null, "from": null, "to": null, "count": 2, "detection_ids": [1,2],
  "stats": { "detections": 2, "social": 2, "apps": 0, "confirmed": 2, "official_excluded": 3 }, "include_evidence": true, "includes_sample": true, "source": "sample",
  "download_url": "/api/reports/17/download", "preview_url": "/api/reports/17/preview", "csv_url": "/api/reports/17/export.csv", "html_url": "/api/reports/17/export.html" }
```
  Without `detection_ids` the detections with the given `status` (default `confirmed`) that match the filters are used. 422 for `pdf`, no matching detections, unknown ids, or `cleared`/`marked_official` ids. Audit `report.generated`. CSV columns: `id,name,handle_or_package,platform,type,status,score,found_at,publisher,url,reasons` (+ `evidence_sha256,evidence_url,captured_at` when `include_evidence`).
- **GET /api/reports** `?limit=10&offset=0` -> `{ total, items: [report] }`. **GET /api/reports/{id}** (adds `rows`). **GET /api/reports/{id}/preview** (inline HTML), **/export.html** and **/export.csv** (attachments, available for every report whatever its stored `format`), **/download** (attachment in the report's own format).
- **POST /api/reports/{id}/approve** (analyst, admin): `draft` -> `approved` with `approved_by/approved_at`; 409 if already approved. Audit `report.approved`. The HTML shows a DRAFT banner until approved and a "sample data" banner when it contains sample rows.

### 18.8 Evaluation

**GET /api/evaluation** (any role) and **POST /api/evaluation/run** (analyst, admin; refreshes the cache; audit `evaluation.run`):
```json
{ "data_mode": "sample", "is_sample": true, "label": "Synthetic numbers are optimistic. ...",
  "synthetic": { "seed": 7, "per_technique": 25, "brands": 5, "computed_at": "...Z", "cached": true,
                 "by_technique": [ { "technique": "character_swap", "tp": 25, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0, "f1": 1.0, "n_pos": 25, "label_ok": 1.0 } ],
                 "overall": { "tp": 0, "fp": 0, "fn": 0, "precision": 1.0, "recall": 0.99, "f1": 0.99, "n_pos": 0, "label_ok": 1.0 }, "false_positives": [] },
  "lookalike_tests": { "total": 12, "passed": 12, "failed": 0, "summary": "12 of 12 pass", "ran_at": "...Z", "true_positives": 7, "false_negatives": 0, "false_positives": 0,
                       "true_negatives": 5, "precision": 1.0, "recall": 1.0, "flagged_expected": 7, "not_flagged_expected": 3, "official_expected": 2, "items": [] },
  "reviewed": { "confirmed": 2, "dismissed": 1, "marked_official": 0, "pending": 8 },
  "detection": { "true_positives": 2, "false_positives": 1, "precision": 0.667, "definition": "precision = confirmed / (confirmed + dismissed), from reviewer decisions. Recall cannot be measured without labelled ground truth." },
  "by_type": [ { "type": "fake_app", "confirmed": 1, "dismissed": 0, "precision": 1.0 } ] }
```
`synthetic` comes from `engine/synthetic.py` over 5 built-in brands (precision/recall per technique and overall; cached in memory per process). **These synthetic numbers are optimistic**: the test names are made with the same tricks the matcher was built to catch. Real-world precision is only `detection.precision` (reviewer decisions); real recall cannot be measured. This replaces the proposed `by_threshold` and `from/to/type/platform` filters of section 14 (not built).

### 18.9 Audit actions written by Phase 4
`scan.started`, `scan.finished`, `detection.status_changed`, `detection.marked_official` (+ `account.added` / `app.added`), `report.generated`, `report.approved`, `settings.updated`, `settings.data_mode_changed`, `source.monitor_toggled`, `lookalike.case_added`, `lookalike.case_removed`, `lookalike.cases_run`, `evaluation.run`. Scheduler-started scans are recorded with actor `scheduler`. (`data_mode.changed` in section 13 is named `settings.data_mode_changed`.)

### 18.10 Phase 4 endpoint index

| Method and path | Role | Notes |
|---|---|---|
| POST /api/scan | analyst, admin | 202; 409 + running scan when one is queued/running |
| GET /api/jobs/{job_id}, /api/scans, /api/scans/latest, /api/scans/{id}, /api/scan/status | any | |
| GET /api/detections, /api/detections/{id}, /api/detections/excluded, /api/detections/false-positives | any | |
| GET /api/detections/{id}/evidence, /api/detections/{id}/evidence/screenshot | any | screenshot 404 unless captured |
| POST /api/detections/{id}/status, /confirm, /keep-reviewing, /dismiss, /mark-official | analyst, admin | audited |
| GET /api/dashboard | any | |
| POST /api/lookalike/test | any | pure |
| GET /api/lookalike/test-cases | any | |
| POST /api/lookalike/test-cases/run | analyst, admin | audited |
| POST /api/lookalike/test-cases, DELETE /api/lookalike/test-cases/{id} | admin | audited |
| GET /api/settings, POST /api/settings/sensitivity-preview | any | webhook hidden from non-admins |
| PUT /api/settings | admin | audited |
| GET /api/reports, /api/reports/{id}, /api/reports/preview, /api/reports/preview.html, /api/reports/{id}/preview, /export.csv, /export.html, /download | any | |
| POST /api/reports, POST /api/reports/{id}/approve | analyst, admin | audited; draft first |
| GET /api/evaluation | any | cached |
| POST /api/evaluation/run | analyst, admin | audited |

### 18.11 Deviations from the proposal in sections 6 to 14
- Scan start returns `{job_id, scan_id, status, scan}` (a superset of `{job_id}`); jobs carry the richer scan object; `state` keeps the old vocabulary.
- Detection `category=` is `social|apps`; `type=` additionally accepts `social|app|apps`. Sort values are `score_desc|score_asc|found_desc|found_asc|last_seen_desc`; `min_score`, `band`, `source`, `q`, `from`, `to` as proposed or added.
- `official` / `suspect` details of the official page are `null` when not collected (not invented). Logo percent is `logo.similarity` / `suspect.logo_match_percent`; it is `null` with a "Logo unavailable" reason when no comparison was possible.
- Report formats are `csv` and `html`; **PDF is not built** (422). Reports are DRAFT until approved (`POST /reports/{id}/approve`, not in the PDF screens). Report creation takes `detection_ids`; the default selection is confirmed detections.
- `mark-official` also accepts Telegram and refuses third-party APK listings and handles over 30 characters.
- Settings: `schedule` gains `off`; notification fields are stored but nothing is sent (`delivery: "not_implemented"`); `account.organization` is the brand name (there is no organisation table); `monitoring` gains `threshold`; the response gains `effective_data_mode` and `scheduler`.
- Evaluation: no `by_threshold`, no date/type/platform filters; `synthetic`, `label` and `reviewed` added.
- `GET /detections/excluded` lists every allowlist asset (with `times_seen`) rather than only assets met in a scan.
- The look-alike `official` verdict also covers the exact brand name (`official_basis: "name"`), as the PDF's "Acme Bank" test case expects.


## 19. Phase 5 notes (frontend connection)

- `GET /api/lookalike/test-cases` seeding race fixed: the 12 default cases are seeded under a process lock (two requests arriving together, such as React StrictMode in development, used to create 24 cases). Single API process only; with several processes a unique index is still needed.
- There is still no `GET /api/sources/events`. The UI builds "Recent source events" from `GET /api/scans` (`source_report`).
- `backend/scripts/e2e_server.py` starts the API for the browser tests: temp SQLite, `Settings(_env_file=None)`, sample collector, stubbed URL checker (no network). Not for production.


## 19. Phase 6: health, readiness and canary (authoritative for these endpoints)

| Method and path | Auth | Purpose |
|---|---|---|
| GET `/health`, `/api/health` | none | Readiness. 200 when `ok` or `degraded`, 503 when the database is down. |
| GET `/health/live` | none | Liveness only: `{"status":"ok","version":...}`, no dependency touched. |
| GET `/api/health/details` | admin | Same checks with details (reasons, ages, revisions, worker count, free disk). |
| POST `/api/health/canary` | admin | Runs the in-memory canary; always 200 with `{passed, checks[], failures[], duration_ms, scope}`; audited as `health.canary`. |

`GET /health` body: `{"status":"ok|degraded|down","version":"0.1.0","database":{"status":"ok|down"},"components":{"database","migration","collectors","redis","worker","data_dir": "<state>"}}`.
Component states: `ok`, `degraded`, `down`, `not_configured` (optional piece switched off: no `REDIS_URL`, SearXNG without a base URL), `unknown` (migration state not determinable, e.g. a database created without Alembic outside production).
Overall: `down` only if the database is down; `degraded` if any component is `degraded` or `down` (Redis unreachable, no worker registered while Redis is configured, data dir not writable or under 100 MB free, migration revision behind head, a live collector unconfigured or failing in its last stored state); otherwise `ok`.
No component makes a live third-party call. Collector state comes from configuration (key present, package installed, base URL set) and the last stored status/age. Neither endpoint returns keys, URLs with credentials or exception text.

`GET /api/health/details` adds, per component: `migration {current, expected, note}`, `collectors {data_mode, effective_mode, sources: {name: {configured, active, status, reason, last_status, last_reason, last_attempt_age_seconds, last_success_age_seconds, consecutive_failures}}}`, `redis {error}`, `worker {workers, queued, last_heartbeat_age_seconds}`, `data_dir {writable, free_mb}`.

Canary `checks[].name`: `scan_completed`, `lookalike_profile_detected`, `lookalike_app_detected`, `official_assets_excluded`, `unrelated_business_not_flagged`, `score_is_explained`, `results_labelled_sample`. The canary never reads or writes the real database and makes no network call.


## 20. Extension work packages E1-E4 (authoritative for the items below)

### 20.1 Severity, algorithm version, signals (every detection)
`risk_band` is unchanged (`high` >= 85, `medium` 70-84, `low` < 70). Detection summaries and details now also carry:

| Field | Meaning |
|---|---|
| `severity` | `LOW` / `MEDIUM` / `HIGH` / `CRITICAL`. Default cutoffs: LOW < 40, MEDIUM 40-69, HIGH 70-84, CRITICAL 85+ (`SEVERITY_MEDIUM_MIN`, `SEVERITY_HIGH_MIN`, `SEVERITY_CRITICAL_MIN`). HIGH and CRITICAL begin where the old medium and high bands begin. |
| `algorithm_version` (detail) | `"1.0"` social and app detections (original scorer, frozen weights `weights_v1`); `"2.0"` web detections (`weights_v2_web`). |
| `scored_at` (detail) | When this score was computed (ISO UTC). |
| `weights` (detail) | `weights_v1` or `weights_v2_web`. |
| `signals` (detail) | List of `{id, status, value, points, max_points, provenance, note, checked_at}`. `status`: `present` (found, scored), `clean` (checked, nothing found), `unavailable` (could not be checked: no logo, no bio, no key, quota, timeout). `value` is 0-1 for present/clean and `null` for unavailable, so a missing answer is never read as clean. `provenance`: `real_api` (a live provider answered), `local_algorithm`, `pretrained_model` (reserved), `synthetic` (sample data). |

`GET /api/detections` accepts `severity=LOW|MEDIUM|HIGH|CRITICAL`; `counts` gains `by_severity` and `by_category`. The engine only scores; no status is ever set automatically.
`GET /api/dashboard`: `counts.by_severity`, `counts.by_category.web`, `counts.by_type.lookalike_domain`, `kpis.web_threats`. `by_type` lists `lookalike_domain` only when something was found.

### 20.2 The `web` category (look-alike domains / websites)
* `category=web`, `type=lookalike_domain`, `platform=web`. Filters: `category=web`, `type=web` (category word) or `type=lookalike_domain`, `platform=web`.
* `handle` is the normalized ASCII host (punycode for IDN), `name` the Unicode host, `profile_url` the canonical URL (userinfo, query and fragment removed). One detection per host.
* Detail: `domain` (normalized URL parts and `components`: label, subdomain, suffix, techniques, `tld_swap`, `brand_in_subdomain`, `userinfo_trick`, `mixed_script`, `rapidfuzz`), `reputation` (see 20.3), `suspect` (`url`, `host`, `registrable_domain`, `components`, `reputation`), `official.domains`.
* Reasons (signals) and maximum points (`weights_v2_web`): `domain_similarity` 40, `rapidfuzz_similarity` 12 (only when the name matcher said `safe`), `deceptive_subdomain` 20 (brand in a subdomain, or in the text before `@`), `idn_homograph` 15, `added_words` 10, `url_reputation` 45 (Safe Browsing MATCH only). The total is capped at 100. These weights need the owner's approval.
* Official domains are excluded by REGISTRABLE domain (public-suffix aware), never by substring: `acmebank.in.evil.test` is not official. Excluded items are recorded in `official_matches` with `kind = "domain"`.
* A web detection cannot be marked official (422): add the domain to the brand's official domains instead.
* Sources: the sample collector (hosts on the reserved `.test` TLD only, never sent to a provider). A web candidate is created by `candidate_from_web_url`; manual entry and import arrive in a later package (E8). There is no Source Health row for web.
* Scan `counts` gain `web_candidates`, `reputation_checked`, `reputation_not_checked`, `reputation_match`, `reputation_clean`, `reputation_unavailable`, `reputation_skipped_sample`, `reputation_over_limit`.
* The UI has no Web list yet (E9). Web rows appear in the Dashboard totals and are reachable through the API.

### 20.3 URL reputation: Google Safe Browsing (Lookup API v4, `threatMatches:find`)
`detail.reputation` = `{url, verdict, provider: "google_safe_browsing", api_version: "v4", checked_at, threat_types, reason, message, cached, provenance}`.

| `verdict` | When | Risk-engine status |
|---|---|---|
| `match` | Google lists the URL (`threat_types`, e.g. `SOCIAL_ENGINEERING`) | `present` (+45) |
| `clean` | Request succeeded, no match. "Not a safety guarantee." | `clean` (0) |
| `unknown` | Not asked or not answered: `no_key`, `budget` (our daily limit), `quota` (HTTP 429 or a 403 quota message; further requests wait for `Retry-After`, default 1 h) | `unavailable` |
| `failed` | Asked and it went wrong: `timeout`, `unreachable`, `server_error` (5xx), `invalid_key` (400/401/403), `bad_response` | `unavailable` |

Only live, fresh, non-official web candidates whose domain already resembles the brand are sent, in one batched request (max 500 URLs). Sample URLs are never sent. URLs are looked up, never opened. The key is read only from `GOOGLE_SAFE_BROWSING_API_KEY` and sent in the `x-goog-api-key` header. Budget: `SAFE_BROWSING_DAILY_BUDGET` requests per UTC day (default 5000, our own limit), per-scan URL cap `SAFE_BROWSING_MAX_URLS_PER_SCAN` (100), cache TTL `SAFE_BROWSING_CACHE_TTL_SECONDS` (3600), timeout `SAFE_BROWSING_TIMEOUT_SECONDS` (5).
`GET /api/sources` adds `reputation`: `{provider, api_version, configured, note, key_env, quota: {used, budget, remaining, unit: "requests", period: "UTC day"}, last, blocked_until}`. Never the key.

### 20.4 Official App Store id (defect D3) and the same app on both stores (D4)
* `POST /api/brands/{id}/apps` accepts optional `apple_app_id` (numeric id, `id123456789` or an apps.apple.com URL; only with store `app_store`). `AppOut` returns `apple_app_id`.
* `PATCH /api/brands/{id}/apps/{app_id}` with `{ "apple_app_id": "..." }` stores it later (audit `app.updated`) and closes existing detections for that listing.
* Adding an existing package id with a store it does not have yet adds the store and returns 201 (before: 409). The same store again is still 409.
* During a scan, an App Store listing matched by its official BUNDLE id (iTunes) also teaches its numeric id for that scan, so the copy that arrives with only the numeric id is excluded. The registry is not changed by this.

### 20.5 Scheduler waits for an official asset (D5)
`GET /api/scan/status` > `scheduler` gains `waiting_for_official_assets`. While the brand has no official account and no official app, the scheduler starts nothing, `next_scan_at` is `null` and `reason` says so. The first official asset releases it (the next tick starts the first scheduled scan). Manual scans are never blocked. Data mode `auto` still resolves to `live` whenever a Tavily key or SearXNG URL exists; the reason is shown (`Auto: live because configured: Tavily key.`).

### 20.6 Retry confirmation (D6)
Each `GET /api/sources` item has `retry_estimate: {credits, provider, remaining, note}`. `credits > 0` only for Tavily-backed sources in live mode (queries per scan x credit cost of the search depth, an upper bound). `POST /api/sources/{id}/retry` for such a source without `?confirm=true` answers **409** `{detail, code: "confirmation_required", estimated_credits, provider, credits_remaining}` and spends nothing. With `?confirm=true` it runs; the response carries `estimated_credits` and the audit row records it. Free retries (sample, iTunes, SearXNG) need no confirmation.

### 20.7 Migration 0005
`detections.severity`, `algorithm_version`, `scored_at` (existing rows back-filled with the default cutoffs and version `1.0`), `official_apps.store_id`. Downgrade drops them.

## 21. Extension work packages E5-E7 (authoritative for the items below)

Writers = analyst or admin. Viewers get 403 on every write.

### 21.1 Text analysis providers (E5)
- `GET /api/providers` (any user): `{active_text_provider, hosted_text_analysis_enabled, text_providers: [{name: local|gemini|huggingface, label, configured, model, needs_key, reason, active}], embeddings: {enabled: false, ...}, image_similarity_backend}`. Keys are never returned.
- `PUT /api/providers/active` (admin, audited `settings.provider_changed`): `{provider: off|local|gemini|huggingface}`. Stored in the `settings` table; the default comes from `SCAM_LLM_PROVIDER` (`gemini`).
- Hosted calls happen only when `SCAM_LLM_ENABLED=true` AND the active provider is configured AND the candidate is not sample data. Otherwise the local baseline still runs and the signal `ai_text_assessment` is `unavailable` (never `clean`).
- Detection detail gets `text_analysis: {local, api}` and two signals (in `signals`, never in `evidence`): `text_similarity` (provenance `local_algorithm`, label "Deterministic similarity") and `ai_text_assessment` (provenance `real_api`, label "API-generated assessment (an opinion, not evidence)"). Findings carry `provenance`: `api_generated_assessment` or `deterministic_similarity`. For social bios an API likelihood >= 0.6 feeds the unchanged scorer (+round(10 x likelihood), inside the bio cap of 28); for app descriptions it is informational (0 points). Scores can only rise from it.

### 21.2 Images (E6)
- `POST /api/candidate-images?purpose=candidate_image|app_icon` (writer, multipart `file`): PNG/JPEG/WebP by magic bytes, 32..6000 px, <= 25 MP, <= `CANDIDATE_IMAGE_MAX_BYTES` (2 MB), re-encoded, stored under a hash name; 413/415/422 on refusal. Returns `{id, format, width, height, size_bytes, sha256, url, note}`; `GET /api/candidate-images/{id}/file` serves it with a fixed image type and `nosniff`. The original filename is never stored or used.
- `POST /api/brands/{id}/logos?label=` (extra official logo, same rules as the primary logo), `GET /api/brands/{id}/logos/{logo_id}/file`, `DELETE /api/brands/{id}/logos/{logo_id}`. `BrandOut.extra_logos` lists them. A candidate is compared with every logo; `details.logo.matched_logo` names the best one.
- `IMAGE_SIMILARITY_BACKEND` = `hash` (default, original pHash+dHash, algorithm `image-1`, scores unchanged) | `multihash` (`image-2`: 0.4 pHash + 0.3 dHash + 0.3 wHash, computed locally) | `imagehash` (same weights with the optional ImageHash package). Hash similarity is a hint, not proof of reuse.

### 21.3 Registry v2 (E7)
- `BrandIdentity.description` (<= 2000 chars; a PUT without the key keeps the stored value). `BrandOut` adds `description`, `archived`, `extra_logos`, `publishers`, `verifications`.
- `DELETE /api/brands/{id}?confirm=<exact name>` (admin): soft archive (`archived_at`), audited `brand.archived`; archived brands vanish from lists, scans and the scheduler and do not block creating a new one (still one ACTIVE brand). `POST /api/brands/{id}/restore` (admin; 409 while another brand is active).
- `POST/DELETE /api/brands/{id}/publishers` `{store: google_play|app_store|any, name, publisher_id}`.
- `PUT /api/brands/{id}/verification` `{asset_type: domain|account|app|publisher|logo, asset_key, status: unverified|verified, method, note}`; `verified` needs a method (`dns_txt, website_link, store_listing, platform_badge, manual_review, other`) and records when and by whom. This is an attestation by a person; the app does not check DNS or stores. Domains, accounts and apps stay allowlisted as before; a PUBLISHER id is allowlisted only once verified (a candidate app whose `publisher_id` matches).

### 21.4 Manual entry and import (E7)
- `POST /api/candidates/social|app|web` (JSON, strict schema) and `POST /api/candidates/import?kind=social|app|web&format=csv|json&dry_run=false` (multipart `file`). Columns: social `platform,handle,display_name,url,bio,followers,following,posts,created_at,verified,image_id`; app `store,package_id,name,publisher,publisher_id,url,description,installs,rating_count,image_id`; web `url,title,note`. Unknown columns/fields are refused. Limits: `IMPORT_MAX_BYTES` (1 MB, 413), `IMPORT_MAX_ROWS` (500, 413), UTF-8 (BOM allowed) or UTF-16 with BOM, else 422.
- Response: `{dry_run, total, accepted, rejected, duplicates, created, updated, official, results: [{row, status: created|updated|cleared|official|not_flagged|duplicate|error|valid, detection_id, score, severity, message}]}`. Valid rows are saved even when others fail. A dry run validates only.
- Rows use the same pipeline as a scan (allowlist first, then scoring, then the de-duplicating upsert on `canonical_key`). Detections carry `origin`: `scan | manual | import | synthetic` (`source` stays `live | sample`). URLs in rows are never opened; logo comparison uses only an uploaded `image_id`. Free text is cleaned (control characters removed, a leading `= + - @` gets a `'` prefix) and stored as inert text.
- Authorized provider interface (stub only, no access claimed): `app/core/providers/authorized.py`.

### 21.5 Migration 0006
`brands.description/archived_at`, `detections.origin` (back-filled: sample rows `synthetic`), tables `brand_logos`, `brand_publishers`, `asset_verifications`, `candidate_images`, `text_analysis_cache`.


## 22. Final pass (E8-E10)

- **Resolution.** Migration 0007 adds `detections.resolution` (nullable). `POST /api/detections/{id}/resolution` (writer) `{resolution: true_positive|false_positive|resolved|monitoring, note}` returns the detail plus `audit_id`. It does not change `status`, adds an event (`kind: resolution`) to `history`, and is audited as `detection.resolution_set`. `GET /api/detections?resolution=` filters; rows and detail carry `resolution`. `severity` is already on every row and detail.
- **Evaluation.** `GET /api/evaluation` -> `synthetic` also has `confusion {matrix{tp,fp,fn,tn}, false_positive_rate, false_negative_rate, accuracy, total_cases, negative_groups[{group,n,flagged,false_positive_rate}]}`, `dataset {name, version, size, synthetic: true, banner, split}`, `algorithm_version {social_app, web}` and `ml_classifier` ("deferred until labelled analyst data exists"). The top level adds `by_category[{category,total,confirmed,dismissed,precision}]` from reviewer decisions. Negative groups: `hard_negative`, `fan_page`, `authorized_reseller`, `unrelated`. There is no train/test split (nothing is trained), so no variants can leak. Authorized-reseller names are flagged by a name-only matcher (known limit; the allowlist clears real ones).
- **Rate limit.** Every `/api/*` request counts against `API_RATE_LIMIT_PER_MINUTE` (default 600; 0 = off) per client IP and per token; over the limit -> `429 {detail}` with `Retry-After: 60`. In memory, single process. `/health*` is exempt. See `docs/SECURITY.md`.
