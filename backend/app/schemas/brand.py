from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from app.config.time import UtcDatetime
from app.services import validators as v


class BrandIdentity(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    industry: str = Field(default="", max_length=120)
    aliases: list[str] = Field(default_factory=list, max_length=200)
    keywords: list[str] = Field(default_factory=list, max_length=200)
    domains: list[str] = Field(default_factory=list, max_length=200)
    description: str | None = Field(default=None, max_length=2000)  # the brand's own public description

    @field_validator("name", "industry")
    @classmethod
    def _trim(cls, s: str) -> str:
        return " ".join(s.split())

    @model_validator(mode="after")
    def _normalize(self) -> "BrandIdentity":
        if not self.name:
            raise ValueError("Brand name is required")
        self.aliases = v.clean_text_list(self.aliases, what="alias")
        self.keywords = v.clean_text_list(self.keywords, what="keyword")
        domains: list[str] = []
        for raw in self.domains:
            if not raw.strip():
                continue
            d = v.normalize_domain(raw)
            if d not in domains:
                domains.append(d)
        if len(domains) > 50:
            raise ValueError("At most 50 domains allowed")
        self.domains = domains
        if self.description is not None:
            self.description = " ".join(self.description.split()) or None
        return self


class AccountCreate(BaseModel):
    platform: str
    handle: str = Field(min_length=1, max_length=100)

    @field_validator("platform")
    @classmethod
    def _platform(cls, p: str) -> str:
        p = p.strip().lower()
        if p not in v.ACCOUNT_PLATFORMS:
            raise ValueError(f"Platform must be one of: {', '.join(v.ACCOUNT_PLATFORMS)}")
        return p

    @field_validator("handle")
    @classmethod
    def _handle(cls, h: str, info: ValidationInfo) -> str:
        return v.normalize_handle(h, info.data.get("platform"))


class AppCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    package_id: str = Field(min_length=1, max_length=155)
    publisher: str = Field(min_length=1, max_length=120)
    stores: list[str] = Field(min_length=1)
    # Optional numeric App Store id (or the listing URL): lets a listing that only exposes the id be recognised as official.
    apple_app_id: str | None = Field(default=None, max_length=300)

    @field_validator("name", "publisher")
    @classmethod
    def _trim(cls, s: str) -> str:
        s = " ".join(s.split())
        if not s:
            raise ValueError("This field is required")
        return s

    @field_validator("stores")
    @classmethod
    def _stores(cls, stores: list[str]) -> list[str]:
        out: list[str] = []
        for s in stores:
            s = s.strip().lower()
            if s not in v.APP_STORES:
                raise ValueError(f"Store must be one of: {', '.join(v.APP_STORES)}")
            if s not in out:
                out.append(s)
        return out

    @model_validator(mode="after")
    def _package(self) -> "AppCreate":
        self.package_id = v.validate_package_id(self.package_id, self.stores)
        if self.apple_app_id is not None and self.apple_app_id.strip():
            if "app_store" not in self.stores:
                raise ValueError("An App Store id can only be given for an app listed on the App Store")
            self.apple_app_id = v.normalize_apple_app_id(self.apple_app_id)
        else:
            self.apple_app_id = None
        return self


class AppUpdate(BaseModel):
    apple_app_id: str = Field(min_length=1, max_length=300)

    @field_validator("apple_app_id")
    @classmethod
    def _apple(cls, raw: str) -> str:
        return v.normalize_apple_app_id(raw)


class LogoOut(BaseModel):
    url: str
    format: str
    width: int
    height: int
    size_bytes: int
    square: bool = True
    meets_requirements: bool = True
    sha256: str


class AccountOut(BaseModel):
    id: int
    platform: str
    handle: str
    url: str
    status: str
    last_checked_at: UtcDatetime | None
    source: str
    warning: str | None = None


class AccountList(BaseModel):
    total: int
    items: list[AccountOut]


class AppOut(BaseModel):
    id: int
    name: str
    package_id: str
    publisher: str
    stores: list[str]
    status: str
    source: str
    apple_app_id: str | None = None


class AppList(BaseModel):
    total: int
    items: list[AppOut]


class HealthCheckOut(BaseModel):
    key: str
    label: str
    ok: bool
    weight: int
    points: int
    why: str | None


class MissingOut(BaseModel):
    key: str
    label: str
    why: str | None


class ProfileHealthOut(BaseModel):
    percent: int
    message: str
    checks: list[HealthCheckOut]
    missing: list[MissingOut]


class AllowlistOut(BaseModel):
    accounts: int
    apps: int
    domains: int


class ExtraLogoOut(BaseModel):
    id: int
    label: str
    url: str
    format: str
    width: int
    height: int
    size_bytes: int
    sha256: str


class PublisherOut(BaseModel):
    id: int
    store: str
    name: str
    publisher_id: str | None


class VerificationOut(BaseModel):
    asset_type: str
    asset_key: str
    status: str
    method: str | None
    note: str
    verified_at: UtcDatetime | None
    verified_by: str | None


class PublisherCreate(BaseModel):
    store: str = "any"
    name: str = Field(default="", max_length=120)
    publisher_id: str | None = Field(default=None, max_length=120)

    @field_validator("store")
    @classmethod
    def _store(cls, s: str) -> str:
        s = s.strip().lower()
        if s not in ("google_play", "app_store", "any"):
            raise ValueError("store must be google_play, app_store or any")
        return s

    @model_validator(mode="after")
    def _need(self) -> "PublisherCreate":
        self.name = " ".join(self.name.split())
        self.publisher_id = (self.publisher_id or "").strip() or None
        if not self.name and not self.publisher_id:
            raise ValueError("Give a publisher name or a publisher id")
        return self


VERIFY_METHODS = ("dns_txt", "website_link", "store_listing", "platform_badge", "manual_review", "other")


class VerificationIn(BaseModel):
    asset_type: str
    asset_key: str = Field(min_length=1, max_length=300)
    status: str
    method: str | None = None
    note: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def _ok(self) -> "VerificationIn":
        if self.asset_type not in ("domain", "account", "app", "publisher", "logo"):
            raise ValueError("asset_type must be domain, account, app, publisher or logo")
        if self.status not in ("unverified", "verified"):
            raise ValueError("status must be unverified or verified")
        if self.status == "verified" and self.method not in VERIFY_METHODS:
            raise ValueError("A verified asset needs a method: " + ", ".join(VERIFY_METHODS))
        return self


class BrandOut(BaseModel):
    id: int
    name: str
    description: str | None = None
    archived: bool = False
    extra_logos: list[ExtraLogoOut] = []
    publishers: list[PublisherOut] = []
    verifications: list[VerificationOut] = []
    industry: str
    aliases: list[str]
    keywords: list[str]
    domains: list[str]
    logo: LogoOut | None
    official_accounts: list[AccountOut]
    official_apps: list[AppOut]
    profile_health: ProfileHealthOut
    allowlist: AllowlistOut
    official_assets: int
    updated_at: UtcDatetime
    source: str


class BrandSummary(BaseModel):
    id: int
    name: str
