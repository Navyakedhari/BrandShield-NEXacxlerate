# ScamMind AI: Frontend Contract

Read-only scan of `frontend/src` against the design PDF `ScamMind AI Screens.pdf`. No source file was modified.
Paths below are relative to the project root `ScamMind AI/`. Frontend root: `frontend/src`.

## 0. Headline findings

1. **The 10 routed pages do NOT call the backend (except Login).** Every page except `/login` reads and writes a React context
   (`data/store.ts`) that persists to `localStorage` under keys `scammind_v2_{brand,accounts,apps,detections,sources,auditLog,settings}`.
   There is no `fetch` anywhere in those pages. Every "scan", "retry", "generate report", "download" is a `setTimeout` plus `alert()`.
2. **`frontend/src/api/*.ts` is a client for the OLD product** (investigations, campaigns, graph, DNA, simulator, watchlist, brand monitor, app
   analysis). Only `api.login` (via `LoginPage`) and `session`/`onUnauthorized` (via `App.tsx`) are used by the current 10 routes.
   Everything else in `api/` is called only by 9 orphaned page files that are not routed.
3. **The PDF has 12 pages, not 10.** 10 real screens plus 2 fragments (p3: sidebar-only shell; p6: dashboard header strip, a layout stub).
4. The code has exactly 10 routes and they map 1:1 to the 10 PDF screens. Mismatch is in depth (hard-coded values), not in page count.
5. **Auth is effectively bypassable** (see section 12): `RequireAuth` silently starts an "offline" session when no token exists, and `LoginPage`
   starts an offline session when login fails for any reason, including a wrong password.

Terminology: "UI status" = the string shown in the UI today (`'New'`). The API doc (`docs/API.md`) proposes lowercase snake_case wire values.

---

## 1. Route map

| # | Route | Component | Layout | PDF page | Guard |
|---|-------|-----------|--------|----------|-------|
| 1 | `/login` | `pages/LoginPage.tsx` | none | 1 | public |
| 2 | `/setup` | `pages/SetupPage.tsx` | none | 2 | public (no guard) |
| 3 | `/dashboard` | `pages/DashboardPage.tsx` | `AppLayout` (sidebar) | 4 (3 and 6 are fragments) | `RequireAuth` |
| 4 | `/brand` | `pages/BrandProfilePage.tsx` | AppLayout | 5 | RequireAuth |
| 5 | `/social` | `pages/SocialMonitoringPage.tsx` | AppLayout | 7 | RequireAuth |
| 6 | `/apps` | `pages/AppStoreMonitoringPage.tsx` | AppLayout | 8 | RequireAuth |
| 7 | `/lookalike` | `pages/LookalikeLabPage.tsx` | AppLayout | 9 | RequireAuth |
| 8 | `/sources` | `pages/SourceHealthPage.tsx` | AppLayout | 10 | RequireAuth |
| 9 | `/reports` | `pages/ReportsPage.tsx` | AppLayout | 11 | RequireAuth |
| 10 | `/settings` | `pages/SettingsPage.tsx` | AppLayout | 12 | RequireAuth |
| - | `/` and `*` | redirect to `/dashboard` | | | |

Routes are defined in `App.tsx`. `AppLayout` (`components/layout/AppLayout.tsx`) renders `Sidebar` + `main`, and injects `onOpenMobileMenu` into the page.
Note `/brand` is **Brand Profile**; there is no `/audit`, `/evaluation`, `/detections/:id`, or `/reset` route.

### Orphaned page files (exist, NOT routed, not in PDF)

`AppAnalysisPage`, `BrandMonitorPage`, `InvestigatePage`, `ResultsPage`, `ScamDnaPage`, `ThreatEvolutionPage`, `ThreatGraphPage`,
`ThreatSimulatorPage`, `WatchlistPage` (each with a `.css`). Support files used only by them: `api/{apps,campaigns,investigations,monitor,reports,
simulator,watchlist,dashboard}.ts`, `data/mockData.ts`, `data/advancedData.ts`, `components/charts/*` (EntityGraph, IndiaMap, Radar, RingSegments,
SemiGauge, SmoothLines, BarSpark, TickRing), `data/states.geojson` (empty, 0 lines). Only `charts/Sparkline.tsx` and `charts/Donut.tsx` are used by the live Dashboard.
`README.md` still says "12 pages"; that number matches neither the 10 routed pages nor the 19 page files (stale). Decision needed: delete or keep (see section 13).

---

## 2. Shared shell

### Sidebar (`components/layout/Sidebar.tsx`)
- Search box "Search detections" with keycap "⌘K": **decorative**, no handler.
- Active brand card (links to `/brand`): avatar = first letter of `brand.name`; text `Active brand · {officialAccounts.length + officialApps.length} official assets`.
  (PDF: "Acme Bank / Active brand · 7 official assets"). Chevron suggests brand switcher: **not implemented**, single brand only.
- Nav items in order: Dashboard `/dashboard`, Brand Profile `/brand`, Social Monitoring `/social`, App Store Monitoring `/apps`, Look-alike Lab `/lookalike`,
  Source Health `/sources`, Reports `/reports`; pinned bottom: Settings `/settings`, Support (button, `alert('Support desk: support@scammind.ai')`).
- Matches PDF menu order exactly.

### Header (`components/layout/Header.tsx`)
- Props: title, subtitle, actions, onOpenMobileMenu.
- Bell button with permanent dot: decorative, no panel, no data.
- User chip: **hard-coded** `{name:'Harini', email:'admin@company.com'}`. Menu has one item "Sign out".
- Sign out does `localStorage.removeItem('scammind_auth')` then navigates to `/login`. **Bug:** the real token key is `scammind.auth` (dot), and
  `api.logout` / `session.clear()` are never called, so tokens survive and the refresh token is never revoked.
- README says the header shows an "Offline: showing sample data" chip; **no such chip exists in the current Header** (only `api.isOffline` exists, unused by the 10 pages).

### Footer note (every data page)
`.page-bottom-note`: "Sample data for the fictional brand Acme Bank. Not real detections." Static text on Dashboard, Social, Apps, Look-alike,
Sources, Reports. Must become conditional on `data_mode === 'sample'` (section 12).

---

## 3. Shared enums and types (from `data/store.ts`)

```ts
OfficialAccount.platform: 'Instagram' | 'X' | 'Facebook' | 'LinkedIn' | 'YouTube'
OfficialAccount.status:   'Verified' | 'Unreachable'
OfficialApp.status:       'Verified'                       // only value
OfficialApp.stores:       string[]  // values used: 'Google Play', 'App Store'
Detection.type:     'fake_profile' | 'fake_brand_page' | 'scam_account' | 'fake_app'
Detection.category: 'social' | 'apps'                       // derived: fake_app => apps, others => social
Detection.platform: 'Instagram'|'X'|'Facebook'|'Telegram'|'LinkedIn'|'Google Play'|'App Store'|'Third-party APK'
Detection.status:   'New' | 'Reviewing' | 'Confirmed' | 'Dismissed' | 'Marked official'
SourceItem.status:  'Online' | 'Slow' | 'Unavailable'
```

Display label maps used in the UI:
- type to label: Social page `type.replace('_',' ')` gives "fake profile", "scam account", and **"fake brand_page"** (bug: `replace` only swaps the first underscore;
  same bug in the Reports preview table). PDF label is "Fake brand page" (Social list) and "Fake page" (Dashboard recent table). Dashboard donut uses "Fake brand page".
- status chip color (same ternary copy-pasted in Social, Apps, Reports): New=blue, Reviewing=orange, Confirmed=red, everything else (Dismissed, Marked official)=green.
- Risk bands: **none in code**. Only the Dashboard "Needs review" chip is hard-coded "High". The PDF uses "High" there. Sensitivity threshold in Settings is 50..95 (default 75).
  The API must define a band (proposed: `high >= 85`, `medium 70-84`, `low < 70`, see API.md) or the frontend must keep not showing bands. Decision needed.
- Platform naming drift: detections use `'App Store'`; sources and settings use `'Apple App Store'`; Brand Profile add-app dropdown value `'App Store'` with label "Apple App Store".
  API uses slug `app_store` for both; frontend maps to the label.

Other types: `DetectionReason {label, points}`, `DetectionEvidence {screenshot?, url, timestamp, sha256}`, `AuditLogItem {id, user, action, target, time}`,
`BrandProfile {name, industry, aliases[], keywords[], domains[], logo{format, dimensions, size, isSquare}}`,
`AppSettings {scanSchedule, sensitivity, monitoredPlatforms: Record<string,boolean>, rules{characterSwaps, digitsAndSymbols, addedWords,
spacingAndSeparators, transposedOrMissing}, impersonationWords[], notifications{emailAlerts, highRiskImmediate, weeklyDigest, webhookUrl}}`.

---

## 4. Page: Login `/login` (PDF p1)

**File:** `pages/LoginPage.tsx`. **Purpose:** sign in. Left hero pane (static copy: "A Safer Digital Space for Everyone", three pillars "Explainable scores / Human approves / Evidence kept"),
right form.

**Fields:** `email` (type email, required), `password` (required), checkbox "Keep me signed in on this device" (default **true**, to `persist`).
**Actions:**
- Submit: if both non-empty, `api.login(email, password, keepSignedIn)` => `POST /api/auth/login` `{email, password}`. On ANY thrown error (401, network, 500) it calls
  `session.startOffline()` and still navigates to `/dashboard`. If either field is blank (browser `required` normally prevents it) it also starts offline and navigates.
  `errorMsg` state and `.auth-error-alert` exist but are **never set on failure** (dead path).
- "Forgot password?": `alert('Password reset link sent to your administrator.')`. `api.forgot(email)` / `api.reset(token,new_password)` exist but unused. No reset page.
- "Set up your brand profile": navigate `/setup`.
- Footer text: "Sessions use short-lived tokens. Every review action is written to the audit log."

**Response expected (`api/auth.ts`, `api/client.ts`):** `{access_token, refresh_token, user:{id,name,email,role:'admin'|'analyst'|'viewer',active?,default_brand_id?}}`.
Stored in `localStorage` (persist) or `sessionStorage` (not persist) under `scammind.auth` = `{access, refresh}` and `scammind.user`.
**States:** loading = button text "Signing in…" and disabled. Error = none displayed (bug). Empty = n/a.
**Mock to replace:** none in the page; the fallback is the offline session.

## 5. Page: Setup `/setup` (PDF p2)

**File:** `pages/SetupPage.tsx`. 3-step wizard: Brand, Official assets, First scan. Top bar "Skip setup for now" links to `/dashboard`.
**Step 1:** `companyName` (default `brand.name || 'Acme Bank'`), `aliasesText` (comma separated, default `'Acme, AcmeBank, Acme Bank India'`), logo box
(**static**: "Official logo uploaded · ✓ PNG · 512 × 512 · 84 KB · square", Replace button = `alert`).
**Step 2:** static pills only: accounts `@acmebank (Instagram)`, `@AcmeBank (X)`, `acmebank (Facebook)`; apps `Acme Bank Mobile (com.acmebank.mobile)`, `Acme UPI (com.acmebank.upi)`. Not editable, not read from store.
**Step 3:** static text "Ready to scan 8 threat intelligence sources..." with three hard-coded pills (Instagram, Google Play, X "Online"). Button "Launch Protection Dashboard".
**Finish:** `setBrand({...prev, name, aliases})` then `/dashboard`. No scan is started, no account created, no API call.
**Needed from API:** create account + organization + brand (auth is not required to reach `/setup`, so it needs `POST /api/auth/register` or an onboarding token), upload logo, add official accounts/apps, start first scan, list sources.
**States:** none (no loading/error/validation, empty name allowed).
**PDF diffs:** PDF step 1 matches. PDF shows only step 1; steps 2 and 3 are code inventions (copy differs: code step titles "List official assets", "Start your first scan").

## 6. Page: Dashboard `/dashboard` (PDF p4)

**File:** `pages/DashboardPage.tsx`. Title "Dashboard", subtitle `Brand risk overview for {brand.name}`.

| Block | Today's source | Must become API |
|---|---|---|
| Hero "Protect {brand.name} across social media and app stores" | `brand.name` | brand name |
| Hero status line "Last scan 12 Oct 2026, 11:58 AM · 6 of 8 sources online" | **hard-coded string** | `last_scan_at`, `sources_online`, `sources_total` |
| Button "Run scan now" | `setTimeout 1500` then `alert('Scan complete! 8 sources queried...')` | `POST` start scan, poll job |
| Button "Edit brand profile" | navigate `/brand` | - |
| KPI Total detections | `detections.length` (store) | `kpis.total_detections.value` |
| KPI delta "↑ 4 this week" + sparkline `[4,6,8,7,9,10,11]` | **hard-coded** | `delta_week`, `trend[]` |
| KPI Social impersonations | count `category==='social'`; delta "↑ 2 this week"; spark `[2,3,3,4,5,5,6]` | same |
| KPI Suspicious apps | count `category==='apps'`; "↑ 3 this week"; spark `[1,2,2,3,3,4,5]` | same |
| KPI Official assets protected | `officialAccounts.length + officialApps.length`; "Never flagged" | `official_assets` |
| Donut "Impersonation types" (`impersonationTypesData`) | **hard-coded** `[Fake app 5 #4f9dff 45%, Fake profile 3 #a78bfa 27%, Scam account 2 #f0566a 18%, Fake brand page 1 #f59e55 10%]`; center `detections.length` / "Detections" | `by_type[]` |
| "Where we found them" (`whereWeFoundThemData`) | **hard-coded** Google Play 3, Instagram 2, X 2, Facebook 1, Telegram 1, App Store 1, Third-party APK 1; bar width = count/`max:4` | `by_platform[]` (UI should compute max) |
| "Needs review" (`needsReviewItems`) | **hard-coded** 5 rows ids det-1,2,3,7,8, detail strings like "Instagram · scam account · 2 minutes ago", chip "High" | `needs_review[]` = top N detections, status New/Reviewing, high band, with `found_at` for relative time |
| "View all" link | `/social` | - |
| "Recent detections" table (`recentDetections`) | **hard-coded** 6 rows with fields `name, sub, platform, type, match 'NN/100', status, statusClass`. Note types shown here: "Scam account", "Fake app", "**Fake page**", "Fake profile" | `recent[]` latest 6 |
| Row click | navigates to `/apps` if platform contains "Play" or "Store", else `/social`. Does not select the clicked detection | needs deep link `?detection=id` |
| "Why it was flagged" card | **hard-coded** `@acrne_bank_support ... scored 94`, four tags | `top_detection` with `reasons_summary[]`, `tags[]` |
| "Review detection" | navigate `/social` | deep link |
| "Source health" mini card | `sources.slice(0,6)` from store; status dot online=green, slow=orange, else red; text "Online" / "Slow, retrying" / "Unavailable, cached" | `sources[]` |
| "Manage" link | `/sources` | - |

**States:** no loading, no empty, no error. If store lists are empty the numbers show 0 but the hard-coded blocks still show rows.
**PDF diffs:** PDF shows exactly these blocks; PDF p4 "Source health" shows 6 sources (code slices to 6, ok). PDF p3 and p6 are layout fragments only.

---

## 7. Page: Brand Profile `/brand` (PDF p5)

**File:** `pages/BrandProfilePage.tsx`. Header subtitle "Tell us what is official. Everything else is compared against it."
Local component state initialised from `brand` in the store.

**Section 1 Brand identity** (all saved only by the bottom "Save brand profile" button)
- `companyName` (text), `industry` (text; default 'Banking and financial services'), `aliases[]` (tag input, Enter to add, x to remove, de-duplicated),
  `keywords[]` (same; PDF example `acme, acmebank, #AcmeBank`), `domains[]` (same; `acmebank.com, acmebank.in`; green pill).
- Official logo box: letter badge "A"; spec text "✓ PNG or SVG ✓ 512 × 512 px ✓ 84 KB (max 2 MB) ✓ Square" (**static string**, not from `brand.logo`); "Replace logo" = `alert('Select a new PNG/SVG file...')`.
  **Logo upload is not implemented.** Constraints implied by copy: PNG or SVG, square, 512x512 recommended, max 2 MB.

**Section 2 Official social accounts**
- List from `officialAccounts`: avatar letter, title `{handle} on {platform}`, url, status chip (Verified green / Unreachable orange), trash button "Remove from allowlist".
- Badge `{n} accounts on the allowlist`.
- Add form: `platform` select (Instagram, X, Facebook, LinkedIn, YouTube), `handle` text, button "Add to allowlist", hint "Enter the exact handle your organization owns."
  Store logic `addOfficialAccount`: prepends `@` unless platform is Facebook/LinkedIn; builds `url` from a domain map
  (`instagram.com/`, `x.com/`, `facebook.com/`, `linkedin.com/company/`, `youtube.com/@`); status always `'Verified'` (no verification). Applied **immediately**, not on Save. Writes audit "Added official account".
- Remove: immediate, writes audit "Removed official account".

**Section 3 Official mobile apps**
- Table columns: App (letter badge, name, stores joined by " · "), Package or bundle ID, Publisher, Status (always "Verified"), delete.
- "Add an official app" opens inline form: App Name (required), Package or Bundle ID (required), Publisher (required, default 'Acme Bank Ltd'), Store select (`Google Play` | `App Store`; label "Apple App Store"). Single store only per add.
  Applied immediately. Audit "Added official app".
- "Import from store listing URL" = `alert(...)`: **not implemented** (needs endpoint that fetches listing metadata).

**Bottom bar:** note "Changes are applied to the next scan after you save."; Cancel = `window.location.reload()`; Save shows "Saved successfully ✓" for 3 s. Save writes name, industry, aliases, keywords, domains to the store only.

**Side column**
- Profile health: **hard-coded** "86%", "Good coverage. Fix one unreachable URL to reach 100%.", checklist (ok: Brand name and aliases; Official logo meets requirements; Official domains added; Apps include publisher names; warn: "YouTube URL could not be reached"). Needs `profile_health {percent, checks[{label, ok}]}`.
- Allowlist: never flagged: live counts accounts, apps, `domains.length` (local unsaved state). Static explanation.
- "How this profile guides detection": 4 static steps. Link "Test name variations in the Look-alike Lab" to `/lookalike`.

**Mock to replace:** `INITIAL_BRAND`, `INITIAL_ACCOUNTS` (5), `INITIAL_APPS` (2) in `data/store.ts`; health block inline.
**States:** no loading/empty/error. Empty lists render nothing (no empty-state message). No field validation except `required` on the app form. No duplicate check on accounts/apps.

---

## 8. Page: Social Media Monitoring `/social` (PDF p7)

**File:** `pages/SocialMonitoringPage.tsx`. Subtitle is **hard-coded** "Fake profiles, fake brand pages and scam accounts using the Acme Bank identity" (does not use `brand.name`).

**Sources bar (hard-coded):** pills Instagram, Facebook, X (green), "Telegram · slow" (orange), LinkedIn (green); warning "Telegram is responding slowly. Results may be incomplete. Retrying in 4 minutes. Details" (`href="#details"`, dead link). Not wired to `sources` store. Needs `sources[]` filtered to social with `status`, `retry_in_minutes`, `message`.

**Filter tabs:** `filterType` `'all' | 'fake_profile' | 'fake_brand_page' | 'scam_account'`. Labels "All 6", "Fake profiles 3", "Fake brand pages 1", "Scam accounts 2": **counts are hard-coded strings**. Needs `counts` in the list response.
**Scan button** "Scan social platforms now": `setTimeout(1200)` + `alert('Social scan complete: 486 accounts analyzed. No new threats.')`. Needs `POST` scan with `scope=social`, job polling, progress.

**List ("Detections", "Sorted by risk"):** store order filtered to `category==='social'` then by type. **Not actually sorted by score** (store order gives 94, 88, 91, 82, 90, 76; PDF shows 94, 88, 91, 90, 82, 76; neither is strictly by score). Row fields: platform letter, `name`, `handleOrPackage · platform · type`, `score`, status chip. Default selection id `'det-1'` (hard-coded); falls back to first item. No empty state if filter yields 0 rows (list simply blank); `selected` undefined hides the detail card.

**Detail card fields:** `foundAt` ("Detection detail · 12 Oct, 11:58 AM"), `name`, `handleOrPackage on platform`, type chip, status chip, big score "Risk".
- OFFICIAL card is **hard-coded**: "Acme Bank / @acmebank / 'Official page of Acme Bank. We will never ask for your PIN, OTP or KYC details over DM.' / Followers 2.4M / Joined 2014 / Link acmebank.com" (tag "On allowlist"). Needs the matching official account's profile snapshot.
- SUSPECT card: tag `Logo {logoMatchPercent||91}% match` (91 is a hidden default), `name`, `handleOrPackage`, `bioSnippet` (default KYC text), `Followers: {followers||'214'}`, `Joined: {joined||'12 days ago'}`, `Link: acme-kyc-update.in` (**hard-coded for every detection**). Needs `profile_link` field.
- "Why it scored {score}": `reasons[]` `{label, points}` rendered `+points`. Sum equals score in the sample (e.g. 34+10+25+17+8=94). Label convention is `Signal · explanation`.
- "Evidence kept": "Profile screenshot" and "Profile URL" (**non-clickable spans**), `Captured {foundAt}`, `SHA-256 {first4}…{last4}`. Needs screenshot URL/blob, URL, captured_at, sha256.
- Actions (call `updateDetectionStatus` / `markAsOfficial`, instant, no confirm dialog, no error handling):
  - "Confirm impersonation" => status `Confirmed`
  - "Keep reviewing" => `Reviewing`
  - "Dismiss as false positive" => `Dismissed`
  - "Mark as official" => `Marked official`, **and** appends to `officialAccounts` (social: platform, handle, url from evidence, status Verified) or `officialApps` (apps: name, package, publisher default 'Acme Bank Ltd', stores `[platform]`).
  - Each writes an audit entry (`Updated detection status to "X"` / `Added to allowlist (marked official)`), user hard-coded 'Harini'.
  - Note: `New` is never set by an action; no way back to New. Note text: "A person confirms every detection. No takedown request is sent automatically."
- After Dismissed or Marked official the row stays in the list (green chip). No undo.

**Bottom panels:**
- "Excluded as official": **hard-coded** 5 rows ("@acmebank on Instagram", "@AcmeBank on X", "acmebank on Facebook", "company/acmebank on LinkedIn", "@acmebank on YouTube", each chip "Official · not flagged"). Should be the allowlist (`officialAccounts`) or a server list of matches skipped during scan.
- "False-positive guard": **hard-coded** 3 rows: "Acme Bakery · Facebook page / Category Bakery, logo 6% match", "Acme Hotels · Instagram / Category Hospitality, logo 4% match", "@acme_weather · X / Category Weather app, logo 3% match"; chip "Not flagged". Link "See how names are matched" to `/lookalike`.

**Mock to replace:** `INITIAL_DETECTIONS` det-1,3,4,6,7,11 (social) in `data/store.ts`; plus every hard-coded block above.
**States:** no loading, no error, no empty list message.

## 9. Page: App Store Monitoring `/apps` (PDF p8)

**File:** `pages/AppStoreMonitoringPage.tsx`. Same structure as Social.
- Sources bar **hard-coded**: Google Play (green), "Apple App Store · unavailable" (red), Third-party APK sites (green). Banner: "App Store is unreachable. Showing cached results from 12 Oct, 9:40 AM." with inline "Retry" = `alert('Retrying Apple App Store scrape worker...')`.
- Tabs `storeFilter` `'all' | 'Google Play' | 'App Store' | 'Third-party APK'`; labels "All stores 5", "Google Play 3", "App Store 1", "Third-party APK 1" (**hard-coded counts**).
- Scan button "Scan app stores now": `setTimeout(1200)` + `alert('App store scan complete: 368 APK listings reviewed across 3 marketplaces.')`.
- List "Suspicious apps · Sorted by risk" (not sorted; store order det-2,5,8,9,10; PDF shows only 3 rows 93, 91, 87 as New/Confirmed/Reviewing mix). Row: `name`, `{publisher||'Unknown'} · {platform}`, score, status chip. Default selection `'det-2'`.
- Detail: `App detail · found {foundAt}`, name, `handleOrPackage` (package id), status chip, score "Risk".
  - Publisher alert banner: "Publisher differs from the official one. Official apps are published by **Acme Bank Ltd**, this one by **{publisher||'Quick Tools Studio'}**." Official publisher hard-coded; always shown even if publisher matches.
  - OFFICIAL card **hard-coded**: Acme Bank Mobile, com.acmebank.mobile, Publisher Acme Bank Ltd, Stores Google Play, App Store, Downloads 10M+.
  - SUSPECT card: `Icon {iconMatchPercent||93}% match`, publisher, `Store: platform`, `Downloads: {downloads||'500+'} · {released||'released 6 days ago'}`.
  - Description overlap: `Description overlap {overlapPercent||88}%`, `descriptionSnippet`, `descriptionKeywords[]` pills, note "Highlighted phrases also appear in the official app description."
  - "Why it scored": same `reasons[]`. Evidence block and 4 action buttons identical to Social, labels "Confirm fake app", "Keep reviewing", "Dismiss as false positive", "Mark as official". Note "A person confirms every detection. Store screenshot and listing details are saved as evidence."
- Bottom: "Excluded as official" **hard-coded** (Acme Bank Mobile · com.acmebank.mobile / Acme Bank Ltd · Google Play, App Store; Acme UPI · com.acmebank.upi / Acme Bank Ltd · Google Play) and "False-positive guard" **hard-coded** (Acme Weather · com.acme.weather / Acme Corp · icon 8%, description 4%; Acme Fitness Tracker · com.acmefit.app / Acme Fit Pvt Ltd · icon 5%, description 2%).
- Stale cache: PDF Source Health says cached results are "marked as stale on the app page". The code shows no per-row stale marker. API should return `stale: true`, `cached_at` per detection.

**Mock:** `INITIAL_DETECTIONS` det-2,5,8,9,10. **States:** none (no loading, empty, error).

---

## 10. Page: Look-alike Lab `/lookalike` (PDF p9)

**File:** `pages/LookalikeLabPage.tsx`. "Comparing against: {brand.name}".
- Input `testInput` (default `'acrnebank-refund'`) + 8 preset pills: `acrnebank-refund, Acme Bank Support, acm3bank, A C M E Bank, Acme Bnak, Acme Bakery, Acme Hotels, Acme Bank`.
- **Verdict panel is static** whatever is typed: "Flagged as a look-alike of {brand}", text, chips "Name match 94%", "Character swap (rn → m)", "Added word: refund". The test never evaluates; typing "Acme Bakery" still says Flagged.
- "How the name was normalized": 5 steps computed client-side by a toy function `getNormalizationSteps`: (1) You typed raw; (2) Lower-cased; (3) Look-alike characters folded (`rn`→`m`, `3`→`e`, `0`→`o` only); (4) Spaces and symbols removed (`[^a-z0-9]`); (5) Brand and extra words (special-cased: contains acme+refund => "acmebank + refund", acme+support => "acmebank + support", acme => "acmebank", else the cleaned string; note "acmebnak" never gets corrected). This logic must be replaced by server output `normalization_steps[]`.
- Side cards: "Variations we catch" (5 static examples) and "How we avoid over-flagging" (4 static rules). Links to `/brand`.
- "Test cases" table: **static array** `testCases` of 12: `acrnebank, Acme Bank Support, acme-bank, A C M E Bank, acm3bank, Acme Bnak, acmebank_official` expected Flagged; `Acme Bakery, Acme Hotels, Acme Weather` expected Not flagged; `Acme Bank, @acmebank` expected Official. `result` and `pass` are literals (always pass); chip "12 of 12 pass" literal. There is no "Run" button; PDF text says "Run on this page with the same logic as above."
- Result enum: `'Flagged' | 'Not flagged' | 'Official'`.
- Note: the PDF row "Acme Bank Support" and others match the code exactly.
**States:** none; no loading/error.
**Needs API:** evaluate one name (verdict, match %, matched rules, steps), run all test cases (and ideally store user-managed test cases), rules from Settings applied.

---

## 11. Page: Source Health `/sources` (PDF p10)

**File:** `pages/SourceHealthPage.tsx`. State from store `sources` (8 items) and `auditLog`.
- KPI cards: Online `count(status==='Online')` "of 8 sources" (the "8" is literal); Slow count "Partial results kept"; Unavailable count "Cached results shown"; Next scan "**12:10**" "Every 5 minutes" (**hard-coded**, ignores Settings schedule).
- Card header "Sources" + "Checked every 5 minutes · last full sync 12 Oct 2026, 12:05 PM" (**hard-coded**).
- Per source row: letter avatar, `name`, `description`, status chip (Online green / Slow orange / Unavailable red), "Monitor" checkbox bound to `isMonitored` (store only, toggles locally), `statusMessage`, `Last sync {lastSync} · {syncDetail}`; "Retry now" button shown only for Slow or Unavailable: `setTimeout(1200)` then `alert('Source sync retry initiated...')`, button text "Retrying…". Status never changes after retry.
- Side: "When a source is unavailable" (4 static steps; "We retry automatically after 1, 5 and 15 minutes, then hourly."). "Recent source events": renders **`auditLog`** items as `"{action}, {target}"` + `time`. So user actions (status updates, allowlist adds) appear in "source events" too; PDF shows only source events. Should be two separate feeds (`/sources/events` vs `/audit`).
- Source records (from `INITIAL_SOURCES`, ids src-1..src-8, in order): Instagram "Public profile and page search" Online 11:58 AM "214 profiles checked"; Facebook "Page and profile search" Online 11:55 AM "96 pages checked"; X "Profile search" Online 11:57 AM "138 profiles checked"; Telegram "Public channels and groups" **Slow** 11:31 AM "41 of about 120 checked", msg "Rate limited. Partial results are kept. Retrying automatically in 4 minutes.", `retryMinutes: 4`; LinkedIn "Company page search" Online 11:50 AM "22 pages checked"; Google Play "Store search and listing details" Online 11:58 AM "310 apps checked"; Apple App Store same description **Unavailable** 9:40 AM "Cached results shown", msg "The store could not be reached. Showing cached results from 9:40 AM. They are marked as stale on the app page."; Third-party APK sites "Mirror and download site lookup" Online 11:40 AM "58 listings checked", msg "Working normally. Coverage is limited to known mirror sites."
- Event seed (`INITIAL_AUDIT_LOG`): "Instagram scan finished / 2 new detections 11:58 AM", "Telegram rate limited / partial results kept 11:31 AM", "Google Play scan finished / 1 new detection 11:42 AM", "Apple App Store unreachable / using cached results 9:41 AM", "Brand profile saved / allowlist refreshed 9:12 AM". The PDF lists events in this same (non-chronological) order. Events show only `time`, no date.
**States:** none; no loading/empty/error. `Monitor` toggles do not affect scanning or Settings (`settings.monitoredPlatforms` is a separate, unsynced copy of the same concept).

---

## 12. Page: Reports `/reports` (PDF p11)

**File:** `pages/ReportsPage.tsx`.
**Form state (all local, none sent anywhere):**
- `dateRange` select: `'Last 7 days (6 to 12 Oct 2026)'` (default, literal dates), `'Last 30 days'`, `'Quarter to date'`.
- Include checkboxes (all default true): Fake profiles, Fake brand pages, Scam accounts, Fake apps (maps to types `fake_profile`, `fake_brand_page`, `scam_account`, `fake_app`).
- `statusFilter` select: 'All statuses' | 'New only' | 'Reviewing only' | 'Confirmed only' (no Dismissed / Marked official option).
- `includeEvidence` (default true) "Include evidence (screenshots, URLs, hashes)".
- `exportFormat` `'PDF' | 'CSV'` ("PDF report" / "CSV data").
- "Generate report": `setTimeout(1200)`, prepends fake entry `acme-bank-report-2026-10-12.{pdf|csv}` with meta `"{FORMAT} · 245 KB · Harini · Just now"` to local `recentExports`, then `alert`. Filters are ignored. In-memory only (lost on refresh).
**Preview card:** title "Brand Protection Report", sub "Acme Bank · 6 to 12 Oct 2026 · Preview" (**hard-coded**, even brand name), chip "Sample data", stat cells **hard-coded** 11 Detections / 6 Social / 5 Apps / 2 Confirmed / 7 "Official, excluded". Table `#, Name or handle, Platform, Type, Score, Status` rendered from **all** store detections (ignores filters/dates); name = `handleOrPackage || name`, type `type.replace('_',' ')` (bug). PDF preview is sorted by score desc and shows app display names (e.g. "Acme Bnak Mobile") where code shows package ids (`com.mobileappshub.acmebnak`). PDF row names for social are handles; for apps they are display names: code mismatch.
**Recent exports:** seeded 3 rows (`acme-bank-detections-2026-10-05.pdf` "PDF · 238 KB · Harini · 5 Oct"; `acme-bank-weekly-2026-09-28.csv` "CSV · 12 KB · Harini · 28 Sep"; `acme-bank-detections-2026-09-21.pdf` "PDF · 191 KB · Harini · 21 Sep"). "Download" builds a Blob of text `Report export for {brand}: {filename}\nGenerated by ScamMind AI` (a stub, not a real file).
**Not present in code but wanted:** printable HTML preview, real PDF, real CSV, server list of exports with size/creator/date.
**States:** `generating` only ("Generating…"). No empty/error states.
**Mock to replace:** preview stats and header; `recentExports` seed; detections source.

## 13. Page: Settings `/settings` (PDF p12)

**File:** `pages/SettingsPage.tsx`. Tabs `'Monitoring' | 'Notifications' | 'Account'`.
- **There is no Save button.** Only `impersonationWords` (add on Enter / remove) is written to the store immediately. `scanSchedule`, `sensitivity`, `platforms`, `rules` live in local `useState` and are discarded on navigation. Store `AppSettings` has the same fields but they are never updated from this page (except words).
- Monitoring tab: "Scan schedule": select `'Every 5 minutes' | 'Every 15 minutes' | 'Hourly' | 'Daily'`; Social platform checkboxes Instagram, Facebook, X, Telegram, LinkedIn; App store checkboxes Google Play, Apple App Store, Third-party APK sites (all default true; keys of `monitoredPlatforms`).
  "Detection sensitivity": range `min=50 max=95` default 75, label "Send to review when risk score is at least {n}", ends "More detections" / "Fewer, higher confidence"; summary **hard-coded** "11 of the 11 current detections would be sent to review at this level. A balanced setting for most teams." (needs `preview {would_review, total}` from server as the slider moves).
  "Look-alike rules" checkboxes: characterSwaps "Character swaps (rn → m)", digitsAndSymbols "Digits and symbols for letters", addedWords "Added words", spacingAndSeparators "Spacing and separators", transposedOrMissing "Transposed or missing letters"; "Impersonation words" chips `support, helpline, official, refund, rewards, kyc` + "Add a word"; link to `/lookalike`.
- Notifications tab (**not in PDF design**): three uncontrolled checkboxes "Send email alerts for risk scores above 85", "Daily digest summary at 9:00 AM", "Webhook dispatch to Slack Security Channel". Store holds `notifications{emailAlerts, highRiskImmediate, weeklyDigest, webhookUrl}` (seed webhook `https://hooks.slack.com/services/T00/B00/XXXX`) but it is not bound to the UI.
- Account tab (**not in PDF**): static "Signed in as Harini (admin@company.com). Organization: Acme Brand Security Workspace." + "Export Audit Log" = `alert('Access audit log exported to CSV.')`.
- **No live/sample data mode toggle exists** in the code or the PDF settings screen (PDF p12 shows only the Monitoring tab). It is a requested new control (see API.md).
**States:** none.

---

## 14. Cross-cutting

### API base URL
`frontend/src/api/client.ts`: `BASE_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000'`. Paths all begin `/api/...`. `frontend/Dockerfile` references `VITE_API_URL` (build arg). `vite.config.ts` has no dev proxy; backend CORS allows origins from `settings.frontend_origin`.

### Auth / session
- Tokens `{access, refresh}` in `localStorage['scammind.auth']` (keep signed in = true) else `sessionStorage`. User object in `scammind.user`. `read()` checks localStorage then sessionStorage.
- Every request adds `Authorization: Bearer <access>` unless `anonymous: true` (login, refresh, logout, forgot, reset).
- On 401 with tokens: single-flight `POST /api/auth/refresh {refresh_token}` expecting `{access_token, refresh_token, user}`; on success retries once; on failure clears session and dispatches window event `scammind:unauthorized`, which `App.tsx` handles by navigating to `/login`.
- Logout contract: `POST /api/auth/logout {refresh_token}` (anonymous) exists in `authApi.logout` but the Header does not call it.
- `GET /api/me` returns user plus `default_brand_id`; `brandId()` caches it, falls back to `1`.
- `RequireAuth` (`App.tsx`): if `!session.isAuthenticated()` it calls `session.startOffline()` and renders the page anyway. So the auth guard never blocks. Decision needed before the real backend goes in: remove this auto-offline behaviour or gate it behind `data_mode === 'sample'`.

### Offline / sample mode
- `session.startOffline()` sets `scammind.offline-session = true`. `request()` then never touches the network for protected calls: returns `fallback` or throws `ApiError(0, 'You are in the offline sample-data session. Sign in to use this feature.')`.
- On a network failure (fetch throws) `request()` sets the offline flag and returns `fallback` if provided, else throws `ApiError(0, 'Cannot reach the server. Check your connection and try again.')`. `withSample(fn, sample)` in `api/common.ts` returns sample data only on `ApiError.status===0`.
- This sample machinery is only used by orphaned pages. The 10 live pages are 100% local sample data, which today is the *only* mode; the "Sample data for the fictional brand Acme Bank" footer is always shown.
- `checkHealth()` calls `GET /api/health` at import time (`api/index.ts`); result sets the offline flag but nothing in the 10 pages reads it.

### Error shape the client expects
`!res.ok` => try `res.json()`; if `detail` is a string use it; if `detail` is an array (FastAPI 422) join each `d.msg` with `'; '`; else `Request failed (${status})`. Throws `ApiError(status, message)`.
Downloads: `downloadFile(path, filename)` fetches with bearer, refreshes once on 401, uses `blob()`, error uses `json().detail`.
Text/HTML responses: `request(..., {text: true})` (used for report previews).
Toasts: `api/toast.ts` (`toast.success/error/info`, `errorMessage(e)`), used only by orphans; the 10 pages use `alert()`.
`useAsync(fn, deps)` returns `{data, loading, error, reload}`; used only by orphans. The 10 pages need loading/error/empty states added when wired.

### Formatting helpers (`api/common.ts`)
`fmtDate` => "11 Oct 2026", `fmtDateTime` => "11 Oct 2026, 10:42 AM" (local time), `fmtChange(pct)` => "↑ 12%" / "↓ 3%", `SEVERITY_LABEL` critical|high|medium|low. The current pages instead show preformatted strings such as `'12 Oct, 11:58 AM'`; the API should return ISO-8601 UTC and the UI formats.

### Audit log
Store `auditLog` (`AuditLogItem {id,user,action,target,time}`) written by `addAuditLog` on: status change, mark official, add/remove official account, add/remove official app. It is displayed only as "Recent source events" on Source Health. There is **no audit-log page**; "Export Audit Log" is an `alert` in Settings > Account. Login page copy promises "Every review action is written to the audit log."

### Persistence keys (to be retired when wired)
`localStorage`: `scammind_v2_brand`, `_accounts`, `_apps`, `_detections`, `_sources`, `_auditLog`, `_settings`; auth `scammind.auth`, `scammind.user`, `scammind.offline-session`; `sessionStorage` `scammind.lastApp` (orphan).

### Misc UX facts
- Alerts (`window.alert`) used for: forgot password, support, logo replace (x2), import from URL, run scan, scan social, scan apps, retry app store, retry source, report generated, export audit log. Each is a placeholder for a real async call plus toast.
- No pagination anywhere (max 11 detections). Lists need pagination or a cap in the API.
- No search: the sidebar "Search detections" box is inert.
- No i18n; times are local strings.

---

## 15. Mismatches between code and PDF

**Screens**
1. PDF = 12 pages: 10 screens + p3 (sidebar-only shell, no content) + p6 (dashboard header strip). Code has the 10 screens. No PDF screen is missing from code.
2. Code has 9 page files not in the PDF and not routed (list in section 1). The related `api/*.ts` modules and `data/mockData.ts`/`advancedData.ts` serve only them.
3. PDF Settings shows tab chips Monitoring / Notifications / Account but only designs the Monitoring tab; code invents Notifications and Account content. PDF has no "Export Audit Log", no audit screen, no evaluation (precision/recall) screen, no live/sample toggle.
4. Other PDFs in `Downloads`: `ScamMind AI dark glass theme, all pages.pdf` (10 pages) and `... (1).pdf` (4 pages) are the **older investigation product** (nav: Dashboard, Investigate, Threat Graph, Threat Evolution, Scam DNA, App Analysis, Brand Monitor, Threat Simulator, Reports, Watchlist; phone-number/fraud-contact rows). The `(1)` variant uses a different nav ("Investigate, App Analysis, Threat Graph, Brand Monitor, Reports, More Tools"). They match the orphaned pages, **not** the PDF. `SCAMMIND_AI_Digital_Forensics_Hackathon_Concept.pdf` (12 pages) is a concept dossier (case file, Scam DNA, hidden network), also the old product. None of them describes the 10-screen brand-protection design.

**Labels / copy**
5. Detection type label: PDF Social/Reports "Fake brand page"; PDF Dashboard recent table "Fake page"; code Social/Reports `fake brand_page` (bug), Dashboard "Fake page" and donut "Fake brand page".
6. PDF Reports preview app rows use display names (e.g. "Acme Bnak Mobile"); code prints `handleOrPackage` (package id). Social rows use handles in both.
7. Social list order: PDF 94, 88, 91, 90, 82, 76; code 94, 88, 91, 82, 90, 76. Both claim "Sorted by risk" but neither is. Apps: PDF shows 3 rows (93, 91, 87); code lists all 5.
8. PDF Dashboard "Recent detections" ordering: det-1,2,3,4,5,6 (by time desc, mixed); code identical (hard-coded).
9. PDF p9 verdict for the sample input is data-dependent; code shows the same verdict for any input.
10. Setup: PDF shows only step 1 (copy matches); steps 2 and 3 are code-only.
11. Login: PDF has no error state; code has a hidden error alert that never fires.
12. Settings: PDF slider range/default text "at least 75" matches; hard-coded "11 of the 11" matches the sample.
13. PDF Source Health "Recent source events" shows only source events; code mixes user audit entries into that list.
14. Platform naming: PDF uses "Apple App Store" (sources) and "App Store" (dashboard, detections, apps tab); code does the same, but with different string literals per page (see section 3).

**Fields/statuses the PDF implies but code lacks**
- Status "Dismissed" and "Marked official" never appear in the PDF samples; Reports status filter lacks them.
- Per-detection stale/cached flag for App Store results.
- Profile health breakdown, last scan time, "Next scan" time, source event feed, export list with size/user.
- Risk band label ("High") shown only on Dashboard Needs review.

---

## 16. Decisions / questions that block a clean build

1. **Existing backend vs rebuild.** `backend/app` is built for the old product (investigations, campaigns, entities, watchlist, simulator). Only auth, `/api/me`, `/api/brands`, `/api/scan` + `/api/jobs/{id}`, `/api/reports`, `/api/audit`, `/api/health`, `/api/dashboard` are near-reusable, and even those return old-product shapes. Reuse vs rebuild must be decided by the owner.
2. **Orphan pages and old API modules**: delete, archive, or keep for a later phase? (They contribute `charts/*`, `mockData.ts`, `advancedData.ts`.)
3. **Auth guard**: remove the auto-offline in `RequireAuth` and the catch-all offline in `LoginPage`? Show real error "Incorrect email or password"?
4. **Onboarding without auth**: `/setup` is public; does it create the org/user (register) or require login first?
5. **Live vs sample data mode**: where does the toggle live (Settings > Account? Monitoring?), is it per org, and does "sample" mean seeded fictional Acme Bank records tagged `source:"sample"`?
6. **Risk bands** thresholds (Dashboard shows "High").
7. **Evaluation (precision/recall)** and **audit-log viewer** are requested but have no screen in the PDF or code; confirm whether a screen is required or API-only.
8. **Evidence**: screenshots storage and the exact meaning of the SHA-256 (hash of screenshot file?).
9. **Multi-brand**: sidebar brand card has a chevron; confirm single brand per org.


---

## 17. Phase 5 status (frontend connected to `backend/`)

Sections 1 to 16 above describe the frontend **before** Phase 5 (they are kept as the record of what was replaced). This section is the current state.
The UI (layout, classes, copy, CSS) of the 10 designed pages was kept; only the data wiring changed, plus the additions listed under "New".

### Wired (all against the real API, no domain data in browser storage)

| Area | What now comes from the API |
|---|---|
| API layer (`src/api/client.ts`, `sm.ts`, `types.ts`, `format.ts`, `sample.ts`) | Typed fetch client, Bearer token, single-flight refresh-on-401 with refresh-token rotation, error mapping (`detail` string, 422 list by field, 429 with Retry-After, network), AbortController. One session key `scammind.auth` (+ `scammind.user`); sign-out calls `POST /auth/logout` and clears both storages. |
| Route guard / login | No offline session. `RequireAuth` redirects to `/login`; `/setup` is behind it (admin must be signed in). Login shows the server error ("Incorrect email or password", the 429 text, the network message). |
| Header / sidebar | User chip from `GET /api/me`; sidebar brand card and "N official assets" from the brand; global **Sample data** badge whenever any answer carries `is_sample: true` (or a data mode resolving to sample). |
| Setup | Creates or updates the brand (`POST/PUT /brands`), client pre-check + `POST /brands/{id}/logo`, real official assets, source list, first scan (`POST /scan`). |
| Dashboard | Everything from `GET /dashboard`: hero status (last scan, sources online, next scan), KPI values/deltas/sparklines, donut, platform bars, needs review, recent detections, why flagged, source health. Run scan now = start + poll `/jobs/{id}` with progress. Rows deep-link to `/social?detection=<id>` or `/apps?...`. |
| Brand Profile | Brand identity save (`PUT`), logo upload with the server rules and errors, accounts (duplicate 409, handle format 422, unreachable warning), apps, profile health (percent, checks), allowlist counts. |
| Social / App Store | List sorted by risk (server), tab counts from `counts`, detail panel (reasons with points, evidence with SHA-256, official side from the allowlist), actions confirm / keep reviewing / dismiss / mark as official with optimistic update and rollback, excluded-as-official and false-positive guard lists, source bar with real status, Retry (apps), scan button with progress. |
| Look-alike Lab | Live `POST /lookalike/test` (350 ms debounce, stale calls cancelled); verdict, match %, rule chips and the 5 normalization steps come from the server; "Run test cases" calls the runner and shows "N of M pass". The hard-coded "Flagged 94%" is gone. |
| Source Health | Sources with status, last sync, message, cached age, next retry; KPI counts; real "Next scan" time and schedule; Monitor toggle (optimistic, rollback); Retry per source; Tavily credit meter; retry back-off text from the API. |
| Reports | Selection from the filters (types, status, date range computed from today), live preview (`/reports/preview` with ids), create **draft** (`POST /reports`, csv or html), recent exports list, preview in a sandboxed iframe, CSV/HTML download via fetch + blob with auth, approve. App rows show display names, social rows handles. Nothing is ever sent. |
| Settings | GET/PUT `/settings` with a Save bar (admin only; others see a read-only form). Schedule, monitored platforms (same state as the Source Health toggle), sensitivity with a live server preview, look-alike rules, impersonation words. |

### New (requested additions)

- **Data mode** switch Live / Sample / Auto in Settings > Account (admin; read-only for others), using `/settings/data-mode`.
- **Evaluation view** at `/evaluation` (linked from Settings > Account; no sidebar entry, so the designed screens are unchanged): precision / recall / F1 per technique and overall, the labelled look-alike case results, reviewer precision, and the server's "synthetic numbers are optimistic" label shown first.
- Tavily credit meter on Source Health; "Sample data" badge in the header and in the Social/Apps detail panel; the footer note is shown only while the data is sample.
- Loading / empty / error (with Retry) states using new minimal classes in `components/common/States.css` (tokens only).

### Deliberate wording changes (flagged for review)

1. Reports format button "PDF report" is now **"Printable report"** (the server has no PDF library: `format: "pdf"` returns 422). A hint says "Print, then Save as PDF".
2. Notifications tab: the second checkbox "Daily digest summary at 9:00 AM" is now **"Weekly digest summary"** (the stored field is `weekly_digest`); every row says "(not delivered yet)" and the tab states `delivery: not implemented`. The webhook row now has a URL field (https only).
3. Source Health step 3 text is built from the real back-off of the API (`1m,5m,15m,1h,6h`) instead of "1, 5 and 15 minutes, then hourly".
4. The Settings sensitivity note says "of the N candidates found so far" (the `total` of the server includes cleared candidates), not "current detections".
5. App detail: "Description overlap 88%" cannot be shown (the server returns points, not a percentage, and no matched phrases), so it shows "+N points" or "No description overlap found". The publisher banner appears only when the publisher really differs.
6. The App Store detail panel now has an "Evidence kept" block (required by the task; the original code only had it on Social).
7. Display names: detections use "App Store" / "Third-party APK"; sources and Settings use "Apple App Store" / "Third-party APK sites" (as in the design). `type.replace('_', ' ')` is replaced by a label map ("Fake brand page").

### Still not wired / known limits

- Sidebar search box ("Search detections", inert), bell button (inert), "Support" (still an alert), brand switcher chevron (single brand).
- "Forgot password?" has no endpoint: the button shows an honest message. There is no reset page.
- "Import from store listing URL": the server has no import endpoint; the button explains that.
- **No source-event feed in the API**: "Recent source events" is built from the source reports of the latest scans (`GET /scans`), and says so. The user audit log is no longer shown there.
- Retry is synchronous in the server (it answers with the finished result), so there is no job to poll; a polling fallback exists for a future async server but has only unit-level coverage.
- Logo: PNG, JPEG or WebP, at least 256 px, roughly square, max 2 MB (the server refuses SVG), not "PNG or SVG" as the design copy says.
- Official page bio / followers / joined, app downloads and icon match, screenshots: not collected in sample mode (screenshots are off by default), so they show as not collected, never invented.
- Reports use up to 200 detections (API limit per list call). Audit log viewer: only "Export Audit Log" (CSV) is wired; there is no audit screen.
- Token refresh is single-flight **per browser tab**. Two tabs that refresh at the same instant can present the same rotated refresh token twice; the server treats that as theft and revokes all refresh tokens of that user, so both tabs are signed out. Not mitigated (would need a cross-tab lock).
- Unverified: live mode with a real Tavily key or real stores (no live calls were made); slow/unavailable source states are covered by unit tests only (the sample collector always reports online); PostgreSQL.

### Visual parity (design PDF vs the running app)

A side-by-side pass of the 10 screens (1440 px wide) against `ScamMind AI Screens.pdf`. Font is unchanged (Manrope, which the PDF also uses).

- Dashboard: KPI cards have the tinted icon chip, the delta as a pill and a larger sparkline (only when the API sends a trend; the "Official assets" card has none, so none is drawn); hero has the stacked layout with the violet and green glow; "Where we found them" is a name | bar | count row; Needs review has type icons and a warm tint; "Why it was flagged" uses the lightbulb and blue pills.
- Social / App detail: the header score is a real SVG ring (arc = score/100, colour by `risk_band`, `components/charts/ScoreRing.tsx`); "Why it scored" shows one bar per signal, width = that signal's points relative to the strongest signal of the same detection (the API has no per-signal maximum, so nothing is invented). List scores are coloured by band; source chips are tinted by status; official/suspect cards use bold values and pill tags; Excluded / False-positive guard use icons instead of emoji.
- App Store Monitoring opens on "Google Play" (a `?detection=` deep link still opens on "All stores" so any store's detection is found).
- Shell: sidebar is a floating rounded card, no header divider, new brand mark (`components/common/BrandMark.tsx`) and "AI" in the accent colour, Settings and Look-alike icons as in the design.
- Login, Setup, Brand Profile (health ring from the real percent, allowlist card, neutral keyword pills, bold handles), Look-alike Lab (tester and test cases in the left column, red result panel, numbered steps), Source Health (one-line message + sync for healthy rows, tinted slow/unavailable rows), Reports (stat tiles) and Settings (accent threshold value, styled slider, larger controls) were re-spaced and re-coloured to the PDF.
- Remaining differences (all deliberate or data-dependent): "Description overlap 88%" with matched-phrase chips (API has points only); official-page bio, followers, joined, downloads are "not collected" texts; the PDF's sample figures (94, 91, ...) differ from the dev data; Reports "Printable report" label; "Run test cases" button and "Not run" results instead of a pre-filled "12 of 12 pass"; Settings/Brand save bars and the header "Sample data" badge; the "Sample data" badge inside the detail panel; the sidebar search placeholder reads "Search detections" (the PDF clips it); Dashboard Source health lists the first six sources; alias, keyword and domain pills keep a remove (x) button.

### Tests

`cd frontend && npm test` (Vitest + Testing Library: API client refresh/error mapping/abort, hooks, login errors, route guard, header, optimistic rollback, source health) and `npm run test:e2e` (Playwright walk of every page against the real backend in sample mode). See `frontend/README.md`.
