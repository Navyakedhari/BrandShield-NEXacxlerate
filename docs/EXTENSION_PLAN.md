# Extension plan: DigitalTrust AI brief on top of ScamMind AI

Status: E1-E7 IMPLEMENTED 2026-10-14 (see the progress table below); E8-E10 IMPLEMENTED 2026-10-15 (see DONE_AUDIT.md for what is not met). Originally written as a plan only. Written 2026-10-13 from a read of `backend/app`, `frontend/src`, `docs/`.
Provider facts in section 3 were read from official docs/PyPI on this date; anything not confirmed is marked UNVERIFIED.

## 1. Gap analysis (requirement by requirement)

Existing layout: engine `backend/app/core/engine/*` (pure, no I/O), collectors `backend/app/core/collectors/*`, pipeline `backend/app/core/pipeline/scan_pipeline.py`, API `backend/app/api/*`, models `backend/app/models/{brand,detection,ops,user}.py`, migrations `backend/alembic/versions/0001..0004`.

| # | Requirement | Already satisfied (where) | Gap | Proposed change |
|---|---|---|---|---|
| A | Safe Browsing adapter, verdicts match/clean/unknown/failed, timestamps, key optional, clean != safe | Collector contract with `no_key`/`quota`/`rate_limited`/`bad_response` reason codes (`collectors/base.py`, `docs/COLLECTORS.md`); SSRF-safe fetch (`core/pipeline/url_safety.py`); source-health page | No URL-reputation provider at all; no `website`/URL candidate; no per-finding provider label | New `backend/app/core/providers/safe_browsing.py` (httpx, no new dep) returning `ReputationResult{verdict, checked_at, provider, raw_threat_types}`. `unknown` = no key / quota / timeout. UI text: "No match in Google's lists; not a safety guarantee". Register in Source Health. |
| B | Name/handle/domain lookalike with RapidFuzz, PSL registrable domain, IDN/punycode, deceptive subdomain, scores + reasons | Rich name matcher (`engine/name_match.py`, 381 lines: homoglyph, combo, typo, allowlist, hard negatives), OSA distance (`engine/distance.py`), URL parsing (`engine/urls.py`) | No domain comparison: no registrable-domain extraction, no punycode/IDN, no hostname/subdomain/registrable split. RapidFuzz absent | New `engine/domain_match.py` using `tldextract` (offline snapshot, no fetch) + `idna` + RapidFuzz as an extra similarity signal. Do NOT replace `name_match.py` (see decision D2). |
| C | Bio/app-description analysis via Gemini (schema output, untrusted text, validation, timeouts, bounded retries, cache, local fallback), optional HF | `engine/llm.py`: `ScamIntentLLM` Protocol, LRU cache by SHA-256, untrusted-text prompt, JSON parse, OFF by default (Anthropic only); local `text_signals.py` (`analyze_bio`, `description_overlap`) is the fallback | No Gemini or HF implementation; no schema-constrained output, timeouts, retries; env names differ | Add `GeminiScamIntentLLM` and `HuggingFaceScamIntentLLM` behind the same Protocol (keeps Anthropic). Pydantic-validated response, `timeout`, max 2 retries with backoff, cache. `GEMINI_API_KEY` absent -> local `text_signals` result labelled `local_algorithm`. |
| D | Logo/icon comparison with Pillow + ImageHash, optional embedding, safe upload | Home-grown pHash/dHash (`engine/logo_similarity.py`), `ImageSimilarity` Protocol + optional `ClipSimilarity` (open_clip), safe logo upload: PNG/JPEG/WebP, re-encode, size/aspect limits, SVG refused (`core/pipeline/logo.py`, `api/brands.py`), SSRF-safe image fetch (`core/pipeline/image_fetch.py`) | ImageHash not used; no multi-logo per brand; no upload endpoint for candidate images | Add `ImageHashSimilarity` implementing the same Protocol (phash+dhash+whash via ImageHash), keep the home-grown one as default until parity tests pass (D3). Multiple brand logos (see brand registry). |
| E | Fake social account analysis from manual entry + CSV/JSON import; no claim of auto platform monitoring | Social scoring (`engine/scoring.py::score_social`), Tavily/SearXNG discovery (labelled as search, not platform monitoring), accounts list; sample data flagged `source="sample"` | No manual-entry candidate endpoint; no CSV/JSON import | `POST /api/candidates/social` (single) and `/import` (CSV/JSON, size/row caps, schema-validated). Candidates get `origin="manual"/"import"`. UI copy states data is user-supplied. |
| F | Fake app listing analysis from manual entry/CSV/JSON + authorized store provider interface | `score_app`, iTunes + Google Play collectors, `CandidateApp`, store URL parsing | No manual/import path; no explicit `AuthorizedStoreProvider` interface | Same endpoints for apps. Define `StoreProvider` Protocol in `collectors/base.py`; existing iTunes/Play adapt to it; documented stub for "authorized" providers (no scraping). |
| G | Explainable risk engine: normalized signals, configurable weights, no double counting, missing != clean, 0-100, LOW/MEDIUM/HIGH/CRITICAL, thresholds, reasons, timestamp + algorithm version, no confirm from similarity alone, analyst workflow, optional RF/XGBoost | `engine/scoring.py`: reasons list that sums to raw_total, per-category caps, publisher counted only with another signal (anti double-count), `capped` flag; `engine/pipeline.py`; statuses new/reviewing/confirmed/dismissed/cleared/marked_official + `detection_events` audit trail; "engine only scores, human approves" | Scoring weights hard-coded in constants; 3 bands at 85/70; no `algorithm_version`/`scored_at` on a result; missing evidence is silently 0 (not recorded as `unknown`); no CRITICAL; no ML | New `engine/risk_engine.py` that WRAPS the existing scorer (see D1, D4): emits normalized signals `{id, value 0-1 or null, status: present/absent/unknown, source, weight}`, config object for weights/thresholds (defaults = current numbers so scores do not change), `algorithm_version`, `scored_at`. Add `severity` column beside `band`. ML deferred to final optional package and gated on labelled data. |
| H | Brand registry CRUD: description, official domains, handles, app listings, package ids, publisher ids, logos, trusted-identifier verification status | `brands`, official accounts/apps CRUD (`api/brands.py`), logo upload, description, aliases, allowlist, `mark-official` | One brand/org only (409 on second); no official domains table; no publisher-id or multi-logo; no verification status; no DELETE/archive brand | Alembic 0005: `brand_domains`, `brand_logos`, `brand_publishers`, `verification_status` + `verified_at/by` on identifiers. Lift one-brand limit (D8). `DELETE` = soft archive. |
| I | Canonicalization, dedup, repeat scans | `detections.canonical_key`, `first_seen_at/last_seen_at/score_history`, `times_seen`, `engine/urls.py` canonicalizers | No canonical key for domains | Extend `canonical_key` with `domain:<registrable>`; reuse upsert in `services/detections.py`. |
| J | Provider interfaces labelling every finding's source (real API / local algorithm / pretrained model / synthetic) | `source` = live/sample on detections; per-reason `signal` | No per-signal provenance | `provenance` enum in each signal: `real_api`, `local_algorithm`, `pretrained_model`, `synthetic`; stored in `detections.evidence.signals[]`; shown as a chip in the UI. |
| K | `.env.example` names | `backend/.env.example` has DATABASE_URL, ANTHROPIC_API_KEY, SCAM_LLM_*, TAVILY_* ... | No GEMINI_*, GOOGLE_SAFE_BROWSING_API_KEY, HUGGINGFACE_* | Add the 5 new names, empty values, plus model ids as defaults (section 3). Never commit real values. |
| L | 7 pages | 9 live routes: dashboard, brand, social, apps, lookalike, sources, reports, settings, evaluation (`frontend/src/App.tsx`) + 9 orphaned old pages | Page set and names differ | See D6/D7: map, do not delete. |
| M | Evaluation: P/R/F1, confusion matrix, FPR, per-class, dataset/model versions, hard negatives, synthetic labelled | `engine/evaluation.py` (P/R/F1 per technique, labelled OPTIMISTIC), `engine/synthetic.py` (hard negatives incl. other-brand names), `/api/evaluation`, `EvaluationPage` | No confusion matrix, FPR, per-class, version stamps; no fan-page / authorized-reseller negatives | Extend `evaluation.py` + `synthetic.py`; version header `{dataset_version, algorithm_version, generated_at}`; every case tagged `synthetic: true`. |
| N | Security | CORS (`main.py`), `FailureLimiter` (login only), SSRF guard with IP pinning (`url_safety.py`), JWT + bcrypt + refresh rotation, audit log + CSV export, logo re-encode, prompt-injection note in `llm.py`; candidate URLs never auto-visited except reachability/logo fetch via the SSRF guard | General API rate limiting missing; prompt-injection test coverage thin; import endpoints absent; tenant isolation: single org (`context.py`) | Add `slowapi`-style or in-house per-route limiter (D9); upload/import caps; "never visit candidate URL" test (reachability and logo fetch stay behind `url_safety`, and are OFF for imported rows by default). Document auth/audit/tenant model in `docs/SECURITY.md` (production notes only, no multi-tenant build). |
| O | Tests for all | ~60 test files incl. engine, collectors, scan flow, migration, postgres | New modules uncovered | Each work package below lists its tests. |
| P | 10 phases ending in verification | Phases 1-5 done in `DECISIONS.md` | Numbering reset | Section 4 uses E1..E10 to avoid clashing with old phase numbers. |

## 2. Conflicts and decisions needing the user

Recommended answer in bold.

| ID | Conflict | Options | Recommendation |
|---|---|---|---|
| D1 | Bands: existing high/medium/low at 85/70 vs LOW/MEDIUM/HIGH/CRITICAL | (a) replace; (b) add `severity` alongside `band` | **(b).** Keep `band` and 85/70 for every existing API/UI/test. New `severity` defaults: LOW <40, MEDIUM 40-69, HIGH 70-84, CRITICAL 85+ (configurable). The mapping preserves the current cutoffs (70 and 85) so nothing flips. Confirm the 40 cutoff. |
| D2 | RapidFuzz vs home-grown OSA | replace vs add | **Add** RapidFuzz for new domain/handle similarity and as a secondary score. Keep `name_match.py`: it encodes the decided hard-negative rules (DECISIONS Phase 2) and RapidFuzz alone would flag "Acme Bakery". |
| D3 | ImageHash vs home-grown pHash/dHash | replace vs add | **Add** as a second `ImageSimilarity`; switch default only if the golden tests in `test_logo_similarity.py` and `test_sample_logo_bands.py` still pass. Otherwise keep home-grown. |
| D4 | Scorer weights from the original ScamMind brief (social 34/28/12/28/18; app 24/30/22/17/10; 85/70 bands; "publisher counts only with another signal") | must not silently change | **Freeze as `weights_v1`.** New engine loads them as defaults, stamps `algorithm_version="scammind-1.0"` on today's behaviour and any new signal (domain, Safe Browsing) gets a NEW version `2.0` with explicit weights. Safe Browsing match = its own signal, not folded into name points. Needs your approval for the new weights. |
| D5 | Anthropic optional LLM vs Gemini | | **Keep Anthropic behind the Protocol, make Gemini the documented default**, HF optional. Provider chosen by `SCAM_LLM_PROVIDER=gemini|huggingface|anthropic|off`. |
| D6 | Tailwind/Recharts (brief) vs plain CSS + custom charts (existing, matches the PDF) | | **Do not rewrite styling.** New pages use the existing `tokens.css` + per-page CSS and components. No Tailwind, no Recharts. |
| D7 | Page mapping to the 7 requested pages | Overview=Dashboard; Brand Registry=Brand Profile (extended); Social Threats=Social; App Threats=Apps; Investigations; Model Evaluation=Evaluation; Settings | **Rename labels only, keep routes** (`/dashboard` etc.). Look-alike Lab, Source Health, Reports stay as extra nav items (the user said extend, not shrink). Confirm you accept 9+ pages rather than exactly 7. |
| D8 | Website/domain threats as a third candidate kind | `category` currently `social|apps` | **Yes, add `category="web"`** (type `lookalike_domain`) so Safe Browsing + domain lookalike have a home. Needs migration (new column values only; no schema break) and a "Web Threats" tab inside Investigations or Overview. Confirm. |
| D9 | "Investigations" page vs existing status workflow (new/reviewing/confirmed/dismissed + notes + event log) | | **New page = case view over existing detections**: cross-kind list, filters, timeline, analyst notes, resolution (`resolved_takedown`, `false_positive`, `resolved_other`). Reuse `detection_events`; add `resolution` field instead of a second workflow. `old orphaned InvestigatePage.tsx` is not reused unless you ask. |
| D10 | One brand per org vs registry CRUD | | **Lift to many brands per org** (409 rule removed, tests updated). Confirm; changes `test_brand.py`. |
| D11 | Commercial use of Safe Browsing | API is non-commercial only | See section 3. If DigitalTrust is a paid/revenue product, use **Web Risk** instead. Adapter will be built provider-neutral so either works. |
| D12 | Tenant isolation / multi-user | brief asks "documented" only | **Document only**, no build. |
| D13 | Optional RF/XGBoost | | **Defer** until labelled analyst decisions exist (>= a few hundred reviewed rows); scikit-learn only (BSD), XGBoost skipped unless wanted. |

## 3. Provider and credential facts (verified 2026-10-13 unless marked)

| Item | Finding | Source |
|---|---|---|
| Safe Browsing | Two versions on the reference: **v4** (`POST /v4/threatMatches:find`, Lookup API) and **v5** (`GET /v5/urls:search`). The page does not mark v4 deprecated. UNVERIFIED which one Google recommends for new work; plan: implement v4 `threatMatches:find` (well-known request shape), keep adapter swappable to v5. Key = Google Cloud API key with Safe Browsing API enabled. | developers.google.com/safe-browsing/reference/rest |
| Safe Browsing terms | "For non-commercial use only" ("not for sale or revenue generating purposes"); commercial use -> **Web Risk API** (Google Cloud, paid). Default quota granted on enable; no numeric quota on the page (UNVERIFIED numbers; check Cloud Console). Free of cost. | developers.google.com/safe-browsing/v4/usage-limits |
| Gemini SDK | Official package **`google-genai`** (PyPI 2.30.0, Apache-2.0), `pip install google-genai`. Legacy `google-generativeai` deprecated Nov 30 2025: do not use. | ai.google.dev/gemini-api/docs/libraries |
| Gemini text model | Models page lists `gemini-3.8-flash` (newest flash), `gemini-3.6-flash`, `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`. 2.5 models only for previous users; 2.0 shut down. Plan default: **`GEMINI_MODEL=gemini-3.5-flash-lite`** (cheapest, recommended for new projects by the page). One page said `gemini-3.8-flash-lite` is not listed on pricing; do not use. Make the id configurable and verify at key-setup time. | ai.google.dev/gemini-api/docs/models, /pricing |
| Gemini embedding | Models page: **`gemini-embedding-001`** (no preview suffix; not marked stable) and `gemini-embedding-2-preview`. Pricing page instead lists `gemini-embedding-2`: **conflict, UNVERIFIED**. Plan: embeddings are optional, default off; resolve the id when the user supplies a key. | same |
| Gemini free tier | Free input/output for flash and flash-lite; but free tier content "used to improve our products" = Yes (paid = No). Do not send real customer/brand-confidential text on the free tier. No numeric rate limits on the page (UNVERIFIED). | /pricing |
| Hugging Face | Inference Providers: pay-as-you-go, monthly credits for PRO/Team; fine-grained token with "Make calls to Inference Providers". Python: **`huggingface_hub`** (2.2.0, Apache-2.0) `InferenceClient`; or OpenAI-compatible chat endpoint `https://router.huggingface.co/v1` (chat only). Model suffix policies `:fastest/:cheapest`. Feature extraction exists only for some providers (HF Inference, Scaleway, Together). `HUGGINGFACE_MODEL` has no default: user must pick one that a provider currently serves (UNVERIFIED which). Free-tier credit amount UNVERIFIED. | huggingface.co/docs/inference-providers |
| Licenses (PyPI metadata) | RapidFuzz 3.14.6 **MIT**; tldextract 5.4.0 **BSD-3-Clause**; idna 3.20 **BSD-3-Clause**; ImageHash 4.3.2 **BSD-2-Clause**; scikit-learn 1.9.1 **BSD-3-Clause**; google-genai Apache-2.0; huggingface_hub Apache-2.0; xgboost 3.4.1 Apache-2.0. No copyleft. Note ImageHash pulls numpy, scipy, PyWavelets (not checked here: UNVERIFIED, run the existing `docs/DEPENDENCIES.md` procedure after install). tldextract by default downloads the PSL: pin `suffix_list_urls=()` to use the bundled snapshot (no runtime network). | PyPI JSON |

## 4. Phased plan (work packages)

Rules for every package: all existing tests stay green; `pytest -q` and `npm test`/`tsc` run after each; existing UI untouched except additive tabs/chips; every new provider has a fake for tests and no live call in tests. Size S <= half day, M ~1 day, L 2+ days.

**E1 Decisions and foundations (S)**
- Resolve D1-D13. Add `docs/SECURITY.md` skeleton. Add deps (rapidfuzz, tldextract, idna, ImageHash, google-genai, huggingface_hub as optional in `requirements-extra.txt`; rapidfuzz/tldextract/idna/ImageHash in `requirements.txt`). Update `DEPENDENCIES.md`, `.env.example`.
- Accept: install clean; no GPL; settings load with all new vars empty.

**E2 Risk-engine skeleton, no behaviour change (M)**
- Add `engine/risk_engine.py`, `engine/signals.py`, `RiskConfig` (weights_v1 defaults), `severity_for()`, `algorithm_version`, `scored_at`; wrap `score_social/score_app`. Alembic 0005 adds `severity`, `algorithm_version`, `scored_at`, `resolution`, `provenance` in evidence.
- Tests: golden test that every existing synthetic/sample case yields identical score and band; severity boundary tests; missing evidence yields `unknown` not 0-clean; double-count guard; "never confirmed from similarity alone" (status never leaves `new` automatically).
- Accept: old suites unchanged and green.

**E3 Domain/handle lookalike (M)**
- `engine/domain_match.py`: hostname/subdomain/registrable split, punycode/IDN decode, mixed-script check, deceptive subdomain (`acme.com.evil.io`), RapidFuzz ratios; output normalized values, scores, matched components, reasons. Hard negatives: `acme.com` itself, official domains, `acmeplumbing.com`.
- Tests: table-driven incl. `xn--` homoglyphs, multi-part TLD (`co.uk`), IP hosts rejected, no network.

**E4 Safe Browsing / URL reputation (M)**
- `providers/base.py` (provenance + result envelope), `providers/safe_browsing.py`, wired as a collector-style source with status in Source Health; `unknown` when no key; timeout, quota handling; results cached per canonical URL with TTL. URLs are sent to Google, never fetched by us.
- Tests: respx/httpx mock for match, clean, 403/429 -> unknown/failed, no key, malformed body.

**E5 Text analysis providers (M)**
- `engine/llm_gemini.py`, `engine/llm_hf.py`, provider factory; schema `{scam_likelihood, categories[], impersonation_claims[], rationale}`; injection-safe prompt (delimited data, no tool use, output validated and clamped); timeout, 2 retries, cache; local fallback labelled `local_algorithm`.
- Tests: fake client; injection strings ("ignore previous...") cannot change score beyond cap; invalid JSON -> fallback; no key -> fallback.

**E6 Image similarity (S/M)**
- `ImageHashSimilarity`, multi-logo comparison (best match), upload size/type/decompression-bomb limits (`Image.MAX_IMAGE_PIXELS`) for candidate images; optional embedding stays behind the existing Clip adapter.
- Tests: identical, resized, cropped, recolored, unrelated; corrupt file; SVG rejected.

**E7 Brand registry v2 + web category (L)**
- Migration 0006 (domains, logos, publishers, verification status), `api/brands.py` CRUD extensions, soft delete, `category="web"`, canonical/dedup for domains; allowlist uses verified identifiers only.
- Tests: CRUD, dedup, repeat scans bump `last_seen_at`, one-brand 409 test updated (D10), migration up/down on SQLite and Postgres tests.

**E8 Manual entry + CSV/JSON import + store provider interface (M)**
- `POST /api/candidates/{social|app|web}` and `/import`; caps (rows, bytes, field length), strict pydantic, formula-injection-safe CSV, rows labelled `origin=import`; per-row error report; `StoreProvider` Protocol.
- Tests: valid/invalid rows, oversized file, duplicate rows, CSV with `=cmd|...` cells, imports never trigger network by default.

**E9 Frontend (L)**
- Nav relabel per D7; Brand Registry tabs (domains, logos, publishers, verification); Social/Apps: add-candidate + import dialog, provenance chips, severity badge beside band, missing-evidence "unknown" rows; new Investigations page (case list, timeline, resolution, false-positive); Model Evaluation: confusion matrix + per-class table + version header + SYNTHETIC badge; Settings: provider status (key present yes/no only, never the value), thresholds/weights read-only first.
- Plain CSS, existing components. Vitest for each new page; existing `App.test.tsx`, `SourceHealthPage.test.tsx` stay green. Visual check against the PDF screens.

**E10 Evaluation, security hardening, verification (L)**
- Evaluation extensions (confusion matrix, FPR, per-class, versions, hard negatives: fan pages, authorized resellers, unrelated similar names; all tagged synthetic). General rate limiter on write/import/upload routes; CORS review; prompt-injection regression set; SSRF tests for new URL inputs; `docs/SECURITY.md` finalized (auth, audit, tenant isolation notes). Optional: RF baseline in `scripts/train_ml.py`, off by default, only if labelled data exists.
- Final verification: full pytest (SQLite + Postgres if available), frontend tsc/vitest/build, migration up/down, `scripts/evaluate.py`, live-check script with keys, update `API.md`, `DECISIONS.md`, `COLLECTORS.md`, `FRONTEND_CONTRACT.md`, `DEPENDENCIES.md`.

### Progress (updated 2026-10-14)

| Package | State | Notes |
|---|---|---|
| E1 Decisions and foundations | DONE (partly) | Decisions recorded in DECISIONS.md. Deps: tldextract, rapidfuzz, idna in `requirements.txt`; ImageHash in `requirements-extra.txt` (unused until E6); google-genai / huggingface_hub NOT added yet (E5). `.env.example` has the Safe Browsing and severity names; Gemini/HF names come with E5. `docs/SECURITY.md` skeleton not written. |
| E2 Risk engine skeleton | DONE | `engine/risk_engine.py` (severity, algorithm_version, scored_at, signals, provenance, weights_v1 frozen, golden test). Migration 0005 adds severity, algorithm_version, scored_at. `resolution` column and `provenance` in `evidence` deliberately NOT done (signals live in `details`; `resolution` belongs to Investigations, E9). |
| E3 Domain lookalike | DONE | `engine/domain_match.py`: URL normalization, IDN/punycode, registrable domain, deceptive subdomain, userinfo trick, TLD swap, homoglyph, RapidFuzz (additional signal). Handle (social) lookalike with RapidFuzz not done. |
| E4 Safe Browsing | DONE (mocked HTTP only) | `providers/safe_browsing.py`, wired into the scan for web candidates, budget and 429 handling, `GET /api/sources` > `reputation`. Not run against the live API (needs a key). Source Health row not added (reported as a block on `/api/sources`). Web candidate category added end to end (D8) in the API; no UI list yet. |
| Defects D3, D4, D5, D6 | FIXED | See FEATURE_STATUS.md. |
| E5 Text providers | DONE (mocked only) | `app/core/providers/text/`: local baseline, Gemini (google-genai), Hugging Face (optional), cache, retries, injection-safe prompt, `/api/providers`. Anthropic removed. Embeddings: class exists, OFF, not wired into scans. Not run live (needs keys). |
| E6 Images | DONE | `engine/image_hashes.py` (local hashes, optional ImageHash), multi-logo, safe upload. Default scoring backend unchanged. |
| E7 Registry v2 + intake | DONE (API only) | Migration 0006, description, archive, publishers, verification, manual entry, CSV/JSON import. UI for these NOT built. |
| E8-E10 | DONE (ML classifier deferred) | Manual entry and import (planned in E8) were delivered with E7. |

## 5. What the user must provide, and when

| Item | When | Notes |
|---|---|---|
| Answers to D1, D4, D7, D8, D10, D11 | before E2 | blocks schema/severity |
| `GOOGLE_SAFE_BROWSING_API_KEY` (Google Cloud project, Safe Browsing API enabled) or Web Risk project | before E4 live check | Plan works without it (`unknown`). Commercial -> Web Risk. |
| `GEMINI_API_KEY` (AI Studio) + confirm `GEMINI_MODEL` | before E5 live check | Free tier trains on inputs; consider billing for real data. |
| `HUGGINGFACE_API_TOKEN` + a `HUGGINGFACE_MODEL` you choose | optional, E5 | fine-grained, "Inference Providers" permission |
| Real labelled analyst decisions | only for optional ML in E10 | |
| Approval of new signal weights (domain, Safe Browsing) | before E4/E3 merge | |
| Keys go into `backend/.env` yourself; I never read or print it | | |

## 6. Risks

- Safe Browsing is non-commercial; using it in a revenue product breaches terms. Mitigation: provider-neutral adapter, Web Risk swap.
- Model ids drift fast (the docs showed conflicting embedding ids). Mitigation: ids are env config, validated at startup in Settings status, never hard-coded.
- Weight changes silently altering scores. Mitigation: `weights_v1` golden tests, version stamp on every score.
- Gemini/HF output is untrusted: schema validation, clamped contribution (existing bio cap of 28), fallback.
- Free-tier Gemini data-use; do not send confidential text without billing.
- Scope: brief is ~3x current feature set; E7 and E9 are the largest and touch existing tests (one-brand rule, nav labels).
- Lifting the one-brand limit may affect dashboard/report queries that assume a single brand (`services/dashboard.py`, `reports.py`, `context.py`): audit before E7.
- tldextract PSL goes stale; pin snapshot and note refresh date.
- Evaluation numbers on synthetic data are optimistic (already stated in `engine/evaluation.py`); UI must keep that label.
- Another agent is using the stack: the plan changes nothing until you approve it.
