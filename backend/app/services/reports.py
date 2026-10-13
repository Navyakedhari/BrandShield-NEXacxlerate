"""Report building and rendering (CSV and printable HTML).

A report is created as a DRAFT from selected or confirmed detections and stores a snapshot of its rows. A person
approves it (audited). Nothing here sends or files anything anywhere: no takedown, no email, no webhook.
PDF is not produced: no PDF library is installed. The printable HTML uses print CSS, so "Print to PDF" in a browser works.
Every user-controlled string is HTML-escaped in the HTML export and formula-neutralised in the CSV export.
"""

from __future__ import annotations

import csv
import html
import io
import re
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.time import iso_z, utcnow
from app.models import Brand, Detection, OfficialAccount, OfficialApp, Report
from app.services.detections import ALL_TYPES, ActionError, summary_row

BASE_COLUMNS = ["id", "name", "handle_or_package", "platform", "type", "status", "score", "found_at", "publisher", "url", "reasons"]
EVIDENCE_COLUMNS = ["evidence_sha256", "evidence_url", "captured_at"]
REPORTABLE = ("new", "reviewing", "confirmed", "dismissed")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value: Any) -> str:
    """Neutralise spreadsheet formula injection: a cell that could start a formula gets a leading apostrophe."""
    s = "" if value is None else str(value)
    return "'" + s if s.startswith(_FORMULA_START) else s


def reasons_text(reasons: list[dict]) -> str:
    return "; ".join(f"{r.get('label') or r['signal']} (+{r['points']}): {r.get('detail', '')}" for r in reasons if r.get("points"))


def row_for(d: Detection, n: int) -> dict:
    s = summary_row(d)
    ev = d.evidence or {}
    return {
        "n": n, "id": d.id, "name": s["name"], "handle_or_package": s["handle"] or s["package_id"] or "", "platform": d.platform, "type": d.type,
        "category": d.category, "status": d.status, "score": d.score, "band": d.band, "found_at": s["found_at"], "publisher": d.publisher or "",
        "url": d.profile_url or "", "summary": s["summary"], "reasons": reasons_text(d.reasons or []), "reason_items": d.reasons or [],
        "evidence_sha256": ev.get("sha256") or "", "evidence_url": ev.get("page_url") or "", "captured_at": ev.get("captured_at") or "", "source": d.source,
    }


def select_detections(
    db: Session, brand: Brand, *, ids: list[int] | None, from_: datetime | None, to: datetime | None, types: list[str] | None,
    status: str | None, mode: str,
) -> list[Detection]:
    if ids:
        rows = list(db.scalars(select(Detection).where(Detection.brand_id == brand.id, Detection.id.in_(set(ids)))))
        found = {d.id for d in rows}
        missing = sorted(set(ids) - found)
        if missing:
            raise ActionError(422, f"Detections not found: {', '.join(map(str, missing))}")
        bad = [d.id for d in rows if d.status not in REPORTABLE]
        if bad:
            raise ActionError(422, f"Detections {', '.join(map(str, sorted(bad)))} cannot be in a report (cleared or marked official).")
        rows.sort(key=lambda d: (-d.score, d.id))
        return rows
    q = select(Detection).where(Detection.brand_id == brand.id, Detection.source == mode)
    q = q.where(Detection.status == status) if status else q.where(Detection.status == "confirmed")
    if types:
        bad_types = [t for t in types if t not in ALL_TYPES]
        if bad_types:
            raise ActionError(422, f"types must be from: {', '.join(ALL_TYPES)}")
        q = q.where(Detection.type.in_(types))
    if from_:
        q = q.where(Detection.found_at >= from_)
    if to:
        q = q.where(Detection.found_at <= to)
    return list(db.scalars(q.order_by(Detection.score.desc(), Detection.id)))


def stats(db: Session, brand: Brand, rows: list[Detection]) -> dict:
    official = (db.scalar(select(func.count()).select_from(OfficialAccount).where(OfficialAccount.brand_id == brand.id)) or 0) + (
        db.scalar(select(func.count()).select_from(OfficialApp).where(OfficialApp.brand_id == brand.id)) or 0)
    return {
        "detections": len(rows), "social": sum(d.category == "social" for d in rows), "apps": sum(d.category == "apps" for d in rows),
        "confirmed": sum(d.status == "confirmed" for d in rows), "official_excluded": official,
    }


def filename_for(brand: Brand, fmt: str, now: datetime) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", brand.name.lower()).strip("-") or "brand"
    return f"{slug}-detections-{now.date().isoformat()}.{fmt}"


# ---------------------------------------------------------------------------------------------------------
# CSV


def to_csv(rows: list[dict], include_evidence: bool) -> str:
    cols = BASE_COLUMNS + (EVIDENCE_COLUMNS if include_evidence else [])
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(cols)
    for r in rows:
        w.writerow([csv_safe(r.get(c, "")) for c in cols])
    return out.getvalue()


# ---------------------------------------------------------------------------------------------------------
# printable HTML

_CSS = """
body{font:13px/1.5 system-ui,Segoe UI,Arial,sans-serif;color:#111;margin:32px;max-width:1000px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:15px;margin:24px 0 8px}
.meta{color:#555;margin-bottom:16px}.banner{padding:8px 12px;border:1px solid #b45309;background:#fffbeb;color:#92400e;margin:12px 0;font-weight:600}
.sample{border-color:#2563eb;background:#eff6ff;color:#1e40af}
.stats{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}.stat{border:1px solid #ccc;padding:8px 14px;min-width:90px}.stat b{display:block;font-size:20px}
table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:5px 8px;text-align:left;vertical-align:top;overflow-wrap:anywhere}
th{background:#f3f4f6}.mono{font-family:ui-monospace,Consolas,monospace;font-size:11px}
@media print{body{margin:12mm}.noprint{display:none}}
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def to_html(*, title: str, brand_name: str, status: str, rows: list[dict], stats_: dict, include_evidence: bool, created_by: str,
            created_at: datetime, approved_by: str | None, approved_at: datetime | None, period: str, includes_sample: bool) -> str:
    parts = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">",
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body>",
        f"<h1>{_e(title)}</h1>",
        f"<div class=\"meta\">Brand: {_e(brand_name)} &middot; Period: {_e(period)} &middot; Created by {_e(created_by)} on {_e(iso_z(created_at))}</div>",
    ]
    if status == "approved":
        parts.append(f"<div class=\"meta\">Approved by {_e(approved_by)} on {_e(iso_z(approved_at) if approved_at else '')}</div>")
    else:
        parts.append("<div class=\"banner\">DRAFT. Not yet approved by a person. Nothing in this report has been sent to anyone.</div>")
    if includes_sample:
        parts.append("<div class=\"banner sample\">Contains sample data for a fictional brand. Not real detections.</div>")
    parts.append("<div class=\"stats\">" + "".join(
        f"<div class=\"stat\"><b>{_e(v)}</b>{_e(k.replace('_', ' '))}</div>" for k, v in stats_.items()) + "</div>")
    parts.append("<h2>Detections</h2><table><thead><tr><th>#</th><th>Name</th><th>Handle / package</th><th>Platform</th><th>Type</th>"
                 "<th>Score</th><th>Status</th><th>Found</th></tr></thead><tbody>")
    for r in rows:
        parts.append(
            f"<tr><td>{_e(r['n'])}</td><td>{_e(r['name'])}</td><td>{_e(r['handle_or_package'])}</td><td>{_e(r['platform'])}</td>"
            f"<td>{_e(r['type'])}</td><td>{_e(r['score'])}</td><td>{_e(r['status'])}</td><td>{_e(r['found_at'])}</td></tr>")
    if not rows:
        parts.append("<tr><td colspan=\"8\">No detections in this report.</td></tr>")
    parts.append("</tbody></table>")
    parts.append("<h2>Why each was flagged</h2>")
    for r in rows:
        parts.append(f"<h3>{_e(r['n'])}. {_e(r['name'])} ({_e(r['score'])})</h3><table><tbody>")
        for item in r.get("reason_items", []):
            parts.append(f"<tr><td>{_e(item.get('label') or item.get('signal'))}</td><td>+{_e(item.get('points'))}</td><td>{_e(item.get('detail'))}</td></tr>")
        parts.append("</tbody></table>")
        if include_evidence:
            parts.append(f"<p class=\"mono\">URL: {_e(r['evidence_url'])}<br>Captured: {_e(r['captured_at'])}<br>SHA-256: {_e(r['evidence_sha256'])}</p>")
    parts.append("<p class=\"meta\">Generated by ScamMind AI. A person reviewed every detection; no takedown or report was sent automatically.</p>")
    parts.append("</body></html>")
    return "".join(parts)


def report_html(db: Session, report: Report, brand: Brand) -> str:
    from app.models import User

    creator = db.get(User, report.created_by) if report.created_by else None
    approver = db.get(User, report.approved_by) if report.approved_by else None
    p = report.params or {}
    rows = report.snapshot or []
    return to_html(
        title=report.title, brand_name=brand.name, status=report.status, rows=rows, stats_=p.get("stats", {}), include_evidence=bool(p.get("include_evidence")),
        created_by=creator.name if creator else "unknown", created_at=report.created_at, approved_by=approver.name if approver else None,
        approved_at=report.approved_at, period=p.get("period", "all time"), includes_sample=report.includes_sample,
    )


def report_csv(report: Report) -> str:
    return to_csv(report.snapshot or [], bool((report.params or {}).get("include_evidence")))


def now() -> datetime:
    return utcnow()
