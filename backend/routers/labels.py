"""Shipment Labels — per-user label management + assignment.

Endpoints (all prefixed with `/api`):

  GET    /labels                     — list this user's labels
                                       (auto-seeds 10 defaults on first call)
  POST   /labels                     — create a new label
  PUT    /labels/{id}                — update a label (name / icon / color)
  DELETE /labels/{id}                — delete a label (also removes it
                                       from every shipment.labels array)
  PUT    /shipments/{id}/labels      — replace the labels[] array on one
                                       shipment (idempotent; used by chip
                                       add/remove in the UI)

Design notes:
- Labels are stored in the `labels` collection scoped by `user_id`. Every
  user gets their own copy of the 10 default labels seeded on first
  access, so they can freely rename / re-color / delete them without
  affecting other users.
- Shipment assignments are an EMBEDDED array on the shipment document
  (`shipments.labels = ["<label_id>", ...]`). This keeps the read path
  cheap — the existing GET /shipments already returns the whole doc,
  so the frontend gets labels "for free". Search / filter is a
  MongoDB `$in` query on that array — no join table needed.
- DELETE label is best-effort — it removes the label AND pulls the id
  from every shipment's labels array in a single updateMany.
- Zero touch to any existing endpoint. This router is additive.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

_logger = logging.getLogger("routers.labels")

labels_router = APIRouter(prefix="/api", tags=["labels"])


# --------------------------------------------------------------------
# Default seed set — 10 labels every new user gets on first /labels GET.
# The user can rename / re-color / delete any of these freely.
# `kind` is only used for UI grouping (ORDER LABELS vs PRIORITY LABELS).
# --------------------------------------------------------------------
DEFAULT_LABELS: List[Dict[str, Any]] = [
    # ── Order labels ─────────────────────────────────────────────
    {"name": "Address Issue",       "icon": "location",       "color": "#F97316", "kind": "order"},
    {"name": "Phone Number Issue",  "icon": "call",           "color": "#3B82F6", "kind": "order"},
    {"name": "Item Pending",        "icon": "cube",           "color": "#F59E0B", "kind": "order"},
    {"name": "Return Received",     "icon": "return",         "color": "#8B5CF6", "kind": "order"},
    {"name": "Resend Article",      "icon": "package",        "color": "#10B981", "kind": "order"},
    {"name": "Manual Review",       "icon": "alert",          "color": "#6B7280", "kind": "order"},
    # ── Priority labels ──────────────────────────────────────────
    {"name": "VIP Customer",        "icon": "star",           "color": "#A855F7", "kind": "priority"},
    {"name": "Urgent",              "icon": "flag",           "color": "#DC2626", "kind": "priority"},
    {"name": "High Priority",       "icon": "flag",           "color": "#F97316", "kind": "priority"},
    {"name": "Low Priority",        "icon": "flag",           "color": "#3B82F6", "kind": "priority"},
]


# --------------------------------------------------------------------
# Pydantic models
# --------------------------------------------------------------------
class LabelCreate(BaseModel):
    name:  str = Field(..., min_length=1, max_length=40)
    icon:  str = Field("tag", min_length=1, max_length=32)
    color: str = Field("#F97316", min_length=4, max_length=9)
    kind:  Optional[str] = Field(None, max_length=16)  # "order" | "priority" | custom


class LabelUpdate(BaseModel):
    name:  Optional[str] = Field(None, min_length=1, max_length=40)
    icon:  Optional[str] = Field(None, min_length=1, max_length=32)
    color: Optional[str] = Field(None, min_length=4, max_length=9)


class ShipmentLabelsUpdate(BaseModel):
    labels: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------
# Late-bind init — registers routes after server.py is up (matches the
# pattern used by every other extracted router in this project).
# --------------------------------------------------------------------
def init() -> None:
    from server import (  # noqa: WPS433 — intentional late import
        db,
        get_current_user as _get_current_user,
        utcnow_iso,
    )

    async def _ensure_indexes() -> None:
        try:
            await db.labels.create_index(
                [("user_id", 1), ("name", 1)],
                name="user_name",
            )
        except Exception:
            _logger.exception("labels index creation failed (non-fatal)")

    async def _seed_defaults_if_empty(user_id: str) -> None:
        """First-touch: give this user the 10 default labels."""
        existing = await db.labels.count_documents({"user_id": user_id})
        if existing:
            return
        docs = []
        now = utcnow_iso()
        for defn in DEFAULT_LABELS:
            docs.append({
                "id":          str(uuid.uuid4()),
                "user_id":     user_id,
                "name":        defn["name"],
                "icon":        defn["icon"],
                "color":       defn["color"],
                "kind":        defn["kind"],
                "is_default":  True,   # UI can show a subtle "default" hint
                "created_at":  now,
                "updated_at":  now,
            })
        try:
            await db.labels.insert_many(docs)
        except Exception:
            _logger.exception("labels default-seed failed for user=%s", user_id)

    # ── GET /labels ────────────────────────────────────────────────
    @labels_router.get("/labels")
    async def list_labels_endpoint(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        await _ensure_indexes()
        await _seed_defaults_if_empty(current_user["id"])
        rows = await db.labels.find(
            {"user_id": current_user["id"]}, {"_id": 0},
        ).sort([("kind", 1), ("created_at", 1)]).to_list(length=None)
        return {"labels": rows, "count": len(rows)}

    # ── POST /labels ───────────────────────────────────────────────
    @labels_router.post("/labels")
    async def create_label_endpoint(
        payload: LabelCreate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        await _ensure_indexes()
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Label name required")
        # Case-insensitive dedupe per user — mirrors what the UI expects.
        clash = await db.labels.find_one({
            "user_id": current_user["id"],
            "name":    {"$regex": f"^{name}$", "$options": "i"},
        })
        if clash:
            raise HTTPException(status_code=409, detail="Label name already exists")
        now = utcnow_iso()
        doc = {
            "id":          str(uuid.uuid4()),
            "user_id":     current_user["id"],
            "name":        name,
            "icon":        payload.icon or "tag",
            "color":       payload.color or "#F97316",
            "kind":        payload.kind or "custom",
            "is_default":  False,
            "created_at":  now,
            "updated_at":  now,
        }
        await db.labels.insert_one(doc)
        # Never leak the internal Mongo _id.
        doc.pop("_id", None)
        return {"ok": True, "label": doc}

    # ── PUT /labels/{id} ───────────────────────────────────────────
    @labels_router.put("/labels/{label_id}")
    async def update_label_endpoint(
        label_id: str,
        payload: LabelUpdate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        update: Dict[str, Any] = {"updated_at": utcnow_iso()}
        if payload.name is not None:
            update["name"] = payload.name.strip()
        if payload.icon is not None:
            update["icon"] = payload.icon
        if payload.color is not None:
            update["color"] = payload.color
        if len(update) == 1:
            raise HTTPException(status_code=400, detail="No fields to update")
        res = await db.labels.update_one(
            {"id": label_id, "user_id": current_user["id"]},
            {"$set": update},
        )
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Label not found")
        merged = await db.labels.find_one(
            {"id": label_id, "user_id": current_user["id"]}, {"_id": 0},
        )
        return {"ok": True, "label": merged}

    # ── DELETE /labels/{id} ────────────────────────────────────────
    @labels_router.delete("/labels/{label_id}")
    async def delete_label_endpoint(
        label_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        res = await db.labels.delete_one(
            {"id": label_id, "user_id": current_user["id"]},
        )
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Label not found")
        # Best-effort: strip this label id from every shipment that
        # references it (avoids orphan chip on the list card).
        try:
            await db.shipments.update_many(
                {"user_id": current_user["id"], "labels": label_id},
                {"$pull": {"labels": label_id}},
            )
        except Exception:
            _logger.exception("orphan-label cleanup failed for label=%s", label_id)
        return {"ok": True}

    # ── PUT /shipments/{id}/labels ─────────────────────────────────
    @labels_router.put("/shipments/{shipment_id}/labels")
    async def set_shipment_labels_endpoint(
        shipment_id: str,
        payload: ShipmentLabelsUpdate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        # Dedupe + sanity-cap to prevent abuse (a shipment with 200
        # labels doesn't render cleanly on any device anyway).
        raw = payload.labels or []
        cleaned: List[str] = []
        for lid in raw:
            if not isinstance(lid, str):
                continue
            lid = lid.strip()
            if lid and lid not in cleaned:
                cleaned.append(lid)
        if len(cleaned) > 20:
            raise HTTPException(status_code=400, detail="Max 20 labels per shipment")

        # Silently drop label ids the user no longer owns (deleted /
        # foreign) — never fail the whole write on a stale chip.
        if cleaned:
            valid_rows = await db.labels.find(
                {"user_id": current_user["id"], "id": {"$in": cleaned}},
                {"id": 1, "_id": 0},
            ).to_list(length=None)
            valid_ids = {r["id"] for r in valid_rows}
            cleaned = [lid for lid in cleaned if lid in valid_ids]

        now = utcnow_iso()
        res = await db.shipments.update_one(
            {"id": shipment_id, "user_id": current_user["id"]},
            {"$set": {"labels": cleaned, "updated_at": now}},
        )
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Shipment not found")
        return {"ok": True, "labels": cleaned}

    _logger.info(
        "labels router mounted: 5 endpoints, defaults=%d",
        len(DEFAULT_LABELS),
    )
