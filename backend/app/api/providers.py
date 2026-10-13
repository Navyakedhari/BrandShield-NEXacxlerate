"""GET /api/providers: which analysis providers are configured (never a key). PUT /api/providers/active (admin): choose the text provider."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config.config import Settings
from app.config.deps import get_current_user, get_settings_dep, require_admin
from app.core.pipeline.text_analysis import SETTING_KEY, active_choice
from app.core.providers.text import build_embedder, build_provider
from app.db.session import get_db
from app.models import User
from app.services import app_settings, audit

router = APIRouter(prefix="/providers")


class ActiveIn(BaseModel):
    provider: Literal["off", "local", "gemini", "huggingface"]


def _view(db: Session, settings: Settings) -> dict:
    active = active_choice(db, settings)
    items = []
    for name in ("local", "gemini", "huggingface"):
        st = build_provider(name, settings).status()
        items.append({"name": name, "label": st.label, "configured": st.configured, "model": st.model, "needs_key": st.needs_key,
                      "reason": st.reason, "active": name == active})
    return {
        "active_text_provider": active,
        "hosted_text_analysis_enabled": settings.scam_llm_enabled,
        "text_providers": items,
        "embeddings": {"enabled": build_embedder(settings) is not None, "model": settings.gemini_embedding_model,
                       "note": "Off by default; the embedding model id is unverified and has no default."},
        "image_similarity_backend": settings.image_similarity_backend,
        "note": "Keys are read from the environment and never returned. Hosted analysis only runs when SCAM_LLM_ENABLED=true.",
    }


@router.get("")
def providers(_: User = Depends(get_current_user), db: Session = Depends(get_db), settings: Settings = Depends(get_settings_dep)) -> dict:
    return _view(db, settings)


@router.put("/active")
def set_active(body: ActiveIn, request: Request, user: User = Depends(require_admin), db: Session = Depends(get_db),
               settings: Settings = Depends(get_settings_dep)) -> dict:
    app_settings.put_setting(db, SETTING_KEY, body.provider, user)
    audit.record(db, user, "settings.provider_changed", "text provider", body.provider)
    db.commit()
    return _view(db, settings)
