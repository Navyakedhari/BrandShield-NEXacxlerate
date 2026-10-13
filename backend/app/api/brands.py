import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.config import Settings
from app.config.deps import get_current_user, get_settings_dep, get_url_checker, require_admin, require_writer
from app.config.time import utcnow
from app.core.pipeline.logo import LogoError, process_logo, save_logo
from app.core.pipeline.url_safety import UrlChecker
from app.db.session import get_db
from app.models import AssetVerification, Brand, BrandLogo, BrandPublisher, OfficialAccount, OfficialApp, User
from app.schemas.auth import OkResponse
from app.schemas.brand import (
    AccountCreate,
    AccountList,
    AccountOut,
    AllowlistOut,
    AppCreate,
    AppList,
    AppOut,
    AppUpdate,
    BrandIdentity,
    BrandOut,
    BrandSummary,
    ExtraLogoOut,
    LogoOut,
    ProfileHealthOut,
    PublisherCreate,
    PublisherOut,
    VerificationIn,
    VerificationOut,
)
from app.services import audit
from app.services import validators as v
from app.services.detections import sync_with_allowlist
from app.services.profile_health import compute_profile_health

router = APIRouter(prefix="/brands")
log = logging.getLogger("app.brands")


def get_brand_or_404(db: Session, brand_id: int, include_archived: bool = False) -> Brand:
    brand = db.get(Brand, brand_id)
    if brand is None or (brand.archived_at is not None and not include_archived):
        raise HTTPException(404, "Brand not found")
    return brand


def account_out(a: OfficialAccount, warning: str | None = None) -> AccountOut:
    return AccountOut(
        id=a.id,
        platform=a.platform,
        handle=v.display_handle(a.platform, a.handle),
        url=a.url,
        status=a.status,
        last_checked_at=a.last_checked_at,
        source=a.source,
        warning=warning,
    )


def app_out(a: OfficialApp) -> AppOut:
    return AppOut(
        id=a.id,
        name=a.name,
        package_id=a.package_id,
        publisher=a.publisher,
        stores=a.stores,
        status=a.status,
        source=a.source,
        apple_app_id=a.store_id,
    )


def logo_out(b: Brand) -> LogoOut | None:
    if not b.logo_path:
        return None
    return LogoOut(
        url=f"/api/brands/{b.id}/logo",
        format=b.logo_format or "",
        width=b.logo_width or 0,
        height=b.logo_height or 0,
        size_bytes=b.logo_size_bytes or 0,
        sha256=b.logo_sha256 or "",
    )


def brand_out(b: Brand) -> BrandOut:
    return BrandOut(
        id=b.id,
        name=b.name,
        description=b.description,
        archived=b.archived_at is not None,
        extra_logos=[ExtraLogoOut(id=lg.id, label=lg.label, url=f"/api/brands/{b.id}/logos/{lg.id}/file", format=lg.format, width=lg.width,
                                  height=lg.height, size_bytes=lg.size_bytes, sha256=lg.sha256) for lg in b.logos],
        publishers=[PublisherOut(id=p.id, store=p.store, name=p.name, publisher_id=p.publisher_id) for p in b.publishers],
        verifications=[VerificationOut(asset_type=x.asset_type, asset_key=x.asset_key, status=x.status, method=x.method, note=x.note,
                                       verified_at=x.verified_at, verified_by=x.verified_by) for x in b.verifications],
        industry=b.industry,
        aliases=b.aliases,
        keywords=b.keywords,
        domains=b.domains,
        logo=logo_out(b),
        official_accounts=[account_out(a) for a in b.accounts],
        official_apps=[app_out(a) for a in b.apps],
        profile_health=ProfileHealthOut(**compute_profile_health(b)),
        allowlist=AllowlistOut(accounts=len(b.accounts), apps=len(b.apps), domains=len(b.domains)),
        official_assets=len(b.accounts) + len(b.apps),
        updated_at=b.updated_at,
        source=b.source,
    )


def _touch(b: Brand) -> None:
    b.updated_at = utcnow()


# ---------------------------------------------------------------- brands


@router.get("", response_model=list[BrandSummary])
def list_brands(_: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[Brand]:
    return list(db.scalars(select(Brand).where(Brand.archived_at.is_(None)).order_by(Brand.id)))


@router.post("", response_model=BrandOut, status_code=201)
def create_brand(body: BrandIdentity, user: User = Depends(require_writer), db: Session = Depends(get_db)) -> BrandOut:
    # One ACTIVE brand per organisation for now (an archived brand does not count); the table keeps brands separate for later.
    if db.scalar(select(func.count()).select_from(Brand).where(Brand.archived_at.is_(None))):
        raise HTTPException(409, "A brand already exists. Edit it instead.")
    brand = Brand(**body.model_dump())
    db.add(brand)
    db.flush()
    audit.record(db, user, "brand.created", brand.name)
    db.commit()
    return brand_out(brand)


@router.get("/{brand_id}", response_model=BrandOut)
def get_brand(brand_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> BrandOut:
    return brand_out(get_brand_or_404(db, brand_id))


@router.put("/{brand_id}", response_model=BrandOut)
def update_brand(
    brand_id: int, body: BrandIdentity, user: User = Depends(require_writer), db: Session = Depends(get_db)
) -> BrandOut:
    brand = get_brand_or_404(db, brand_id)
    data = body.model_dump()
    if "description" not in body.model_fields_set:
        data.pop("description")  # an older client that does not send it must not wipe it
    changed = [k for k, val in data.items() if getattr(brand, k) != val]
    for k, val in data.items():
        setattr(brand, k, val)
    _touch(brand)
    audit.record(db, user, "brand.updated", brand.name, "changed: " + (", ".join(changed) or "nothing"))
    db.commit()
    return brand_out(brand)


# ---------------------------------------------------------------- logo


@router.post("/{brand_id}/logo", response_model=LogoOut)
def upload_logo(
    brand_id: int,
    file: UploadFile = File(...),
    user: User = Depends(require_writer),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> LogoOut:
    brand = get_brand_or_404(db, brand_id)
    data = file.file.read(settings.logo_max_bytes + 1)  # never read more than the cap + 1 byte
    try:
        logo = process_logo(data, settings.logo_max_bytes)
    except LogoError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    old = brand.logo_path
    rel = save_logo(settings.data_dir, brand.id, logo)
    brand.logo_path = rel
    brand.logo_format = logo.format
    brand.logo_width, brand.logo_height = logo.width, logo.height
    brand.logo_size_bytes = len(logo.data)
    brand.logo_sha256 = logo.sha256
    _touch(brand)
    audit.record(db, user, "logo.replaced", brand.name, f"{logo.format} {logo.width}x{logo.height} sha256={logo.sha256[:12]}")
    db.commit()
    if old and old != rel:
        (settings.data_dir / old).unlink(missing_ok=True)
    out = logo_out(brand)
    assert out is not None
    return out


@router.get("/{brand_id}/logo")
def get_logo(
    brand_id: int,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> FileResponse:
    brand = get_brand_or_404(db, brand_id)
    if not brand.logo_path:
        raise HTTPException(404, "No logo uploaded")
    root = Path(settings.data_dir).resolve()
    path = (root / brand.logo_path).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, "Logo file is missing")
    media = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[brand.logo_format or "png"]
    return FileResponse(path, media_type=media, headers={"X-Content-Type-Options": "nosniff"})


# ---------------------------------------------------------------- official accounts


@router.get("/{brand_id}/accounts", response_model=AccountList)
def list_accounts(brand_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AccountList:
    brand = get_brand_or_404(db, brand_id)
    return AccountList(total=len(brand.accounts), items=[account_out(a) for a in brand.accounts])


@router.post("/{brand_id}/accounts", response_model=AccountOut, status_code=201)
def add_account(
    brand_id: int,
    body: AccountCreate,
    user: User = Depends(require_writer),
    db: Session = Depends(get_db),
    checker: UrlChecker = Depends(get_url_checker),
) -> AccountOut:
    brand = get_brand_or_404(db, brand_id)
    key = body.handle.lower()
    exists = db.scalar(
        select(OfficialAccount.id).where(
            OfficialAccount.brand_id == brand.id,
            OfficialAccount.platform == body.platform,
            OfficialAccount.handle_key == key,
        )
    )
    if exists:
        raise HTTPException(409, f"{body.handle} is already an official {body.platform} account")
    url = v.build_account_url(body.platform, body.handle)
    result = checker.check(url)  # never raises; failure becomes status=unreachable plus a warning
    account = OfficialAccount(
        brand_id=brand.id,
        platform=body.platform,
        handle=body.handle,
        handle_key=key,
        url=url,
        status=result.status,
        check_message=result.message,
        last_checked_at=utcnow(),
    )
    db.add(account)
    db.flush()
    _touch(brand)
    audit.record(db, user, "account.added", f"{v.display_handle(body.platform, body.handle)} on {body.platform}", result.status)
    db.expire(brand, ["accounts"])
    sync_with_allowlist(db, brand, user)  # an existing detection for exactly this handle leaves the detection lists
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, f"{body.handle} is already an official {body.platform} account") from exc
    return account_out(account, None if result.ok else result.message)


@router.delete("/{brand_id}/accounts/{account_id}", response_model=OkResponse)
def remove_account(
    brand_id: int, account_id: int, user: User = Depends(require_writer), db: Session = Depends(get_db)
) -> OkResponse:
    brand = get_brand_or_404(db, brand_id)
    account = db.get(OfficialAccount, account_id)
    if account is None or account.brand_id != brand.id:
        raise HTTPException(404, "Account not found")
    audit.record(db, user, "account.removed", f"{v.display_handle(account.platform, account.handle)} on {account.platform}")
    db.delete(account)
    _touch(brand)
    db.commit()
    return OkResponse()


# ---------------------------------------------------------------- official apps


@router.get("/{brand_id}/apps", response_model=AppList)
def list_apps(brand_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AppList:
    brand = get_brand_or_404(db, brand_id)
    return AppList(total=len(brand.apps), items=[app_out(a) for a in brand.apps])


@router.post("/{brand_id}/apps", response_model=AppOut, status_code=201)
def add_app(
    brand_id: int, body: AppCreate, user: User = Depends(require_writer), db: Session = Depends(get_db)
) -> AppOut:
    brand = get_brand_or_404(db, brand_id)
    key = body.package_id.lower()
    existing = db.scalar(select(OfficialApp).where(OfficialApp.brand_id == brand.id, OfficialApp.package_key == key))
    if existing is not None:
        # D4: the same app on the other store is the same official app: add the store instead of refusing
        new_stores = [st for st in body.stores if st not in (existing.stores or [])]
        if not new_stores:
            raise HTTPException(409, f"{body.package_id} is already an official app")
        existing.stores = [*(existing.stores or []), *new_stores]
        if body.apple_app_id:
            existing.store_id = body.apple_app_id
        _touch(brand)
        audit.record(db, user, "app.updated", f"{existing.name} ({existing.package_id})", "store added: " + ", ".join(new_stores))
        db.expire(brand, ["apps"])
        sync_with_allowlist(db, brand, user)
        db.commit()
        return app_out(existing)
    app = OfficialApp(
        brand_id=brand.id,
        name=body.name,
        package_id=body.package_id,
        package_key=key,
        publisher=body.publisher,
        stores=body.stores,
        store_id=body.apple_app_id,
    )
    db.add(app)
    db.flush()
    _touch(brand)
    audit.record(db, user, "app.added", f"{body.name} ({body.package_id})", ", ".join(body.stores))
    db.expire(brand, ["apps"])
    sync_with_allowlist(db, brand, user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, f"{body.package_id} is already an official app") from exc
    return app_out(app)


@router.patch("/{brand_id}/apps/{app_id}", response_model=AppOut)
def update_app(
    brand_id: int, app_id: int, body: AppUpdate, user: User = Depends(require_writer), db: Session = Depends(get_db)
) -> AppOut:
    """Store the numeric App Store id of an official app (so the same listing arriving with only its id is not flagged)."""
    brand = get_brand_or_404(db, brand_id)
    app = db.get(OfficialApp, app_id)
    if app is None or app.brand_id != brand.id:
        raise HTTPException(404, "App not found")
    if "app_store" not in (app.stores or []):
        raise HTTPException(422, "Only an app listed on the App Store has an App Store id")
    app.store_id = body.apple_app_id
    _touch(brand)
    audit.record(db, user, "app.updated", f"{app.name} ({app.package_id})", f"App Store id {body.apple_app_id}")
    db.expire(brand, ["apps"])
    sync_with_allowlist(db, brand, user)
    db.commit()
    return app_out(app)


@router.delete("/{brand_id}/apps/{app_id}", response_model=OkResponse)
def remove_app(
    brand_id: int, app_id: int, user: User = Depends(require_writer), db: Session = Depends(get_db)
) -> OkResponse:
    brand = get_brand_or_404(db, brand_id)
    app = db.get(OfficialApp, app_id)
    if app is None or app.brand_id != brand.id:
        raise HTTPException(404, "App not found")
    audit.record(db, user, "app.removed", f"{app.name} ({app.package_id})")
    db.delete(app)
    _touch(brand)
    db.commit()
    return OkResponse()


# ---------------------------------------------------------------- archive (soft delete)


@router.delete("/{brand_id}", response_model=OkResponse)
def archive_brand(
    brand_id: int, confirm: str = "", user: User = Depends(require_admin), db: Session = Depends(get_db)
) -> OkResponse:
    """Soft delete (admin): the brand disappears from lists and scans; nothing is erased and it can be restored. `confirm` must be the exact name."""
    brand = get_brand_or_404(db, brand_id)
    if confirm != brand.name:
        raise HTTPException(409, "Type the exact brand name in `confirm` to archive it")
    brand.archived_at = utcnow()
    audit.record(db, user, "brand.archived", brand.name)
    db.commit()
    return OkResponse()


@router.post("/{brand_id}/restore", response_model=BrandOut)
def restore_brand(brand_id: int, user: User = Depends(require_admin), db: Session = Depends(get_db)) -> BrandOut:
    brand = get_brand_or_404(db, brand_id, include_archived=True)
    if brand.archived_at is None:
        return brand_out(brand)
    if db.scalar(select(func.count()).select_from(Brand).where(Brand.archived_at.is_(None))):
        raise HTTPException(409, "Another brand is active; archive it first")
    brand.archived_at = None
    audit.record(db, user, "brand.restored", brand.name)
    db.commit()
    return brand_out(brand)


# ---------------------------------------------------------------- extra official logos


@router.post("/{brand_id}/logos", response_model=ExtraLogoOut, status_code=201)
def add_logo(
    brand_id: int, label: str = "", file: UploadFile = File(...), user: User = Depends(require_writer), db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> ExtraLogoOut:
    brand = get_brand_or_404(db, brand_id)
    data = file.file.read(settings.logo_max_bytes + 1)
    try:
        logo = process_logo(data, settings.logo_max_bytes)
    except LogoError as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
    if logo.sha256 == brand.logo_sha256 or any(lg.sha256 == logo.sha256 for lg in brand.logos):
        raise HTTPException(409, "This logo is already registered")
    if len(brand.logos) >= 10:
        raise HTTPException(422, "At most 10 extra logos")
    rel = save_logo(settings.data_dir, brand.id, logo)
    row = BrandLogo(brand_id=brand.id, label=" ".join(label.split())[:120], path=rel, format=logo.format, width=logo.width, height=logo.height,
                    size_bytes=len(logo.data), sha256=logo.sha256)
    db.add(row)
    _touch(brand)
    audit.record(db, user, "logo.added", brand.name, f"{logo.format} {logo.width}x{logo.height} sha256={logo.sha256[:12]}")
    db.commit()
    return ExtraLogoOut(id=row.id, label=row.label, url=f"/api/brands/{brand.id}/logos/{row.id}/file", format=row.format, width=row.width,
                        height=row.height, size_bytes=row.size_bytes, sha256=row.sha256)


@router.get("/{brand_id}/logos/{logo_id}/file")
def get_extra_logo(brand_id: int, logo_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db),
                   settings: Settings = Depends(get_settings_dep)) -> FileResponse:
    brand = get_brand_or_404(db, brand_id)
    row = db.get(BrandLogo, logo_id)
    root = Path(settings.data_dir).resolve()
    path = (root / row.path).resolve() if row and row.brand_id == brand.id else None
    if path is None or root not in path.parents or not path.is_file():
        raise HTTPException(404, "Logo not found")
    return FileResponse(path, media_type={"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[row.format], headers={"X-Content-Type-Options": "nosniff"})


@router.delete("/{brand_id}/logos/{logo_id}", response_model=OkResponse)
def remove_logo(brand_id: int, logo_id: int, user: User = Depends(require_writer), db: Session = Depends(get_db)) -> OkResponse:
    brand = get_brand_or_404(db, brand_id)
    row = db.get(BrandLogo, logo_id)
    if row is None or row.brand_id != brand.id:
        raise HTTPException(404, "Logo not found")
    audit.record(db, user, "logo.removed", brand.name, f"sha256={row.sha256[:12]}")
    db.delete(row)
    _touch(brand)
    db.commit()
    return OkResponse()


# ---------------------------------------------------------------- official publisher / developer identifiers


@router.post("/{brand_id}/publishers", response_model=PublisherOut, status_code=201)
def add_publisher(brand_id: int, body: PublisherCreate, user: User = Depends(require_writer), db: Session = Depends(get_db)) -> PublisherOut:
    brand = get_brand_or_404(db, brand_id)
    key = (body.publisher_id or body.name).lower()
    if any(p.store == body.store and p.publisher_key == key for p in brand.publishers):
        raise HTTPException(409, "This publisher is already registered")
    row = BrandPublisher(brand_id=brand.id, store=body.store, name=body.name, publisher_id=body.publisher_id, publisher_key=key)
    db.add(row)
    _touch(brand)
    audit.record(db, user, "publisher.added", brand.name, f"{body.store}: {body.publisher_id or body.name} (unverified)")
    db.commit()
    return PublisherOut(id=row.id, store=row.store, name=row.name, publisher_id=row.publisher_id)


@router.delete("/{brand_id}/publishers/{publisher_id}", response_model=OkResponse)
def remove_publisher(brand_id: int, publisher_id: int, user: User = Depends(require_writer), db: Session = Depends(get_db)) -> OkResponse:
    brand = get_brand_or_404(db, brand_id)
    row = db.get(BrandPublisher, publisher_id)
    if row is None or row.brand_id != brand.id:
        raise HTTPException(404, "Publisher not found")
    for x in list(brand.verifications):
        if x.asset_type == "publisher" and x.asset_key == row.publisher_key:
            db.delete(x)
    audit.record(db, user, "publisher.removed", brand.name, row.publisher_id or row.name)
    db.delete(row)
    _touch(brand)
    db.commit()
    return OkResponse()


# ---------------------------------------------------------------- verification status of official identifiers


def _asset_exists(brand: Brand, asset_type: str, key: str) -> bool:
    if asset_type == "domain":
        return key in (brand.domains or [])
    if asset_type == "account":
        return any(f"{a.platform}:{a.handle_key}" == key for a in brand.accounts)
    if asset_type == "app":
        return any(a.package_key == key for a in brand.apps)
    if asset_type == "publisher":
        return any(p.publisher_key == key for p in brand.publishers)
    return key == brand.logo_sha256 or any(lg.sha256 == key for lg in brand.logos)


@router.put("/{brand_id}/verification", response_model=VerificationOut)
def set_verification(brand_id: int, body: VerificationIn, user: User = Depends(require_writer), db: Session = Depends(get_db)) -> VerificationOut:
    """Record that a person checked an official identifier (how, when, by whom). The app does not verify DNS or stores itself.
    Domains stay on the allowlist either way; a PUBLISHER id is only allowlisted once it is verified."""
    brand = get_brand_or_404(db, brand_id)
    key = body.asset_key.strip().lower()
    if not _asset_exists(brand, body.asset_type, key):
        raise HTTPException(404, f"No such {body.asset_type} is registered for this brand")
    row = next((x for x in brand.verifications if x.asset_type == body.asset_type and x.asset_key == key), None)
    if row is None:
        row = AssetVerification(brand_id=brand.id, asset_type=body.asset_type, asset_key=key)
        db.add(row)
    row.status, row.note = body.status, " ".join(body.note.split())
    if body.status == "verified":
        row.method, row.verified_at, row.verified_by_id, row.verified_by = body.method, utcnow(), user.id, user.name
    else:
        row.method = row.verified_at = row.verified_by_id = row.verified_by = None
    _touch(brand)
    audit.record(db, user, "asset.verification_changed", f"{body.asset_type} {key}", f"{body.status}" + (f" via {body.method}" if body.method else ""))
    db.flush()
    db.expire(brand, ["verifications"])
    sync_with_allowlist(db, brand, user)  # a newly verified publisher id may close existing detections
    db.commit()
    return VerificationOut(asset_type=row.asset_type, asset_key=row.asset_key, status=row.status, method=row.method, note=row.note,
                           verified_at=row.verified_at, verified_by=row.verified_by)
