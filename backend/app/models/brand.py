from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config.time import utcnow
from app.db.base import Base


class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    industry: Mapped[str] = mapped_column(String(120), default="")
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    domains: Mapped[list] = mapped_column(JSON, default=list)
    logo_path: Mapped[str | None] = mapped_column(String(300), nullable=True)  # relative to data dir
    logo_format: Mapped[str | None] = mapped_column(String(10), nullable=True)
    logo_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    logo_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    logo_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    logo_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)  # the brand's own public description (also the only brand text sent to AI providers)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # soft delete: nothing is ever erased
    source: Mapped[str] = mapped_column(String(10), default="live")  # live | sample
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    accounts: Mapped[list["OfficialAccount"]] = relationship(
        back_populates="brand", cascade="all, delete-orphan", order_by="OfficialAccount.id"
    )
    apps: Mapped[list["OfficialApp"]] = relationship(
        back_populates="brand", cascade="all, delete-orphan", order_by="OfficialApp.id"
    )
    logos: Mapped[list["BrandLogo"]] = relationship(cascade="all, delete-orphan", order_by="BrandLogo.id")
    publishers: Mapped[list["BrandPublisher"]] = relationship(cascade="all, delete-orphan", order_by="BrandPublisher.id")
    verifications: Mapped[list["AssetVerification"]] = relationship(cascade="all, delete-orphan", order_by="AssetVerification.id")


class OfficialAccount(Base):
    __tablename__ = "official_accounts"
    __table_args__ = (UniqueConstraint("brand_id", "platform", "handle_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(20))
    handle: Mapped[str] = mapped_column(String(30))  # bare handle, no leading @
    handle_key: Mapped[str] = mapped_column(String(30))  # lower-case, used for duplicate detection
    url: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="unreachable")  # verified | unreachable
    check_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(10), default="live")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    brand: Mapped[Brand] = relationship(back_populates="accounts")


class OfficialApp(Base):
    __tablename__ = "official_apps"
    __table_args__ = (UniqueConstraint("brand_id", "package_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    package_id: Mapped[str] = mapped_column(String(155))
    package_key: Mapped[str] = mapped_column(String(155))  # lower-case duplicate key
    publisher: Mapped[str] = mapped_column(String(120))
    store_id: Mapped[str | None] = mapped_column(String(20), nullable=True)  # optional numeric App Store id (.../id123456789)
    stores: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="verified")
    source: Mapped[str] = mapped_column(String(10), default="live")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    brand: Mapped[Brand] = relationship(back_populates="apps")


class BrandLogo(Base):
    """An ADDITIONAL official logo / image asset (the first uploaded logo stays on `brands.logo_*`). A candidate is compared with every one
    and the best match wins."""

    __tablename__ = "brand_logos"
    __table_args__ = (UniqueConstraint("brand_id", "sha256"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    path: Mapped[str] = mapped_column(String(300))  # relative to the data dir; the name is derived from the hash
    format: Mapped[str] = mapped_column(String(10))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BrandPublisher(Base):
    """Official publisher / developer identifier on a store (Play developer id, App Store seller / artist id, or just the name)."""

    __tablename__ = "brand_publishers"
    __table_args__ = (UniqueConstraint("brand_id", "store", "publisher_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    store: Mapped[str] = mapped_column(String(20))  # google_play | app_store | any
    name: Mapped[str] = mapped_column(String(120), default="")
    publisher_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    publisher_key: Mapped[str] = mapped_column(String(120))  # lower-case id (or name when there is no id): duplicate key
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AssetVerification(Base):
    """Whether an official identifier is only claimed (`unverified`) or has been checked by a person (`verified`: how, when, by whom).
    This is an analyst attestation recorded with its method; the application does not check DNS or stores itself."""

    __tablename__ = "asset_verifications"
    __table_args__ = (UniqueConstraint("brand_id", "asset_type", "asset_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id", ondelete="CASCADE"), index=True)
    asset_type: Mapped[str] = mapped_column(String(12))  # domain | account | app | publisher | logo
    asset_key: Mapped[str] = mapped_column(String(300))  # normalised identifier of that asset
    status: Mapped[str] = mapped_column(String(12), default="unverified")  # unverified | verified
    method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    note: Mapped[str] = mapped_column(String(300), default="")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_by_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # no FK: the record outlives the user
    verified_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
