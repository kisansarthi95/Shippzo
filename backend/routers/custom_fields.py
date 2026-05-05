"""
Custom Fields + Contact-Save Settings + VCF builder — Phase-3 refactor.

Extracts user-managed custom-field CRUD, the contact-save settings
section, and the admin per-plan custom-field cap endpoints out of the
server.py monolith. The fire-and-forget background helper
`_write_custom_values_to_user_sheet_bg` STAYS in server.py because
it's also called from create_shipment + ship_order paths.

Endpoints (all under /api):

  Contact-save settings + VCF builder
  ----------------------------------
  GET  /me/contact-settings                get_contact_settings
  PUT  /me/contact-settings                put_contact_settings
  POST /contacts/build-one                 build_one_contact
  POST /contacts/build-vcf                 build_bulk_vcf

  Per-user Custom Fields
  ----------------------
  GET    /me/custom-fields                 list_my_custom_fields
  POST   /me/custom-fields                 create_my_custom_field
  PUT    /me/custom-fields/{field_id}      update_my_custom_field
  DELETE /me/custom-fields/{field_id}      delete_my_custom_field

  Admin: per-plan caps
  --------------------
  GET /admin/custom-field-limits           admin_get_custom_field_limits
  PUT /admin/custom-field-limits           admin_set_custom_field_limits

Pattern: late-binding `init()` to avoid circular imports with server.
"""
import re
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel


custom_fields_router = APIRouter(prefix="/api", tags=["custom-fields"])


# ---------------------- Models (router-local) ----------------------

class CustomFieldCreate(BaseModel):
    name: str
    column_letter: str
    field_type: str = "text"   # text | number | date
    show_in_form: bool = True
    show_in_smart_paste: bool = True
    required: bool = False     # Phase-8: enforces non-empty at save
    sort_order: int = 0


class CustomFieldUpdate(BaseModel):
    name: Optional[str] = None
    column_letter: Optional[str] = None
    field_type: Optional[str] = None
    show_in_form: Optional[bool] = None
    show_in_smart_paste: Optional[bool] = None
    required: Optional[bool] = None
    sort_order: Optional[int] = None
    active: Optional[bool] = None


class _ContactSaveSettingsUpsert(BaseModel):
    name_format:    Optional[Dict[str, Any]] = None
    field_mapping:  Optional[Dict[str, Any]] = None
    category:       Optional[Dict[str, Any]] = None


class _ContactBuildRequest(BaseModel):
    shipment_id:       Optional[str] = None
    # Inline shipment payload for preview-mode calls on the Settings
    # screen (shows a live preview without persisting anything).
    shipment:          Optional[Dict[str, Any]] = None
    override_category: Optional[str] = ""


class _ContactBulkRequest(BaseModel):
    shipment_ids:      List[str]
    override_category: Optional[str] = ""


class CustomFieldLimitsPayload(BaseModel):
    """Admin-managed per-plan caps. Unknown plans are ignored."""
    free_trial: Optional[int] = None
    silver:     Optional[int] = None
    gold:       Optional[int] = None
    platinum:   Optional[int] = None


def init() -> None:
    """Late-bind routes after server.py defines its helpers."""
    from server import (  # noqa: WPS433 — intentional late import
        db,
        get_current_user as _get_current_user,
        _get_custom_field_limit as _get_custom_field_limit_h,
        user_has_feature as _user_has_feature_h,
        utcnow_iso as _utcnow_iso,
        contact_default_settings as _contact_default_settings,
        ContactSaveSettings as _ContactSaveSettings,
        contact_build as _contact_build,
        contact_to_vcard as _contact_to_vcard,
        CUSTOM_FIELD_DEFAULT_LIMITS as _CUSTOM_FIELD_DEFAULT_LIMITS,
    )

    # =================  Contact-save Settings  ========================

    @custom_fields_router.get("/me/contact-settings")
    async def get_contact_settings(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        doc = await db.contact_settings.find_one(
            {"user_id": current_user["id"]}, {"_id": 0, "user_id": 0},
        )
        if not doc:
            doc = _contact_default_settings()
        return _ContactSaveSettings(**doc).model_dump()

    @custom_fields_router.put("/me/contact-settings")
    async def put_contact_settings(
        payload: _ContactSaveSettingsUpsert,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Merge-save: only the sections the client sent are
        overwritten, the rest remain untouched."""
        existing = await db.contact_settings.find_one(
            {"user_id": current_user["id"]}, {"_id": 0, "user_id": 0},
        ) or _contact_default_settings()
        merged = dict(existing)
        if payload.name_format is not None:
            merged["name_format"] = payload.name_format
        if payload.field_mapping is not None:
            merged["field_mapping"] = payload.field_mapping
        if payload.category is not None:
            merged["category"] = payload.category
        validated = _ContactSaveSettings(**merged).model_dump()
        await db.contact_settings.update_one(
            {"user_id": current_user["id"]},
            {"$set": {**validated, "user_id": current_user["id"]}},
            upsert=True,
        )
        return validated

    async def _resolve_settings(current_user: Dict[str, Any]) -> Dict[str, Any]:
        """Internal helper duplicating get_contact_settings's body so
        we don't accidentally call the FastAPI handler at runtime."""
        doc = await db.contact_settings.find_one(
            {"user_id": current_user["id"]}, {"_id": 0, "user_id": 0},
        )
        if not doc:
            doc = _contact_default_settings()
        return _ContactSaveSettings(**doc).model_dump()

    @custom_fields_router.post("/contacts/build-one")
    async def build_one_contact(
        payload: _ContactBuildRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Return the contact fields for ONE shipment (or an inline
        dict used by the Settings live-preview)."""
        settings = await _resolve_settings(current_user)
        ship: Dict[str, Any] = {}
        if payload.shipment_id:
            ship = await db.shipments.find_one(
                {"id": payload.shipment_id, "user_id": current_user["id"]},
                {"_id": 0},
            ) or {}
            if not ship:
                raise HTTPException(status_code=404, detail="Shipment not found")
        elif payload.shipment:
            ship = payload.shipment
        else:
            raise HTTPException(
                status_code=400, detail="shipment_id or shipment required"
            )
        return _contact_build(ship, settings, payload.override_category or "")

    @custom_fields_router.post("/contacts/build-vcf")
    async def build_bulk_vcf(
        payload: _ContactBulkRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Bulk export: return a text/vcard body with one VCARD per
        shipment. Frontend turns this into a downloadable .vcf file."""
        if not payload.shipment_ids:
            raise HTTPException(status_code=400, detail="shipment_ids empty")
        settings = await _resolve_settings(current_user)
        rows = await db.shipments.find(
            {"id": {"$in": payload.shipment_ids},
             "user_id": current_user["id"]},
            {"_id": 0},
        ).to_list(len(payload.shipment_ids))
        vcards: List[str] = []
        skipped = 0
        for s in rows:
            c = _contact_build(s, settings, payload.override_category or "")
            if not c.get("phone"):
                skipped += 1
                continue
            vcards.append(_contact_to_vcard(c))
        if not vcards:
            raise HTTPException(
                status_code=400,
                detail="No contacts to export (all shipments missing phone)",
            )
        return {
            "vcf":     "\r\n\r\n".join(vcards) + "\r\n",
            "count":   len(vcards),
            "skipped": skipped,
        }

    # =================  Per-user Custom Fields  ========================

    @custom_fields_router.get("/me/custom-fields")
    async def list_my_custom_fields(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Return the caller's custom fields + the plan-driven cap."""
        limit = await _get_custom_field_limit_h(current_user)
        feature_on = await _user_has_feature_h(current_user, "custom_fields")
        fields = await db.user_custom_fields.find(
            {"user_id": current_user["id"]}, {"_id": 0},
        ).sort("sort_order", 1).to_list(100)
        return {
            "fields": fields,
            "limit": limit,
            "used": len([f for f in fields if f.get("active", True)]),
            "feature_enabled": bool(feature_on),
            "plan": (current_user.get("plan") or "free_trial").lower(),
            "is_admin": bool(current_user.get("is_admin")),
        }

    @custom_fields_router.post("/me/custom-fields")
    async def create_my_custom_field(
        payload: CustomFieldCreate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        # Per-plan admin kill-switch.
        if not await _user_has_feature_h(current_user, "custom_fields"):
            raise HTTPException(
                status_code=403,
                detail="Custom fields are not available on your plan.",
            )
        limit = await _get_custom_field_limit_h(current_user)
        existing = await db.user_custom_fields.count_documents(
            {"user_id": current_user["id"], "active": {"$ne": False}},
        )
        if existing >= int(limit):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"You've reached your plan's custom-field cap ({limit}). "
                    "Upgrade or ask admin to raise the cap."
                ),
            )
        name = (payload.name or "").strip()
        col = (payload.column_letter or "").strip().upper()
        if not name:
            raise HTTPException(status_code=400, detail="Field name is required")
        if not re.match(r"^[A-Z]{1,3}$", col):
            raise HTTPException(
                status_code=400,
                detail="Column letter must be A–Z (up to 3 letters like AA).",
            )
        # Uniqueness: same user can't re-use a column letter.
        dup = await db.user_custom_fields.find_one({
            "user_id": current_user["id"],
            "column_letter": col,
            "active": {"$ne": False},
        })
        if dup:
            raise HTTPException(
                status_code=400,
                detail=f"Column {col} is already used by another custom field.",
            )
        doc = {
            "id": str(uuid.uuid4()),
            "user_id": current_user["id"],
            "name": name,
            "column_letter": col,
            "field_type": (payload.field_type or "text").lower(),
            "show_in_form": bool(payload.show_in_form),
            "show_in_smart_paste": bool(payload.show_in_smart_paste),
            "required": bool(payload.required),
            "sort_order": int(payload.sort_order or 0),
            "active": True,
            "created_at": _utcnow_iso(),
        }
        await db.user_custom_fields.insert_one(doc)
        doc.pop("_id", None)
        return doc

    @custom_fields_router.put("/me/custom-fields/{field_id}")
    async def update_my_custom_field(
        field_id: str,
        payload: CustomFieldUpdate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        existing = await db.user_custom_fields.find_one(
            {"id": field_id, "user_id": current_user["id"]}, {"_id": 0},
        )
        if not existing:
            raise HTTPException(status_code=404, detail="Custom field not found")
        updates: Dict[str, Any] = {}
        if payload.name is not None:
            updates["name"] = payload.name.strip()
        if payload.column_letter is not None:
            col = payload.column_letter.strip().upper()
            if not re.match(r"^[A-Z]{1,3}$", col):
                raise HTTPException(status_code=400, detail="Invalid column letter")
            if col != existing.get("column_letter"):
                dup = await db.user_custom_fields.find_one({
                    "user_id": current_user["id"],
                    "column_letter": col,
                    "active": {"$ne": False},
                    "id": {"$ne": field_id},
                })
                if dup:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Column {col} is already used.",
                    )
            updates["column_letter"] = col
        if payload.field_type is not None:
            updates["field_type"] = payload.field_type.lower()
        if payload.show_in_form is not None:
            updates["show_in_form"] = bool(payload.show_in_form)
        if payload.show_in_smart_paste is not None:
            updates["show_in_smart_paste"] = bool(payload.show_in_smart_paste)
        if payload.required is not None:
            updates["required"] = bool(payload.required)
        if payload.sort_order is not None:
            updates["sort_order"] = int(payload.sort_order)
        if payload.active is not None:
            updates["active"] = bool(payload.active)
        if not updates:
            return {"ok": True, "noop": True}
        updates["updated_at"] = _utcnow_iso()
        await db.user_custom_fields.update_one(
            {"id": field_id, "user_id": current_user["id"]},
            {"$set": updates},
        )
        doc = await db.user_custom_fields.find_one({"id": field_id}, {"_id": 0})
        return doc

    @custom_fields_router.delete("/me/custom-fields/{field_id}")
    async def delete_my_custom_field(
        field_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        r = await db.user_custom_fields.delete_one(
            {"id": field_id, "user_id": current_user["id"]},
        )
        if r.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Custom field not found")
        return {"ok": True}

    # =================  Admin: per-plan caps  ===========================

    @custom_fields_router.get("/admin/custom-field-limits")
    async def admin_get_custom_field_limits(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        if not current_user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin only")
        adm = await db.admin_config.find_one(
            {"_id": "default"}, {"_id": 0, "custom_field_limits": 1},
        ) or {}
        return {
            "limits": (
                adm.get("custom_field_limits")
                or _CUSTOM_FIELD_DEFAULT_LIMITS.copy()
            ),
            "defaults": _CUSTOM_FIELD_DEFAULT_LIMITS,
        }

    @custom_fields_router.put("/admin/custom-field-limits")
    async def admin_set_custom_field_limits(
        payload: CustomFieldLimitsPayload,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        if not current_user.get("is_admin"):
            raise HTTPException(status_code=403, detail="Admin only")
        limits: Dict[str, int] = {}
        for k in ("free_trial", "silver", "gold", "platinum"):
            v = getattr(payload, k, None)
            if v is not None:
                try:
                    iv = max(0, int(v))
                except (TypeError, ValueError):
                    iv = _CUSTOM_FIELD_DEFAULT_LIMITS[k]
                limits[k] = iv
        await db.admin_config.update_one(
            {"_id": "default"},
            {"$set": {"custom_field_limits": limits}},
            upsert=True,
        )
        return {"ok": True, "limits": limits}
