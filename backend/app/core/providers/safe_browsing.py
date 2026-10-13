"""Google Safe Browsing adapter (Lookup API v4, `threatMatches:find`).

What it is: a reputation lookup. The candidate URLs are SENT to Google; they are never fetched, opened or followed by us.

Verdicts (provider-truthful, see providers/base.py):
  match    Google lists the URL (threat types are returned).
  clean    The request succeeded and Google returned no match. NOT a safety guarantee.
  unknown  We could not ask or Google would not answer: no key, our daily budget used up, HTTP 429 / quota.
  failed   We asked and it went wrong: timeout, network error, 5xx, 400/401/403 (bad key, API not enabled), unreadable body.
`unknown` and `failed` both reach the risk engine as `unavailable`: a missing answer is never treated as clean.

Key: only from the environment (GOOGLE_SAFE_BROWSING_API_KEY), sent in the `x-goog-api-key` header (never in the URL, so
it cannot show up in an exception message or a log line). Without a key the adapter makes no request at all.
Terms: the Safe Browsing API is for non-commercial use; for a revenue-generating product use Google's Web Risk API
(same idea, paid). See docs/DECISIONS.md.
Quota: Google does not publish a number on the pages used for this project; the daily request budget
(SAFE_BROWSING_DAILY_BUDGET) is OUR limit, counted per UTC day in the api_usage table. Up to 500 URLs go into one request.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config.time import utcnow
from app.core.collectors.base import parse_retry_after
from app.core.collectors.util import TTLCache
from app.core.providers.base import CLEAN, FAILED, MATCH, UNKNOWN, ReputationResult
from app.models import ApiUsage

log = logging.getLogger("app.safe_browsing")

PROVIDER = "google_safe_browsing"
API_VERSION = "v4"
ENDPOINT = "https://safebrowsing.googleapis.com/v4/threatMatches:find"
MAX_URLS_PER_REQUEST = 500  # documented limit of threatMatches:find
THREAT_TYPES = ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE", "POTENTIALLY_HARMFUL_APPLICATION"]
CLIENT_ID = "scammind-ai"
CLIENT_VERSION = "1.0"
CLEAN_NOTE = "No match in Google's Safe Browsing lists. This does not prove the site is safe."


class DailyLedger:
    """Request counter per UTC day (table api_usage, provider "safe_browsing:YYYY-MM-DD"). `reserve` is atomic."""

    def __init__(self, session_factory: sessionmaker[Session], daily_budget: int, *, now: Callable[[], datetime] = utcnow) -> None:
        self._sf = session_factory
        self.budget = max(0, int(daily_budget))
        self._now = now

    def _key(self) -> tuple[str, str]:
        n = self._now()
        return f"safe_browsing:{n:%Y-%m-%d}", f"{n:%Y-%m}"

    def used(self) -> int:
        provider, month = self._key()
        with self._sf() as db:
            return db.scalar(select(ApiUsage.credits).where(ApiUsage.provider == provider, ApiUsage.month == month)) or 0

    def snapshot(self) -> dict:
        used = self.used()
        return {"provider": PROVIDER, "unit": "requests", "period": "UTC day", "used": used, "budget": self.budget,
                "remaining": max(0, self.budget - used)}

    def reserve(self, n: int = 1) -> bool:
        provider, month = self._key()
        with self._sf() as db:
            if db.scalar(select(ApiUsage.id).where(ApiUsage.provider == provider, ApiUsage.month == month)) is None:
                try:
                    db.add(ApiUsage(provider=provider, month=month, credits=0))
                    db.commit()
                except IntegrityError:
                    db.rollback()
            res = db.execute(
                update(ApiUsage)
                .where(ApiUsage.provider == provider, ApiUsage.month == month, ApiUsage.credits + n <= self.budget)
                .values(credits=ApiUsage.credits + n, updated_at=utcnow())
            )
            db.commit()
            return res.rowcount == 1


class SafeBrowsingClient:
    def __init__(
        self,
        api_key: str | None,
        *,
        client_factory: Callable[[], httpx.Client] | None = None,
        ledger: DailyLedger | None = None,
        cache: TTLCache | None = None,
        now: Callable[[], datetime] = utcnow,
        timeout: float = 5.0,
        endpoint: str = ENDPOINT,
    ) -> None:
        self._key = (api_key or "").strip() or None
        self._factory = client_factory or (lambda: httpx.Client(timeout=timeout, follow_redirects=False))
        self.ledger = ledger
        self.cache = cache
        self._now = now
        self.endpoint = endpoint
        self.blocked_until: datetime | None = None  # set after a 429: no further requests until then
        self.last_status: dict[str, Any] = {"verdict": None, "reason": None, "message": "Not used yet.", "at": None}

    # -- state ------------------------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return self._key is not None

    def status(self) -> dict:
        """Public, secret-free description for Source Health / Settings."""
        quota = self.ledger.snapshot() if self.ledger is not None else None
        return {
            "provider": PROVIDER, "api_version": API_VERSION, "configured": self.configured,
            "note": "URLs are sent to Google and never opened by this app. A clean result is not a safety guarantee.",
            "key_env": "GOOGLE_SAFE_BROWSING_API_KEY", "quota": quota, "last": self.last_status,
            "blocked_until": self.blocked_until.isoformat() if self.blocked_until else None,
        }

    def _note(self, verdict: str, reason: str | None, message: str) -> None:
        self.last_status = {"verdict": verdict, "reason": reason, "message": message, "at": self._now().isoformat()}

    # -- public -----------------------------------------------------------------------------
    def check(self, url: str) -> ReputationResult:
        return self.check_urls([url])[url]

    def check_urls(self, urls: list[str]) -> dict[str, ReputationResult]:
        """One result per distinct input URL. Never raises."""
        distinct = list(dict.fromkeys(u for u in urls if isinstance(u, str) and u))
        out: dict[str, ReputationResult] = {}
        todo: list[str] = []
        for u in distinct:
            cached = self.cache.get(u) if self.cache is not None else None
            if cached is not None:
                out[u] = ReputationResult(**{**cached.__dict__, "cached": True})
            else:
                todo.append(u)
        for i in range(0, len(todo), MAX_URLS_PER_REQUEST):
            out.update(self._request(todo[i : i + MAX_URLS_PER_REQUEST]))
        return out

    # -- internals --------------------------------------------------------------------------
    def _res(self, url: str, verdict: str, reason: str | None, message: str, *, asked: bool,
             threats: tuple[str, ...] = ()) -> ReputationResult:
        return ReputationResult(url=url, verdict=verdict, provider=PROVIDER, api_version=API_VERSION,
                                checked_at=self._now() if asked else None, threat_types=threats, reason=reason, message=message)

    def _all(self, urls: list[str], verdict: str, reason: str, message: str, *, asked: bool) -> dict[str, ReputationResult]:
        self._note(verdict, reason, message)
        return {u: self._res(u, verdict, reason, message, asked=asked) for u in urls}

    def _request(self, urls: list[str]) -> dict[str, ReputationResult]:
        if not self._key:
            return self._all(urls, UNKNOWN, "no_key", "No GOOGLE_SAFE_BROWSING_API_KEY is set, so reputation was not checked.", asked=False)
        now = self._now()
        if self.blocked_until is not None and now < self.blocked_until:
            msg = "Safe Browsing asked us to slow down (HTTP 429). Not checked until the wait is over."
            return self._all(urls, UNKNOWN, "quota", msg, asked=False)
        if self.ledger is not None and not self.ledger.reserve(1):
            return self._all(urls, UNKNOWN, "budget",
                             f"Daily Safe Browsing request budget reached ({self.ledger.budget}). Not checked today.", asked=False)
        body = {
            "client": {"clientId": CLIENT_ID, "clientVersion": CLIENT_VERSION},
            "threatInfo": {
                "threatTypes": THREAT_TYPES, "platformTypes": ["ANY_PLATFORM"], "threatEntryTypes": ["URL"],
                "threatEntries": [{"url": u} for u in urls],
            },
        }
        try:
            with self._factory() as client:
                resp = client.post(self.endpoint, json=body, headers={"x-goog-api-key": self._key, "content-type": "application/json"})
        except httpx.TimeoutException:
            return self._all(urls, FAILED, "timeout", "Safe Browsing did not answer in time.", asked=True)
        except httpx.HTTPError:
            return self._all(urls, FAILED, "unreachable", "Safe Browsing could not be reached.", asked=True)
        except Exception:  # noqa: BLE001 - an adapter must never break a scan
            log.warning("safe browsing request failed unexpectedly")
            return self._all(urls, FAILED, "unreachable", "Safe Browsing could not be reached.", asked=True)
        return self._interpret(urls, resp)

    @staticmethod
    def _error_text(resp: httpx.Response) -> str:
        try:
            err = resp.json().get("error", {})
            return f"{err.get('status', '')} {err.get('message', '')}".lower()
        except Exception:  # noqa: BLE001
            return ""

    def _interpret(self, urls: list[str], resp: httpx.Response) -> dict[str, ReputationResult]:
        code = resp.status_code
        if code == 429 or (code == 403 and any(w in self._error_text(resp) for w in ("quota", "rate", "limit", "resource_exhausted"))):
            wait = parse_retry_after(resp.headers.get("retry-after"), self._now())
            self.blocked_until = self._now() + timedelta(seconds=wait if wait else 3600)
            return self._all(urls, UNKNOWN, "quota", f"Safe Browsing quota or rate limit reached (HTTP {code}). Not checked.", asked=True)
        if code in (400, 401, 403):
            msg = f"Safe Browsing rejected the request (HTTP {code}). Check that the key is valid and the Safe Browsing API is enabled."
            return self._all(urls, FAILED, "invalid_key", msg, asked=True)
        if code >= 500:
            return self._all(urls, FAILED, "server_error", f"Safe Browsing had a server error (HTTP {code}).", asked=True)
        if code != 200:
            return self._all(urls, FAILED, "bad_response", f"Unexpected HTTP {code} from Safe Browsing.", asked=True)
        try:
            data = resp.json()
        except ValueError:
            return self._all(urls, FAILED, "bad_response", "Safe Browsing returned something that is not JSON.", asked=True)
        matches = data.get("matches", []) if isinstance(data, dict) else None
        if not isinstance(matches, list):
            return self._all(urls, FAILED, "bad_response", "Safe Browsing returned an unexpected response shape.", asked=True)

        threats: dict[str, list[str]] = {}
        unattributed = False
        lowered = {u.lower(): u for u in urls}
        for m in matches:
            if not isinstance(m, dict):
                unattributed = True
                continue
            murl = (m.get("threat") or {}).get("url") if isinstance(m.get("threat"), dict) else None
            target = murl if murl in urls else lowered.get(str(murl).lower()) if murl else None
            if target is None and len(urls) == 1:
                target = urls[0]
            ttype = str(m.get("threatType") or "UNSPECIFIED")[:60]
            if target is None:
                unattributed = True
                continue
            if ttype not in threats.setdefault(target, []):
                threats[target].append(ttype)
        out: dict[str, ReputationResult] = {}
        for u in urls:
            if u in threats:
                names = tuple(threats[u])
                out[u] = self._res(u, MATCH, None, "Listed by Google Safe Browsing: " + ", ".join(names) + ".", asked=True, threats=names)
            elif unattributed:
                out[u] = self._res(u, UNKNOWN, "unattributed_match", "A reported match could not be tied to this URL.", asked=True)
            else:
                out[u] = self._res(u, CLEAN, None, CLEAN_NOTE, asked=True)
            if self.cache is not None and out[u].verdict in (MATCH, CLEAN):
                self.cache.set(u, out[u])
        self._note(MATCH if threats else CLEAN, None, f"Checked {len(urls)} URL(s): {len(threats)} match(es).")
        return out
