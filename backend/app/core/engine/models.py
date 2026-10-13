"""Engine input models. Plain pydantic, independent of SQLAlchemy and FastAPI."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BrandProfileInput(_Model):
    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None  # official description / bio, used for overlap checks
    extra_benign_words: list[str] = Field(default_factory=list)  # hard negatives: other-business words
    extra_impersonation_words: list[str] = Field(default_factory=list)


class OfficialAssets(_Model):
    """Everything the brand owns. Matching any of these exactly means the candidate is official."""

    social_handles: dict[str, list[str]] = Field(default_factory=dict)  # platform -> handles (no @)
    social_urls: list[str] = Field(default_factory=list)
    play_package_ids: list[str] = Field(default_factory=list)
    apple_app_ids: list[str] = Field(default_factory=list)
    store_urls: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)  # official website domains
    publisher_names: list[str] = Field(default_factory=list)


class CandidateSocial(_Model):
    platform: str
    handle: str | None = None
    display_name: str | None = None
    url: str | None = None
    bio: str | None = None
    followers: int | None = None
    following: int | None = None
    posts: int | None = None
    created_at: date | None = None
    verified: bool | None = None
    bio_links: list[str] = Field(default_factory=list)
    logo_similarity: float | None = Field(default=None, ge=0, le=100)  # 0-100 from logo_similarity.py


class CandidateApp(_Model):
    store: str  # google_play | apple_app_store | apkpure | ...
    app_id: str | None = None
    url: str | None = None
    title: str | None = None
    publisher: str | None = None
    description: str | None = None
    icon_similarity: float | None = Field(default=None, ge=0, le=100)
    installs: int | None = None
    rating_count: int | None = None
    published_at: datetime | date | None = None
    third_party_mirror: bool | None = None  # None -> derived from `store`
