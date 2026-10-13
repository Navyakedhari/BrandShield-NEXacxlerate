# ScamMind AI: User Guide

This guide is for the people who **use** ScamMind AI (analysts, brand and legal teams) and for the **operator** who installs and runs it.
Everything here was checked by running the app. All screenshots and step-by-step examples use a **fictional** brand called **Acme Bank**
with **sample data**; nothing in them is a real company or account.

Contents

1. [What the app is (and is not)](#1-what-the-app-is-and-is-not)
2. [Install and run (Windows, no Docker)](#2-install-and-run-windows-no-docker)
3. [Sign in](#3-sign-in)
4. [The five-minute tour with Acme Bank](#4-the-five-minute-tour-with-acme-bank)
5. [Page by page](#5-page-by-page): Setup (5.1), Dashboard (5.2), Brand Profile (5.3), Social Monitoring (5.4), App Store Monitoring (5.5), Look-alike Lab (5.6), Source Health (5.7), Reports (5.8), Settings (5.9), Evaluation (5.10)
6. [How scoring works](#6-how-scoring-works-in-plain-language)
7. [The human-approval workflow](#7-the-human-approval-workflow)
8. [Live, Sample or Auto data](#8-live-sample-or-auto-data)
9. [Search credits (Tavily)](#9-search-credits-tavily)
10. [When a source is down](#10-when-a-source-is-down)
11. [Using your real brand: what to expect](#11-using-your-real-brand-what-to-expect)
12. [Troubleshooting](#12-troubleshooting)
13. [Limitations](#13-limitations)
14. [Glossary](#14-glossary)

---

## 1. What the app is (and is not)

ScamMind AI helps a company find **fake social profiles, fake brand pages and look-alike mobile apps** that copy its brand.

1. You tell it what is **official**: your brand name and spellings, logo, domains, official social handles and official apps. This is your **allowlist**.
2. A **scan** looks in public places (a web search service and the Apple App Store) for things that use your name, then **throws away** anything exactly on your allowlist.
3. Everything left is **scored** from 0 to 100, and **every point has a written reason**.
4. A **person** reviews each detection: confirm, keep reviewing, dismiss, or mark as official.
5. A person creates a **report** draft and **approves** it.

What it does **not** do:

* It never contacts a platform, files a takedown, or sends an e-mail or webhook. A report is a file you download. "Approve" only records that a person approved it.
* A detection is a **candidate for review**, not proof and not an accusation.
* It cannot see everything. Search-based discovery finds pages that mention your brand; it can miss fake profiles (see [section 11](#11-using-your-real-brand-what-to-expect)).
* Notifications (e-mail, digest, webhook) are saved as preferences but **not delivered**.

---

## 2. Install and run (Windows, no Docker)

You need **Python 3.11 or newer** and **Node.js 22 or newer**. Nothing else: the database is a SQLite file created for you.

Open **PowerShell** in the project folder.

**Step 1. Server (the API)**

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
copy .env.example .env
```

Open `backend\.env` in Notepad and fill in three values:

| Name | What to put | Example |
|---|---|---|
| `ADMIN_EMAIL` | The e-mail you will sign in with | `you@yourcompany.com` |
| `ADMIN_PASSWORD` | A strong password you choose (this is also the first account's password) | (your own) |
| `JWT_SECRET` | A long random text, 16 characters or more. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(48))"` | (paste the output) |

Keep the other lines blank. A blank line means "not set, use the default". **Never share or commit `.env`.**

Then create the database tables and start the API:

```powershell
.\.venv\Scripts\python -m scripts.migrate
.\.venv\Scripts\python -m uvicorn app.main:create_app --factory --port 8000
```

Leave this window open. Check it works by opening <http://127.0.0.1:8000/health>: you should see `"status":"ok"` (or `"degraded"` on a brand-new install: that only means no scan has run yet).

**Step 2. Frontend (the website)**

Open a **second** PowerShell window:

```powershell
cd frontend
npm install
npm run dev
```

Open **<http://localhost:5173>** in your browser. Use exactly `localhost`, not `127.0.0.1` (see [troubleshooting](#12-troubleshooting)).

**Good to know**

* After you edit `backend\.env` you must **stop the API (Ctrl+C) and start it again**. The file is only read at start.
* Your data lives in `backend\data\` (the database `scammind.db`, logos, evidence snapshots, report files). Back this folder up.
* The API's own documentation page is <http://127.0.0.1:8000/docs>.
* Optional self-check that proves the detection logic still works: `.\.venv\Scripts\python -m scripts.canary` (prints PASS lines, takes a fraction of a second, makes no network calls).

---

## 3. Sign in

![Sign in page](img/01-login.png)

1. Open <http://localhost:5173>. You land on **Sign in**.
2. Type the `ADMIN_EMAIL` and `ADMIN_PASSWORD` from `backend\.env`.
3. Leave **Keep me signed in on this device** ticked if this is your own computer. Untick it on a shared one: the session then ends when you close the tab.
4. Press **Sign in**.

What you may see:

| Message | Meaning |
|---|---|
| **Incorrect email or password** | Wrong e-mail or password (the message never says which). |
| **Too many failed attempts. Try again later.** | 5 wrong tries for the same e-mail: you are blocked for about 15 minutes. Restarting the API clears the block. |
| **Cannot reach the server. Check your connection and try again.** | The API is not running, or the browser is not allowed to talk to it. See [troubleshooting](#12-troubleshooting). |

To leave: click your name (top right) and choose **Sign out**.

**Roles**

| Role | Can do |
|---|---|
| **viewer** | Look at everything. Every action button is greyed out and a "Viewer" note is shown. |
| **analyst** | Everything a viewer can, plus run scans, edit the brand profile, review detections, make reports, run the Look-alike test cases. |
| **admin** | Everything an analyst can, plus Settings, the data mode, the Look-alike test-case list, health details. |

The first admin is created from `backend\.env`. **The app has no screen to add other users** yet; ask your developer to add them.
"Forgot password?" is not available: ask the administrator.

---

## 4. The five-minute tour with Acme Bank

Do this once with **fictional sample data** so you know what everything looks like before using a real brand.

1. In **Settings > Account**, set **Data mode** to **Sample** and press **Save settings** (admin only). Do this first: if a search key is present, "Auto" would use real searches.
2. Go to **Setup** (`/setup`) and follow [section 5.1](#51-setup-first-run).
3. Fill in **Brand Profile** with the Acme Bank inputs in [section 5.3](#53-brand-profile).
4. On the **Dashboard** press **Run scan now**. In under a second you get about **17 sample detections** (in our run: 12 social and 5 apps; 6 high, 4 medium and 7 low risk).
5. Open **Social Monitoring**, click a detection, read **Why it scored**, then press **Confirm impersonation**.
6. Open **Look-alike Lab** and try the names in [section 5.6](#56-look-alike-lab).
7. Open **Reports**, press **Generate report**, then **Preview** and **Approve**.

Every page in sample mode shows a **Sample data** badge at the top right and a note "Sample data for the fictional brand Acme Bank. Not real detections."

---

## 5. Page by page

The left menu (sidebar) order is: Dashboard, Brand Profile, Social Monitoring, App Store Monitoring, Look-alike Lab, Source Health, Reports, then Settings and Support at the bottom. **Setup** opens from the brand card at the top of the sidebar ("Set up your brand profile") or from <http://localhost:5173/setup>. **Evaluation** opens from Settings.

Three small things in the sidebar and header are decorative and do nothing yet: the **Search detections** box, the **bell**, and **Support** (it only shows a pop-up).

### 5.1 Setup (first run)

![Setup step 1](img/02-setup.png)

**What it is for:** the quickest way to create your brand: name, other spellings, logo, then a first scan.

**Steps**

1. **Step 1 of 3: Brand.** Type:
   * Company or brand name: `Acme Bank`
   * Other names people use (optional), separated by commas: `Acme, AcmeBank, Acme Bank India`
   * Press **Upload** and pick a logo: a PNG, JPEG or WebP file, **at least 256 x 256 pixels, roughly square, at most 2 MB**. Wrong files are refused straight away with a reason ("Logo must be at least 256 x 256 pixels", "Logo must be roughly square", "Logo must be a PNG, JPEG or WebP image", "Logo is larger than 2 MB").
   * Press **Continue**.
2. **Step 2 of 3: Official assets.** This step only **shows** the accounts and apps already registered ("None added yet" at first). Add them on the **Brand Profile** page (next section), then come back or go straight to the dashboard.
3. **Step 3 of 3: First scan.** Shows how many sources are ready. **Launch Protection Dashboard** starts a scan at once.

**Tip:** the scan can only skip your official accounts if you have already listed them. So finish **Brand Profile** first, then run the scan from the Dashboard. If a key for live search is present and the data mode is Auto, this first scan is a **real** one and uses search credits ([section 8](#8-live-sample-or-auto-data)).

"Skip setup for now" (top right) goes to the dashboard.

### 5.2 Dashboard

![Dashboard](img/04-dashboard.png)

**What it is for:** a one-page summary of risk for your brand, and the place to start a scan.

| Part | What it shows |
|---|---|
| Top banner | The last scan time and "8 of 8 sources online". **Run scan now** starts a scan (analyst or admin). **Edit brand profile** opens Brand Profile. |
| Tiles | **Total detections**, **Social impersonations**, **Suspicious apps** (each with "this week" and a small trend line) and **Official assets protected** (your accounts plus apps on the allowlist). |
| Impersonation types | The four kinds: Scam account, Fake app, Fake profile, Fake brand page. |
| Where we found them | Detections per platform. |
| Needs review | The five highest-risk items still waiting for a person, with a High / Medium / Low label. |
| Recent detections | Newest items with their match reasons and score. |
| Why it was flagged | The reasons behind the top detection. **Review detection** opens it. |
| Source health | The state of each source. **Manage** opens Source Health. |

Important: the totals count detections that are **New, Reviewing or Confirmed**. Items you **Dismiss** or mark **official** drop out of the totals (the Social and App pages still list dismissed ones, tagged Dismissed).

A scan shows a short message when it ends, for example "Scan finished: 17 new detections, 8 of 8 sources online."

### 5.3 Brand Profile

![Brand Profile](img/03-brand-profile.png)

**What it is for:** telling the app what is **official**. Everything else is compared against this. Anything on the allowlist is **never flagged**, and only an **exact** handle, URL or package id counts (a similar name never does).

**Enter this for Acme Bank**

*1. Brand identity*

| Field | Type this |
|---|---|
| Company or brand name | `Acme Bank` |
| Industry | `Banking` |
| Aliases and spellings people use (type, press Enter) | `Acme`, `AcmeBank`, `Acme Bank India` |
| Keywords to watch | `acme bank`, `acme netbanking`, `acme rewards` |
| Official domains | `acmebank.example`, `netbanking.acmebank.example` |
| Official logo | press **Replace logo** and pick a square PNG 256 px or larger |

Domains must be a plain hostname such as `acmebank.example`: **no** `https://`, no path, no spaces. A wrong one is refused with "use only the hostname, e.g. example.com". Press **Save brand profile** at the bottom. Note: if one field is invalid, **nothing** is saved and typed changes are lost on reload, so save in small steps.

*2. Official social accounts*: choose a platform, type the handle, press **Add to allowlist** (each takes about two seconds because the address is checked):

| Platform | Handle |
|---|---|
| Instagram | `@acmebank` |
| X | `acmebank` |
| Facebook | `acmebank.official` |
| YouTube | `acmebank` |
| LinkedIn | `acmebank` |

Rules: 3 to 30 letters, digits, dots or underscores; a leading `@` is removed. **LinkedIn and YouTube** also accept hyphens (for example `acme-bank`). Other examples:

| You type | Result |
|---|---|
| `acmebank` on Instagram a second time | "acmebank is already an official instagram account" |
| `ab` or `bad handle!` | "Handle must be 3 to 30 characters: letters, digits, dot or underscore" |
| A handle that does not exist | Added, but marked **Unreachable** with a warning such as "The URL answered with HTTP 404". **Verified only means the address answered**, and some sites answer for any name, so it does not prove the account exists. |

The platforms offered are Instagram, X, Facebook, LinkedIn and YouTube (not Telegram).

*3. Official mobile apps*: press **Add an official app** and fill:

| Field | Type this |
|---|---|
| App Name | `Acme Bank Mobile` |
| Package or Bundle ID | `com.acmebank.mobile` |
| Publisher | `Acme Bank Ltd` |
| Store | `Google Play` |

then **Confirm App**. The publisher matters: an app from a different publisher is scored as suspicious. A Google Play package id looks like `com.example.app` (dot-separated, each part starts with a letter); an App Store bundle id is the same shape. The same id cannot be added twice, so an app on both stores can be registered for **one** store here. "Import from store listing URL" is not available yet.

**Profile health** (right side) is a score out of 100 with a reason for anything missing: name and aliases 20, keywords 10, logo 20, domains 15, accounts 15, apps with publisher 10, account addresses reachable 10. After the inputs above it shows **100%**.
The **Allowlist** card counts your accounts, apps and domains.

Changes apply to the **next** scan.

### 5.4 Social Monitoring

![Social Monitoring](img/05-social.png)

**What it is for:** reviewing suspect Instagram, Facebook, X, Telegram and LinkedIn profiles and pages.

**Steps**

1. Use the chips at the top to look at one platform, and the tabs (**All**, **Fake profiles**, **Fake brand pages**, **Scam accounts**) to filter by type. The number is the count.
2. Click a row in **Detections** (sorted by risk, highest first). The right-hand panel shows:
   * **Official vs suspect** side by side, with the logo match percentage when there is one;
   * **Why it scored**: every signal with its points;
   * **Evidence kept**: the page address, when it was captured and a **SHA-256** fingerprint of the saved data (see glossary);
   * four buttons.
3. Decide with one of the buttons (analyst or admin):

| Button | What it does |
|---|---|
| **Confirm impersonation** | Status becomes **Confirmed**. It can go into a report. |
| **Keep reviewing** | Status becomes **Reviewing**. You are still looking. |
| **Dismiss as false positive** | Status becomes **Dismissed**. It stays in the list, tagged Dismissed, and a later scan will not bring it back to New. |
| **Mark as official** | Adds this handle to your allowlist (Brand Profile) and removes it from the list. Future scans skip it. Use it when you find one of your own accounts. |

4. **Scan social platforms now** runs a scan of social sources only.

Below the list, **Excluded as official** shows what your allowlist removed before scoring ("Official · not flagged"), and **False-positive guard** shows names the engine cleared on its own.

Status words: **New** (nobody looked yet), **Reviewing**, **Confirmed**, **Dismissed**. Nothing is ever sent to a platform. A person confirms every detection.

### 5.5 App Store Monitoring

![App Store Monitoring](img/06-apps.png)

Same layout as Social Monitoring for apps: chips for **Google Play**, **Apple App Store** and **Third-party APK sites**, and a list of suspicious apps. The panel compares your official app and the suspect (package id, publisher, store, icon match), lists the reasons, and offers **Confirm fake app**, **Keep reviewing**, **Dismiss as false positive**, **Mark as official**. Only an app on Google Play or the App Store can be marked official; a third-party APK listing never can.

### 5.6 Look-alike Lab

![Look-alike Lab](img/07-lookalike.png)

**What it is for:** typing any name and seeing at once whether the matcher would flag it, and why. It uses the same rules and the same Settings as a real scan, changes no data, and is the best way to learn the matcher.

**Steps**

1. Open **Look-alike Lab**. The top says "Comparing against: Acme Bank".
2. Type a name (or press one of the example chips).
3. Read the result card: **Flagged**, **Official**, or **Not flagged**, the **name match %**, the technique(s) found, and **How the name was normalized** (steps 1 to 5).
4. Press **Run test cases** to run the 12 built-in cases: the badge shows how many pass ("12 of 12 pass" with the Acme Bank profile above).

**Try these (Acme Bank profile from section 5.3).** Results we observed:

| # | Type this | Verdict | Match | Why |
|---|---|---|---|---|
| 1 | `Acme Bank` | Official | 100% | The exact brand name. (A real scan still reviews it unless its handle is on the allowlist.) |
| 2 | `AcmeBank` | Official | 100% | Exact alias / allowlisted handle. |
| 3 | `@acmebank` | Official | 100% | Allowlisted handle. |
| 4 | `acmebank.official` | Official | 92% | Allowlisted Facebook handle. |
| 5 | `Acme` | Official | 100% | An alias counts as the brand's own name. |
| 6 | `Acme Bank Support` | Flagged | 92% | Added word "support". |
| 7 | `Acme Bank Customer Care` | Flagged | 92% | Added scam-flavoured words. |
| 8 | `acmebank kyc update` | Flagged | 92% | Added word "kyc". |
| 9 | `acmebank-helpdesk` | Flagged | 92% | Added word "helpdesk". |
| 10 | `acmebank giveaway` | Flagged | 92% | Added word "giveaway". |
| 11 | `Acme Bank Rewards Club` | Flagged | 92% | Added words. |
| 12 | `acmebank_official` | Flagged | 92% | "official" added to the name (not on the allowlist). |
| 13 | `acme-bank` | Flagged | 96% | Separator added. |
| 14 | `A C M E Bank` | Flagged | 96% | Spaces between letters. |
| 15 | `a.c.m.e.b.a.n.k` | Flagged | 96% | Dots between letters. |
| 16 | `acm3bank` | Flagged | 96% | Digit instead of a letter. |
| 17 | `4cmebank` | Flagged | 96% | Digit instead of a letter. |
| 18 | `acrnebank` | Flagged | 96% | "rn" looks like "m". |
| 19 | `Acrne Bank` | Flagged | 96% | "rn" looks like "m". |
| 20 | `Acme Bnak` | Flagged | 88% | Two letters swapped. |
| 21 | `acmebnk` | Flagged | 88% | A letter missing. |
| 22 | `Acne Bank` | Flagged | 88% | One letter changed (a real company with this name would be a false positive: dismiss it). |
| 23 | `acmebank123` | Flagged | 84% | Numbers added. |
| 24 | `Acme Bank 2024` | Flagged | 84% | Numbers added. |
| 25 | `Acme Banking App` | Flagged | 86% | Extra words. |
| 26 | `aсmebank` (the first "с" is a Cyrillic letter) | Flagged | 96% | Look-alike character (homoglyph). |
| 27 | `the acme bank` | Not flagged | 62% | Only a partial match: goes to "review" at half weight, not flagged on its own. |
| 28 | `acmebankfans` | Not flagged | 62% | Fan-style extra word, not an impersonation word. |
| 29 | `Acme Bakery` | Not flagged | 30% | A different business. |
| 30 | `Acme Hotels` | Not flagged | 30% | A different business. |
| 31 | `Acme Weather` | Not flagged | 30% | A different business. |
| 32 | `Acme Motors` | Not flagged | 30% | A different business. |
| 33 | `Acme Cafe` | Not flagged | 30% | A different business. |
| 34 | `Mr Acme` | Not flagged | 30% | Brand word without "bank". |
| 35 | `Bank of Acme` | Not flagged | 30% | Different word order. |
| 36 | `Acmeville Bank` | Not flagged | 30% | A different name. |
| 37 | `Apex Bank` | Not flagged | 22% | Another bank. |
| 38 | `Axis Bank` | Not flagged | 22% | Another bank. |

The Lab ignores which platform a name came from; and when you register an alias (such as `Acme`) a name equal to it counts as "official name".

In **Test cases**, admins can add or remove cases through the API (`POST /api/lookalike/test-cases`, `DELETE /api/lookalike/test-cases/{id}`); the page itself shows and runs them. If you register `acmebank_official` as an official handle, the built-in case with that name will then (correctly) say Official and show a fail.

### 5.7 Source Health

![Source Health](img/08-source-health.png)

**What it is for:** seeing which data sources work, and what happens when one does not.

| Part | Meaning |
|---|---|
| Tiles | **Online**, **Slow** (answers late, partial results kept), **Unavailable** (cached results shown), **Next scan**. |
| Sources list | Instagram, Facebook, X, Telegram, LinkedIn, Google Play, Apple App Store, Third-party APK sites, each with status, a message, last sync and how many were found. |
| **Monitor** checkbox | Untick to stop scanning that platform. It is saved at once. |
| **Retry now** | Appears on a source that is **slow or unavailable**. It runs that source again immediately, ignoring the wait. **It uses search credits** (see [section 9](#9-search-credits-tavily)): do not press it just to see what happens. |
| **When a source is unavailable** | The rules: the scan still finishes; cached results are labelled with their age; the app retries by itself after 1, 5 and 15 minutes, then hourly, backing off to every 6 hours; you can retry by hand or pause the source. |
| **Search credits (Tavily)** | "N of 900 credits used in <month> . M left". In Sample mode it says no credits are used. |

Statuses: **Online**, **Slow**, **Unavailable**.

### 5.8 Reports

![Reports](img/09-reports.png)

**What it is for:** a file you can hand to a team or a platform's trust-and-safety form. **Nothing is sent anywhere.**

**Steps**

1. Choose a **Date range** (Last 7 days, Last 30 days, Quarter to date).
2. Tick the **types** to include (Fake profiles, Fake brand pages, Scam accounts, Fake apps).
3. Choose a **Status** (All statuses, New only, Reviewing only, Confirmed only). For a report meant for a platform choose **Confirmed only**.
4. Keep **Include evidence** ticked to add page addresses, capture times and SHA-256 hashes.
5. Choose the format: **Printable report** (opens as a page; use your browser's Print, then Save as PDF) or **CSV data**. There is no built-in PDF.
6. Press **Generate report**. This creates a **draft**. A preview with counts and a table appears, and a row under **Recent exports**.
7. Use **Preview** to read it, **Download** or **As CSV** to save it, and **Approve** to record that a person approved it (one approval per report). Viewers cannot generate or approve.

Reports from sample data carry a sample-data banner. Text from the internet (names, bios) is shown as plain text in the report, not as page code, and CSV cells that could be read as formulas are prefixed with an apostrophe.

### 5.9 Settings

![Settings, Monitoring tab](img/10-settings.png)

Only an **admin** can change settings (others see them greyed out). Press **Save settings** at the bottom ("Saved successfully"); **Cancel** discards.

**Monitoring tab**

| Setting | What it does |
|---|---|
| How often to scan | Every 5 minutes, Every 15 minutes, Hourly, Daily (default). The scheduler runs inside the API. It is **on by default** and starts a scan soon after a brand exists, so in live mode check your credits first. With search credits the interval is stretched automatically to spread them over the month. The text under the control says when the next scan is due, or "The scheduler is disabled" when `SCHEDULER_ENABLED=false`. |
| Social platforms / App stores | Tick which platforms to scan (the same switches as **Monitor** in Source Health). |
| Detection sensitivity | A slider from 50 to 95, default **75**. **Higher means more sensitive** (a lower score bar): the lowest score that still goes to review is **100 minus the slider value** (75 means 25; 60 means 40; 95 means 5). The sentence on the page, "Send to review when risk score is at least 75", is worded the opposite way round and is misleading: trust the rule above. A note under the slider says how many stored candidates would be sent to review at that level. A name the matcher **flags outright** always goes to review; the slider only moves the bar for weaker evidence, so with the sample data moving it changes nothing. |
| Look-alike rules | Five switches: Character swaps (rn for m), Digits and symbols for letters, Added words, Spacing and separators, Transposed or missing letters. If a name's only technique is switched off it is downgraded to "review". |
| Impersonation words | Words that make an added word suspicious: `support`, `helpline`, `official`, `refund`, `rewards`, `kyc` by default. Add your own (for example `scheme`) and press Enter. They also change the Look-alike Lab. |

**Notifications tab**

![Settings, Account tab](img/11-settings-account.png)

Three switches (e-mail alerts, weekly digest, Slack or other webhook). The page itself says **"Preferences are saved, but nothing is delivered yet."** A webhook address must start with `https://`.

**Account tab**

* Who you are and your role.
* **Export Audit Log**: downloads `audit-log.csv`, a list of who did what and when (sign-ins, failed sign-ins, brand edits, logo changes, scans, review decisions, reports, settings changes).
* **Data mode**: **Live**, **Sample** or **Auto** (see [section 8](#8-live-sample-or-auto-data)).
* **Open the evaluation** link.

### 5.10 Evaluation

![Evaluation](img/12-evaluation.png)

**What it is for:** how well the **name matcher** performs, honestly labelled.

* **Synthetic** numbers (precision, recall, F1 by technique) come from names the app generates itself with the same tricks it looks for. The page says so: **"Synthetic numbers are optimistic."** They show consistency, not real-world accuracy.
* **Labelled look-alike test names**: the Look-alike Lab cases and how many pass.
* **Real results: reviewer decisions**: precision = confirmed / (confirmed + dismissed), from **your** decisions. This is the only real accuracy number, and it needs enough decisions to mean anything.

**Re-run evaluation** recomputes it.

There is also an optional "AI text check" for scam wording in bios. It is **off** by default and has no switch in the website: it needs `SCAM_LLM_ENABLED=true` and a `GEMINI_API_KEY` in `backend\.env` (see the AI text providers section), is never used on sample data, and the app works fully without it.

---

## 6. How scoring works in plain language

A candidate is first compared with your allowlist. If its handle, URL or package id is **exactly** on it, it is dropped. Otherwise it earns points from several signals. **The total is the sum of the points, up to 100**, and the **Why it scored** list shows each signal.

**Social profiles and pages** (maximum points per signal)

| Signal | Max | What earns points |
|---|---|---|
| Name similarity | 34 | The name matches yours after tricks are undone (look-alike letters, digits, spacing, swapped or missing letters, added words). |
| Logo match | 28 | The profile picture looks like your logo: similarity 55 or more starts earning points, 80 or more means "looks like a copy". |
| Added words | 12 | Scam-flavoured words added to your name (support, kyc, refund, giveaway ...). |
| Bio content | 28 | Scam wording in the bio: KYC lure (9), asks for a fee (12), "DM us" (5), WhatsApp or Telegram contact (6), cashback or giveaway promises (8), urgency (5), "verify your account" (8), UPI, wallet or OTP requests (10), guaranteed returns (10), link shorteners (5). |
| Account signals | 18 | Very new account (up to 6), few posts (3), following far more than followers (4), not verified (2), empty bio (1), link to a non-official site (2). |

**Apps**

| Signal | Max | What earns points |
|---|---|---|
| Name similarity | 24 | As above. |
| Icon match | 30 | The icon looks like yours. |
| Publisher | 22 | A different publisher (counts only if name, icon or description already matched your brand). |
| Description overlap | 17 | Copied wording. (Not currently fed with your official description, so it does not score.) |
| Store signals | 10 | Listed on a third-party APK site (4), few installs (3), brand-new listing (2), few ratings (1). |

**Risk bands**

| Score | Band | Meaning |
|---|---|---|
| 85 to 100 | **High** | Several strong signals together. Review first. |
| 70 to 84 | **Medium** | Worth a look soon. |
| below 70 | **Low** | Weak or partial resemblance. Often legitimate. |

Whether something becomes a detection at all: it needs at least one signal tied to **your** brand (name, logo or icon), and either its score reaches the sensitivity bar (score 25 at the default) or its name was flagged outright. Candidates with no resemblance are skipped ("pre-filtered"). Weak matches the engine decides are harmless are stored in the cleared (false-positive) list.

A high score does not prove fraud and a low one does not prove safety. It is a way to sort your reading list.

---

## 7. The human-approval workflow

1. A scan creates detections with status **New**.
2. A person opens one and decides:
   * **Keep reviewing** if more checking is needed;
   * **Confirm** if it really is an impersonation;
   * **Dismiss** if it is harmless;
   * **Mark as official** if it is actually yours (it goes onto the allowlist).
3. Later scans **keep your decisions**: a confirmed, dismissed or reviewing item is not reset, and an item marked official is skipped.
4. A person generates a **report draft** from confirmed items, reads the preview, and **approves** it.
5. A person (outside this app) decides what to do next, for example sending the report to a platform. The app does nothing on its own.

Every step is written to the **audit log** with who did it and when.

---

## 8. Live, Sample or Auto data

Set it in **Settings > Account > Data mode** (admin).

| Mode | What it does |
|---|---|
| **Sample** | Only fictional sample candidates, built to look like the brand you entered (strong, medium, weak look-alikes, harmless names, apps). No network, no credits. Every page shows **Sample data**. Use it to learn and to demo. |
| **Live** | Only real sources: Tavily web search, Apple iTunes App Store search, and the optional ones. If a source cannot be reached it shows as unavailable; sample data is never mixed in. |
| **Auto** (default) | **Live** if a live source is set up (a Tavily key, a SearXNG address or the Google Play package), otherwise **Sample**. Check the line under the buttons: it says what Auto turned into, for example "Auto: live because configured: Tavily key". |

Sample and live records are kept apart: switching mode changes which set you see; nothing is deleted.

To go live: put `TAVILY_API_KEY=...` in `backend\.env`, restart the API, choose **Live**, and run a scan.
To make sure nothing real is ever contacted, choose **Sample**.

The **Sample data** badge appears in the header whenever the data is sample, and is absent in live mode.

---

## 9. Search credits (Tavily)

Real web searches cost **credits** at Tavily. One normal ("basic") search costs **1 credit**; the free plan gives 1,000 a month.

* A scan runs up to `TAVILY_MAX_QUERIES_PER_SCAN` searches (**12** by default). So one live scan costs **up to 12 credits**, plus a free iTunes pass.
* The guard `TAVILY_MONTHLY_BUDGET` (default **900**) is checked **before every call**. When it is used up, no call is made and the source shows "Tavily monthly budget reached (N of N credits used) ... No call was made."
* The meter is on **Source Health**: "N of 900 credits used in 2026-10 . M left". It is stored in your database, so it survives restarts, but it is per database.
* Things that spend credits: every live scan (manual or **scheduled**), **Retry now** on a Tavily-backed source, the first scan from Setup when mode is Auto and a key exists.
* Things that do not: Sample mode, the Look-alike Lab, reports, a rejected key (401), a blocked call.
* iTunes search needs no key and no credits (about 20 calls a minute; a scan takes 20 seconds or so for it).

To save credits: lower `TAVILY_MAX_QUERIES_PER_SCAN` (for example 8), set the schedule to Daily or turn the scheduler off (`SCHEDULER_ENABLED=false`), and keep aliases few. Edit `backend\.env` and restart the API.

---

## 10. When a source is down

Look at **Source Health**: the row says why in plain words. Common ones:

| Message | What to do |
|---|---|
| "Tavily rejected the API key (401)." | Check `TAVILY_API_KEY` in `backend\.env`, then restart the API. |
| "Tavily monthly budget reached ... No call was made." | Wait for next month, or raise `TAVILY_MONTHLY_BUDGET` if your plan allows. |
| "Tavily is rate limiting requests (429)" / "usage limit reached (432/433)" | Wait, or upgrade the plan. The app waits by itself. |
| "Tavily could not be reached" / "did not answer in time" | Network problem. It retries by itself. |
| "The optional google-play-scraper package is not installed" | Expected. Google Play is not searched by the optional adapter (Tavily may still find Play pages). Install only after reviewing the package's licence. |
| SearXNG "could not be reached" | Only if you set `SEARXNG_BASE_URL`. Check that address. |
| "Waiting to retry (back-off)" | The app is pausing after failures. **Retry now** forces it (costs credits for Tavily). |

What the app does for you: the **scan still finishes** with the other sources, saved results from earlier are shown **labelled with their age**, and it retries automatically after 1, 5 and 15 minutes, then hourly, and at most every 6 hours. If every source is down the scan ends as failed with "No data source was available" and nothing is lost.

A scan that ends as **partial** means "some sources were slow or down": the message says which. Live scans of many queries are often marked slow only because they take more than 4 seconds; that is not an error.

---

## 11. Using your real brand: what to expect

**How to add your brand**

1. Choose **Live** only after the credit steps in section 9 are clear. First try **Sample** with your real name to check spelling and aliases in the **Look-alike Lab**.
2. Brand Profile: your real name, 2 to 5 aliases (the spellings people use; each costs 2 searches), keywords, official domains, a square logo.
3. Add **every** official account (Instagram, X, Facebook, LinkedIn, YouTube) and **every** official app, with the exact package or bundle id and publisher. Anything you forget will show up as a detection and you will have to **Mark as official** (that is fine, it teaches the allowlist).
   * Apple apps: the exact bundle id (for example `com.yourbrand.app`) is matched when the listing comes from the App Store search. If the same app also arrives from the web search (which only knows a numeric id), mark that one official once.
   * Your other apps (a creators app, a lite version) need their own entries.
4. Run a scan from the Dashboard and read the list.

**What live results look like.** In our trial against a very large, well-known consumer brand (8 searches, 8 credits per scan):

* 92 candidates were found, 89 unique; one was an official app and was excluded; about 25 were examined in detail; **19 detections, all in the Low band**, plus a handful cleared.
* Most were **third-party apps that mention the brand** ("... for <brand>" companion apps by other publishers) and **news or article pages**. None looked like a scam.
* The app search (Apple iTunes) is dependable for App Store listings and gives icons, so **icon comparison works on real App Store apps**.
* The web search (Tavily) found **no** fake Instagram, X, LinkedIn or Telegram profiles at all, and gives **no profile pictures**, so **logo comparison does not happen for social profiles on live data**, and bios are only the short search snippet.

**Be honest with yourself about what this means**

* The search finds pages that **mention** your brand. It is **not** a crawl of every account on every platform. An empty or all-Low result does **not** prove nobody is impersonating you.
* Impersonators on Instagram, X, Telegram and WhatsApp are often not indexed by web search at all.
* Names, spellings and misspellings are the strongest live signal. Expect **false positives** (a legitimate business with a similar name, fan pages, news, partner apps). The human review step is there for that.
* Use the **Evaluation** page's reviewer precision over time to learn how reliable your detections are.
* A real impersonation would typically show as Medium or High with several reasons (name + added word + logo + bio). Look at the reasons, not only the number.

---

## 12. Troubleshooting

| Problem | Cause and fix |
|---|---|
| **"Cannot reach the server"** at sign-in | The API is not running (start it: `.\.venv\Scripts\python -m uvicorn app.main:create_app --factory --port 8000`), or the website points at the wrong address. The website calls `http://127.0.0.1:8000` by default; set `VITE_API_URL` if your API is elsewhere, then restart `npm run dev`. |
| Works at `localhost:5173` but not at `127.0.0.1:5173` | **CORS.** The API only accepts the website addresses in `CORS_ORIGINS` (default `http://localhost:5173`). `localhost` and `127.0.0.1` are different to the browser. Always open the site at `http://localhost:5173`, or add the other address to `CORS_ORIGINS` in `backend\.env` (comma separated) and restart the API. |
| Blank white page | Open the browser console (F12). Usually the API is down (see above) or an old tab with a stale session: clear site data for `localhost:5173` and sign in again. |
| The page says "No brand profile yet" and the console shows two 404s | Normal before a brand exists. Create the brand. |
| `npm run dev` says the port is in use | Another copy is running. Close it, or use `npx vite --port 5175` and add that origin to `CORS_ORIGINS`. |
| I added `TAVILY_API_KEY` but the app still says no key / sample | The file is read only at start: **stop and restart the API.** Check the line has no spaces or quotes problems, and that **Data mode** is not set to Sample. |
| "Too many failed attempts" at sign-in | Wait about 15 minutes or restart the API. |
| "A scan is already running" (409) | Wait; one scan at a time. A scan stuck for 30 minutes is failed automatically. |
| "Create the brand profile before scanning" | Do Setup / Brand Profile first. |
| A scan started by itself | The scheduler is on by default (Settings > schedule). Set it to a long interval or set `SCHEDULER_ENABLED=false`. |
| Sample data when I wanted real data | Data mode is Sample (or Auto with no key). Settings > Account. |
| Real searches when I wanted sample data | Data mode is Auto and a key exists. Choose **Sample**. |
| All results are Low | Normal for a big brand with no impersonators in the search index (see section 11). |
| Google Play shows unavailable / not_installed | Expected; that optional package is not installed. |
| The sources all say "Responding slowly" | A live scan of 8 to 12 searches takes 20 seconds; the warning level is 4 seconds. Cosmetic. |
| Logo upload refused | Must be PNG, JPEG or WebP, 256 x 256 pixels or more, roughly square (width and height within 25% of each other), 2 MB or less. |
| Handle refused | 3 to 30 letters, digits, dot or underscore (hyphen only for LinkedIn and YouTube). |
| "database is locked" in an old copy of the app | Fixed in this version. Update, and restart the API. |
| Sign-in works but pages show errors after a few days | The sign-in token expired and refresh failed: sign out and sign in again. |

Server logs are printed in the API window (JSON lines, secrets removed). The first thing to check for any odd behaviour is `http://127.0.0.1:8000/health`.

---

## 13. Limitations

* One brand per workspace.
* No built-in way to add users (viewer, analyst) in the website.
* Web-search discovery cannot see most social profiles, gives no profile pictures, and uses only a search snippet as the "bio". Logo comparison on live data works for **App Store** icons only.
* Google Play search needs an optional package that is not installed by default; SearXNG is optional and not set up by default.
* The Telegram platform cannot be added as an official account.
* An app registered for one store only; Apple numeric ids cannot be typed in (use Mark as official on the detection).
* Reports are HTML or CSV files; no built-in PDF.
* Notifications are not delivered; the app never contacts platforms or sends takedowns.
* Screenshots of suspect pages are off by default.
* The login block is kept in memory and resets when the API restarts.
* The website list shows at most 200 detections and has no page-by-page navigation.
* Sample data is fictional and says nothing about how good detection is on real accounts.

---

## 14. Glossary

| Term | Meaning |
|---|---|
| **Alias** | Another spelling or short form of your brand name (Acme, AcmeBank). |
| **Allowlist** | Your exact official handles, URLs and package ids. They are never flagged. A similar name is never enough to be on it. |
| **Audit log** | The list of who did what and when. Export it from Settings > Account. |
| **Band** | High (85 to 100), Medium (70 to 84) or Low (below 70) risk. |
| **Candidate** | Something a scan found that might be an impersonation, before scoring. |
| **Canary** | A built-in self-test using a made-up bank; it checks the logic, not the live sources. |
| **Cleared** | A candidate the engine decided is harmless. Listed under False-positive guard. |
| **Credits** | The units Tavily charges for searches. |
| **Data mode** | Live, Sample or Auto: where scan data comes from. |
| **Detection** | A candidate that passed the bar and waits for a person to review. |
| **Evidence / SHA-256** | The saved copy of what was found, plus a fingerprint of it. If the saved file is changed by even one character the fingerprint no longer matches ("hash verified" turns false). |
| **False positive** | A detection that is not really an impersonation. Dismiss it. |
| **Homoglyph** | A letter from another alphabet that looks the same as a Latin one (Cyrillic "с" for "c"). |
| **Look-alike** | A name made to resemble yours. |
| **Mark as official** | Add a detection's handle or app to your allowlist. |
| **Monitor** | The per-platform switch for whether scans cover it. |
| **Pre-filtered** | Skipped early because it does not resemble your brand at all. |
| **Sample data** | Fictional data produced by the app for demos. |
| **Scan** | One pass: collect, remove official, match, score, save. |
| **Sensitivity** | The Settings slider that sets how weak a match may be and still be reviewed. |
| **Source** | A place the app looks (a platform or store). |
| **Tavily** | The web search service used for live discovery of social profiles and store pages. |
| **iTunes Search** | Apple's free app search, used for App Store apps. |
| **Unavailable / Slow / Online** | Source states. |
| **Viewer / analyst / admin** | The three user roles. |


## 15. AI text providers, images and importing candidates (new)

These features are API-level in this release (no new screens yet). Use the interactive docs at `http://127.0.0.1:8000/docs` or `curl` with your login token.

### AI text provider setup (optional)
1. Gemini: create a key at https://aistudio.google.com/apikey, then put `GEMINI_API_KEY=...` and `SCAM_LLM_ENABLED=true` in `backend\.env` (never share or commit the file). Optional: `GEMINI_MODEL` (default `gemini-3.5-flash-lite`; check that the id is available to your key). Install the package: `pip install -r requirements-extra.txt`. Restart the API.
2. Hugging Face (optional): create a fine-grained token with the "Make calls to Inference Providers" permission, set `HUGGINGFACE_API_TOKEN` and `HUGGINGFACE_MODEL` (a model a provider currently serves; there is no default), then choose it: `PUT /api/providers/active` with `{"provider": "huggingface"}` (admin).
3. Check: `GET /api/providers` shows `configured: true/false` per provider (never the key). Quick test without a scan, run in `server` with the variable set in that shell: `python -c "import os; from app.providers.text import TextRequest; from app.providers.text.gemini import GeminiTextProvider as G; print(G(api_key=os.environ['GEMINI_API_KEY'], model='gemini-3.5-flash-lite').analyze(TextRequest('bio','Official Acme Bank support: complete your KYC now or your account is blocked')).to_dict())"`
4. The assessment is an opinion labelled "API-generated"; it can only add capped points and never overrides the official list. Without a key the local text check still runs and the AI signal shows "unavailable". (Not run against the live services in this release: only mocked tests.)

### Manual entry and import
Add one candidate: `POST /api/candidates/social` (or `/app`, `/web`) with JSON. Import many: `POST /api/candidates/import?kind=social&format=csv` with a file; add `&dry_run=true` to only check it. Limits: 1 MB, 500 rows, UTF-8. The reply lists every row with its result, so one bad row never blocks the rest. Imported rows are labelled `import`, typed-in rows `manual`; web addresses are never opened.

Sample CSV (fictional Acme Bank), `social.csv`:
```
platform,handle,display_name,bio,followers
instagram,acme_bank_support,Acme Bank Support,Update your KYC now,120
```
Sample JSON `apps.json`:
```
[{"store": "google_play", "package_id": "com.acmebank.cards", "name": "Acme Bank Cards", "publisher": "Quick Tools", "publisher_id": "PUB123"}]
```
Web CSV columns are `url,title,note`. To compare a picture, first `POST /api/candidate-images` (multipart `file`) and put the returned `id` in the row's `image_id`.

### Registry additions
Brand description (`PUT /api/brands/{id}` with `description`), extra official logos (`POST /api/brands/{id}/logos`), publisher ids (`POST /api/brands/{id}/publishers`), verification status (`PUT /api/brands/{id}/verification`) and archiving (`DELETE /api/brands/{id}?confirm=<exact name>`, admin; nothing is erased).


## 16. New pages and screens (final pass)

- **Sidebar names.** Overview (the dashboard), Brand Registry (brand profile), Social Threats, App Threats, Web Threats, Investigations, Look-alike Lab, Source Health, Reports, Model Evaluation, Settings. The routes (`/dashboard`, `/brand`, `/social`, `/apps`) are unchanged.
- **Severity.** Next to the risk band, every detection shows a severity chip (Low, Medium, High, Critical) from the risk engine. In the detail panel, "Signal availability" lists each signal as Present, Clean or Unavailable and where it came from. **Unavailable means it could not be checked, not that it is safe.**
- **Web Threats.** Look-alike domains and links with a severity filter. URLs are never opened; Safe Browsing runs only if a key is set.
- **Investigations.** Every detection as a case. Filter by status, severity and resolution. Set the status, pick a resolution (true positive, false positive, resolved, monitoring) with an optional note, and Save. It is stored on the server, audited, and shown in the timeline after a refresh. Viewers see it read-only.
- **Add candidate / Import.** On the Social, App and Web pages: "Add candidate" scores one entry typed by hand; "Import CSV / JSON" first runs a dry run (nothing saved) with a per-row error list, then "Import valid rows".
- **Brand Registry.** A brand description field and a Verification status card: mark an official domain, account or app as verified (and by which method). This is your own record; the app does not check DNS or stores.
- **Settings.** Analysis providers (which text provider is active, configured yes or no, never a key; admins can switch) and whether Google Safe Browsing is configured.
- **Model Evaluation.** A prominent SYNTHETIC DATA banner, dataset name, version and size, algorithm version, a confusion matrix, the false positive rate and hard-negative groups (fan pages, authorized resellers, unrelated names).
