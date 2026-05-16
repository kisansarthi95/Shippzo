"""
Phase-21 — Video Tutorials backend.

Two MongoDB collections:
  • video_tutorials             — the actual tutorial entries
  • video_tutorial_categories   — admin-managed list of chip categories

User endpoints (auth required, any logged-in user):
  GET    /api/video-tutorials              List active tutorials, optional
                                           category filter, sorted by
                                           displayOrder asc, createdAt desc.
  GET    /api/video-tutorials/{id}         Single tutorial detail (used
                                           by the embedded player screen).
  GET    /api/video-tutorial-categories    List active categories.

Admin endpoints (is_admin required):
  POST   /api/admin/video-tutorials                 Create
  PATCH  /api/admin/video-tutorials/{id}            Partial update
  DELETE /api/admin/video-tutorials/{id}            Soft-delete (isActive=False)

  POST   /api/admin/video-tutorial-categories       Create category
  PATCH  /api/admin/video-tutorial-categories/{id}  Update
  DELETE /api/admin/video-tutorial-categories/{id}  Soft-delete

Auto-magic on create/update:
  • If the admin pastes a YouTube URL, the helper extracts the videoId
    (`watch?v=...`, `youtu.be/...`, `youtube.com/shorts/...`, etc.) and
    auto-derives `thumbnailUrl = https://img.youtube.com/vi/{id}/hqdefault.jpg`.
  • On first server start, a small seed runs to populate the categories
    collection with the canonical Labels / Wallet / Excel / WhatsApp /
    Smart Fill set so the frontend has something to show out of the box.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

_logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────
def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_admin(user: Dict[str, Any]) -> bool:
    return bool(user and user.get("is_admin"))


# YouTube URL forms we recognise:
#   https://www.youtube.com/watch?v=VIDEOID
#   https://youtu.be/VIDEOID
#   https://www.youtube.com/embed/VIDEOID
#   https://www.youtube.com/shorts/VIDEOID
#   https://m.youtube.com/watch?v=VIDEOID
_YT_REGEXES = [
    re.compile(r"(?:v=|/embed/|/shorts/|youtu\.be/)([A-Za-z0-9_-]{6,15})"),
]


def extract_youtube_id(url_or_id: str) -> str:
    """Best-effort extractor. If the input ALREADY looks like a bare
    11-char video id (the standard youtube length) we return it as-is.
    Otherwise we try the regex list. Returns "" if nothing matches —
    the admin form surfaces a validation error in that case."""
    s = (url_or_id or "").strip()
    if not s:
        return ""
    # Already a bare ID? (alnum+_- with 6-15 chars, no spaces or slashes)
    if re.fullmatch(r"[A-Za-z0-9_-]{6,15}", s):
        return s
    for rx in _YT_REGEXES:
        m = rx.search(s)
        if m:
            return m.group(1)
    return ""


def _norm(s: Any, max_len: int = 1000) -> str:
    if s is None:
        return ""
    return str(s).strip()[:max_len]


def _serialise(d: Dict[str, Any]) -> Dict[str, Any]:
    d.pop("_id", None)
    return d


_DEFAULT_CATEGORIES = [
    {"name": "Labels",      "icon": "document-text-outline",   "order": 1},
    {"name": "Wallet",      "icon": "wallet-outline",          "order": 2},
    {"name": "Excel",       "icon": "cloud-upload-outline",    "order": 3},
    {"name": "WhatsApp",    "icon": "logo-whatsapp",           "order": 4},
    {"name": "Smart Fill",  "icon": "sparkles",                "order": 5},
]


# ────────────────────────────────────────────────────────────────────
# Pydantic models
# ────────────────────────────────────────────────────────────────────
class TutorialIn(BaseModel):
    youtube_url:        str = Field(..., min_length=4, max_length=300)
    title:              str = Field(..., min_length=2, max_length=100)
    short_description:  str = Field("", max_length=200)
    category:           str = Field(..., min_length=1, max_length=80)
    duration:           str = Field("", max_length=20)
    display_order:      Optional[int] = None


class TutorialPatch(BaseModel):
    youtube_url:        Optional[str] = None
    title:              Optional[str] = None
    short_description:  Optional[str] = None
    category:           Optional[str] = None
    duration:           Optional[str] = None
    display_order:      Optional[int] = None
    is_active:          Optional[bool] = None


class CategoryIn(BaseModel):
    name:           str = Field(..., min_length=1, max_length=60)
    icon:           str = "document-text-outline"
    display_order:  int = 99
    is_active:      bool = True


class CategoryPatch(BaseModel):
    name:           Optional[str] = None
    icon:           Optional[str] = None
    display_order:  Optional[int] = None
    is_active:      Optional[bool] = None


# ────────────────────────────────────────────────────────────────────
# Router instances
# ────────────────────────────────────────────────────────────────────
tutorials_router       = APIRouter(prefix="/api", tags=["video-tutorials"])
admin_tutorials_router = APIRouter(prefix="/api/admin", tags=["admin-video-tutorials"])


# ────────────────────────────────────────────────────────────────────
# init() — registers every route. Called from server.py.
# ────────────────────────────────────────────────────────────────────
def init() -> None:
    from server import db, get_current_user as _gcu

    # ───── Default-category seed (idempotent) ──────────────────────
    async def _seed_default_categories():
        try:
            existing = await db.video_tutorial_categories.count_documents({})
            if existing == 0:
                docs = []
                now = _utcnow_iso()
                for c in _DEFAULT_CATEGORIES:
                    docs.append({
                        "id":            str(uuid.uuid4()),
                        "name":          c["name"],
                        "icon":          c["icon"],
                        "display_order": c["order"],
                        "is_active":     True,
                        "created_at":    now,
                        "updated_at":    now,
                    })
                await db.video_tutorial_categories.insert_many(docs)
                _logger.info("Seeded %d default video tutorial categories", len(docs))
        except Exception:
            _logger.exception("video_tutorial_categories seed failed")

    # Fire-and-forget on startup. We rely on the importer (server.py)
    # calling init() inside the startup task; the await happens via
    # the helper below from the first request that touches the router.
    # Cheap enough to also call directly here in init().
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_seed_default_categories())
    except Exception:
        pass

    # ───────── USER endpoints ──────────────────────────────────────
    @tutorials_router.get("/video-tutorials")
    async def list_tutorials(
        category: Optional[str] = Query(None),
        limit: int = Query(100, ge=1, le=500),
        current_user: Dict[str, Any] = Depends(_gcu),
    ):
        """Active tutorials, optionally filtered by category. Sorted
        by display_order asc, then created_at desc so admins can
        manually pin highlights to the top."""
        q: Dict[str, Any] = {"is_active": True}
        if category and category.lower() != "all":
            q["category"] = category
        cursor = (
            db.video_tutorials.find(q)
            .sort([("display_order", 1), ("created_at", -1)])
            .limit(limit)
        )
        items: List[Dict[str, Any]] = []
        async for t in cursor:
            items.append(_serialise(t))
        return {"items": items, "count": len(items)}

    @tutorials_router.get("/video-tutorials/{tid}")
    async def get_tutorial(
        tid: str,
        current_user: Dict[str, Any] = Depends(_gcu),
    ):
        t = await db.video_tutorials.find_one({"id": tid})
        if not t:
            raise HTTPException(status_code=404, detail="tutorial not found")
        return _serialise(t)

    @tutorials_router.get("/video-tutorial-categories")
    async def list_categories(
        current_user: Dict[str, Any] = Depends(_gcu),
    ):
        cursor = (
            db.video_tutorial_categories.find({"is_active": True})
            .sort([("display_order", 1), ("name", 1)])
        )
        items: List[Dict[str, Any]] = []
        async for c in cursor:
            items.append(_serialise(c))
        return {"items": items, "count": len(items)}

    # ───────── ADMIN endpoints ─────────────────────────────────────
    def _admin_only(user: Dict[str, Any]):
        if not _is_admin(user):
            raise HTTPException(status_code=403, detail="admin only")

    @admin_tutorials_router.post("/video-tutorials")
    async def create_tutorial(
        payload: TutorialIn,
        current_user: Dict[str, Any] = Depends(_gcu),
    ):
        _admin_only(current_user)
        vid = extract_youtube_id(payload.youtube_url)
        if not vid:
            raise HTTPException(
                status_code=400,
                detail="Could not extract YouTube video ID from URL",
            )
        now = _utcnow_iso()
        tid = str(uuid.uuid4())
        doc = {
            "id":                tid,
            "youtube_video_id":  vid,
            "youtube_url":       _norm(payload.youtube_url, 300),
            "title":             _norm(payload.title, 100),
            "short_description": _norm(payload.short_description, 200),
            "category":          _norm(payload.category, 80),
            "thumbnail_url":     f"https://img.youtube.com/vi/{vid}/hqdefault.jpg",
            "duration":          _norm(payload.duration, 20),
            "display_order":     int(payload.display_order or 99),
            "is_active":         True,
            "created_at":        now,
            "updated_at":        now,
        }
        await db.video_tutorials.insert_one(doc)
        return _serialise(doc)

    @admin_tutorials_router.patch("/video-tutorials/{tid}")
    async def update_tutorial(
        tid: str,
        payload: TutorialPatch,
        current_user: Dict[str, Any] = Depends(_gcu),
    ):
        _admin_only(current_user)
        upd: Dict[str, Any] = {}
        if payload.youtube_url is not None:
            vid = extract_youtube_id(payload.youtube_url)
            if not vid:
                raise HTTPException(status_code=400, detail="invalid YouTube URL")
            upd["youtube_url"]      = _norm(payload.youtube_url, 300)
            upd["youtube_video_id"] = vid
            upd["thumbnail_url"]    = f"https://img.youtube.com/vi/{vid}/hqdefault.jpg"
        if payload.title is not None:             upd["title"]             = _norm(payload.title, 100)
        if payload.short_description is not None: upd["short_description"] = _norm(payload.short_description, 200)
        if payload.category is not None:          upd["category"]          = _norm(payload.category, 80)
        if payload.duration is not None:          upd["duration"]          = _norm(payload.duration, 20)
        if payload.display_order is not None:     upd["display_order"]     = int(payload.display_order)
        if payload.is_active is not None:         upd["is_active"]         = bool(payload.is_active)
        if not upd:
            raise HTTPException(status_code=400, detail="no fields to update")
        upd["updated_at"] = _utcnow_iso()
        res = await db.video_tutorials.update_one({"id": tid}, {"$set": upd})
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="tutorial not found")
        t = await db.video_tutorials.find_one({"id": tid})
        return _serialise(t or {})

    @admin_tutorials_router.delete("/video-tutorials/{tid}")
    async def delete_tutorial(
        tid: str,
        current_user: Dict[str, Any] = Depends(_gcu),
    ):
        """Hard-delete the tutorial doc. Categories are soft-deleted
        because they may be referenced by lots of tutorials; individual
        tutorials are safe to wipe outright."""
        _admin_only(current_user)
        res = await db.video_tutorials.delete_one({"id": tid})
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="tutorial not found")
        return {"ok": True}

    # ── Categories ────────────────────────────────────────────────
    @admin_tutorials_router.post("/video-tutorial-categories")
    async def create_category(
        payload: CategoryIn,
        current_user: Dict[str, Any] = Depends(_gcu),
    ):
        _admin_only(current_user)
        now = _utcnow_iso()
        doc = {
            "id":            str(uuid.uuid4()),
            "name":          _norm(payload.name, 60),
            "icon":          _norm(payload.icon, 40) or "document-text-outline",
            "display_order": int(payload.display_order),
            "is_active":     bool(payload.is_active),
            "created_at":    now,
            "updated_at":    now,
        }
        await db.video_tutorial_categories.insert_one(doc)
        return _serialise(doc)

    @admin_tutorials_router.patch("/video-tutorial-categories/{cid}")
    async def update_category(
        cid: str,
        payload: CategoryPatch,
        current_user: Dict[str, Any] = Depends(_gcu),
    ):
        _admin_only(current_user)
        upd: Dict[str, Any] = {}
        if payload.name is not None:          upd["name"]          = _norm(payload.name, 60)
        if payload.icon is not None:          upd["icon"]          = _norm(payload.icon, 40)
        if payload.display_order is not None: upd["display_order"] = int(payload.display_order)
        if payload.is_active is not None:     upd["is_active"]     = bool(payload.is_active)
        if not upd:
            raise HTTPException(status_code=400, detail="no fields to update")
        upd["updated_at"] = _utcnow_iso()
        res = await db.video_tutorial_categories.update_one({"id": cid}, {"$set": upd})
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="category not found")
        c = await db.video_tutorial_categories.find_one({"id": cid})
        return _serialise(c or {})

    @admin_tutorials_router.delete("/video-tutorial-categories/{cid}")
    async def delete_category(
        cid: str,
        current_user: Dict[str, Any] = Depends(_gcu),
    ):
        _admin_only(current_user)
        # Soft-delete — flips is_active to False so tutorials already
        # referencing this category don't lose their chip label.
        res = await db.video_tutorial_categories.update_one(
            {"id": cid},
            {"$set": {"is_active": False, "updated_at": _utcnow_iso()}},
        )
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="category not found")
        return {"ok": True}
