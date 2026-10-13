from sqlalchemy.orm import Session

from app.models import AuditLog, User


def record(
    db: Session,
    actor: User | None,
    action: str,
    target: str = "",
    detail: str = "",
    *,
    actor_label: str | None = None,
    source: str = "live",
) -> AuditLog:
    """Add an audit row to the caller's transaction (it commits or rolls back with the action itself)."""
    row = AuditLog(
        actor_id=actor.id if actor else None,
        actor=(actor.name if actor else actor_label) or "system",
        action=action,
        target=target[:300],
        detail=detail,
        source=source,
    )
    db.add(row)
    return row
