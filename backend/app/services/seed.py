import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.config import Settings
from app.config.security import hash_password
from app.models import User

log = logging.getLogger("app.seed")


def seed_admin(db: Session, settings: Settings) -> bool:
    """Create the admin from ADMIN_EMAIL / ADMIN_PASSWORD if absent. Never overwrites an existing password."""
    if not settings.admin_email or not settings.admin_password:
        return False
    email = settings.admin_email.strip().lower()
    if db.scalar(select(User.id).where(User.email == email)):
        return False
    db.add(User(name=settings.admin_name, email=email, password_hash=hash_password(settings.admin_password), role="admin"))
    db.commit()
    log.info("seeded admin user %s", email)
    return True
