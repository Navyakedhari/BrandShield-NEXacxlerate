"""Remove ONLY sample-data detections for one brand (used by `scripts.seed_test_brand --reset-sample`).

Sample candidates are keyed by handle, so when the sample generator changes, old sample rows would linger next to the
new ones. This deletes rows whose `source` is "sample" (flagged detections and cleared / not-flagged rows alike) and their
timeline events, so the next sample scan recreates them from scratch. Live rows, brands, accounts, apps, settings,
users and audit entries are never touched.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Detection, DetectionEvent


def reset_sample_detections(db: Session, brand_id: int) -> int:
    """Delete this brand's source='sample' detections (and their events). Returns how many detections were removed."""
    ids = list(db.scalars(select(Detection.id).where(Detection.brand_id == brand_id, Detection.source == "sample")))
    if not ids:
        return 0
    db.execute(delete(DetectionEvent).where(DetectionEvent.detection_id.in_(ids)))
    db.execute(delete(Detection).where(Detection.id.in_(ids)))
    db.commit()
    return len(ids)
