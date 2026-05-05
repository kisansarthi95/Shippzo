"""
Couriers + Variants + Categories — Phase-3 incremental refactor.

Extracts the 17 endpoints related to courier-partner management out of
the server.py monolith. Public API surface is 100% unchanged.

Endpoints (all under /api):
  GET    /couriers                                       list_couriers
  GET    /couriers/limits                                get_courier_limits
  POST   /couriers                                       create_courier
  PUT    /couriers/{courier_id}                          update_courier
  DELETE /couriers/{courier_id}                          delete_courier
  GET    /couriers/{courier_id}                          get_courier
  GET    /couriers/{courier_id}/next-tracking            peek_next_tracking
  POST   /couriers/{courier_id}/consume-tracking         consume_tracking

  GET    /couriers/{courier_id}/variants                 list_courier_variants
  POST   /couriers/{courier_id}/variants                 create_courier_variant
  PUT    /couriers/{courier_id}/variants/{variant_id}    update_courier_variant
  DELETE /couriers/{courier_id}/variants/{variant_id}    delete_courier_variant
  POST   /couriers/{courier_id}/variants/copy-from/{src} copy_variants_from_courier

  GET    /me/all-variants                                list_all_variants
  GET    /me/categories                                  list_my_categories
  POST   /me/categories                                  add_my_category
  DELETE /me/categories/{name}                           remove_my_category

Pattern: late-binding `init()` so we can `from server import …` after
all helpers (db, get_current_user, models, plan caps) are defined.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo import ReturnDocument


couriers_router = APIRouter(prefix="/api", tags=["couriers"])


def init() -> None:
    """Register routes after server.py defines its helpers/models."""
    from server import (  # noqa: WPS433 — intentional late import
        db,
        get_current_user as _get_current_user,
        Courier as _Courier,
        CourierCreate as _CourierCreate,
        CourierUpdate as _CourierUpdate,
        CourierVariant as _CourierVariant,
        CourierVariantCreate as _CourierVariantCreate,
        CourierVariantUpdate as _CourierVariantUpdate,
        _courier_limit_for_plan as _courier_limit_for_plan_h,
        _next_tier_suggestion as _next_tier_suggestion_h,
        _packing_variant_cap_for_user as _packing_variant_cap_for_user_h,
        _PLAN_LABELS as _PLAN_LABELS_h,
        PACKAGE_TYPES as _PACKAGE_TYPES,
        CATEGORIES as _CATEGORIES,
        strip_id as _strip_id,
    )

    # ===========  Couriers (CRUD + limits + tracking helpers)  ============

    @couriers_router.get("/couriers", response_model=List[_Courier])
    async def list_couriers(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        docs = await db.couriers.find(
            {"user_id": current_user["id"]}, {"_id": 0}
        ).sort("created_at", 1).to_list(200)
        return [_Courier(**d) for d in docs]

    @couriers_router.get("/couriers/limits")
    async def get_courier_limits(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Return the caller's current courier count + the cap dictated
        by their plan. Used by the frontend to render `X / Y used`
        badges and to swap the "Add Courier Partner" CTA for an upgrade
        prompt when the cap is reached.

        Admins always see `is_unlimited: true`.
        """
        plan_key = (current_user.get("plan") or "free_trial").lower()
        is_admin = bool(current_user.get("is_admin"))
        current_count = await db.couriers.count_documents(
            {"user_id": current_user["id"]}
        )
        limit = None if is_admin else _courier_limit_for_plan_h(plan_key)
        is_unlimited = limit is None
        can_add = True if is_unlimited else (current_count < int(limit))
        return {
            "plan": plan_key,
            "plan_label": _PLAN_LABELS_h.get(plan_key, plan_key.title()),
            "is_admin": is_admin,
            "limit": limit,
            "current_count": int(current_count),
            "can_add": bool(can_add),
            "is_unlimited": bool(is_unlimited),
            "suggested_upgrade": (
                None if is_admin else _next_tier_suggestion_h(plan_key)
            ),
        }

    @couriers_router.post("/couriers", response_model=_Courier)
    async def create_courier(
        payload: _CourierCreate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        # Enforce per-plan courier partner cap. Admins bypass the check
        # so internal support accounts can set up demo data freely.
        if not current_user.get("is_admin"):
            plan_key = (current_user.get("plan") or "free_trial").lower()
            limit = _courier_limit_for_plan_h(plan_key)
            if limit is not None:
                current_count = await db.couriers.count_documents(
                    {"user_id": current_user["id"]}
                )
                if current_count >= int(limit):
                    plan_label = _PLAN_LABELS_h.get(plan_key, plan_key.title())
                    suggest = _next_tier_suggestion_h(plan_key)
                    msg = (
                        f"Your {plan_label} plan allows only {limit} "
                        f"courier partner"
                        + ("" if int(limit) == 1 else "s") + "."
                    )
                    if suggest:
                        msg += f" Upgrade to {suggest} to add more."
                    else:
                        msg += " Please contact support if you need more."
                    raise HTTPException(status_code=403, detail=msg)
        courier = _Courier(**payload.model_dump())
        doc = courier.model_dump()
        doc["user_id"] = current_user["id"]
        await db.couriers.insert_one(doc)
        return courier

    @couriers_router.put("/couriers/{courier_id}", response_model=_Courier)
    async def update_courier(
        courier_id: str,
        payload: _CourierUpdate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        update = {k: v for k, v in payload.model_dump().items() if v is not None}
        if not update:
            raise HTTPException(status_code=400, detail="No fields to update")
        res = await db.couriers.find_one_and_update(
            {"id": courier_id, "user_id": current_user["id"]},
            {"$set": update},
            return_document=True,
        )
        if not res:
            raise HTTPException(status_code=404, detail="Courier not found")
        return _Courier(**_strip_id(res))

    @couriers_router.delete("/couriers/{courier_id}")
    async def delete_courier(
        courier_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        res = await db.couriers.delete_one(
            {"id": courier_id, "user_id": current_user["id"]}
        )
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Courier not found")
        return {"ok": True}

    @couriers_router.get("/couriers/{courier_id}", response_model=_Courier)
    async def get_courier(
        courier_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        doc = await db.couriers.find_one(
            {"id": courier_id, "user_id": current_user["id"]}, {"_id": 0}
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Courier not found")
        return _Courier(**doc)

    @couriers_router.get("/couriers/{courier_id}/next-tracking")
    async def peek_next_tracking(
        courier_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        doc = await db.couriers.find_one(
            {"id": courier_id, "user_id": current_user["id"]}, {"_id": 0}
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Courier not found")
        c = _Courier(**doc)
        num = str(c.next_number).zfill(c.number_padding)
        return {
            "tracking_id": f"{c.series_prefix}{num}",
            "next_number": c.next_number,
        }

    @couriers_router.post("/couriers/{courier_id}/consume-tracking")
    async def consume_tracking(
        courier_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        doc = await db.couriers.find_one(
            {"id": courier_id, "user_id": current_user["id"]}, {"_id": 0}
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Courier not found")
        c = _Courier(**doc)
        tid = f"{c.series_prefix}{str(c.next_number).zfill(c.number_padding)}"
        await db.couriers.update_one(
            {"id": courier_id, "user_id": current_user["id"]},
            {"$inc": {"next_number": 1}},
        )
        return {"tracking_id": tid}

    # ===================  Courier Packing Variants  =======================

    @couriers_router.get("/couriers/{courier_id}/variants")
    async def list_courier_variants(
        courier_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """List all variants for a given courier (own data only)."""
        courier = await db.couriers.find_one(
            {"id": courier_id, "user_id": current_user["id"]},
            {"_id": 0, "id": 1, "name": 1},
        )
        if not courier:
            raise HTTPException(status_code=404, detail="Courier not found")
        rows = await db.courier_variants.find(
            {"courier_id": courier_id, "user_id": current_user["id"]},
            {"_id": 0},
        ).sort("created_at", 1).to_list(50)
        cap = _packing_variant_cap_for_user_h(current_user)
        return {
            "variants":      rows,
            "cap":           cap,
            "current_count": len(rows),
            "remaining":     None if cap is None else max(0, cap - len(rows)),
            "package_types": _PACKAGE_TYPES,
            "categories":    _CATEGORIES,
        }

    @couriers_router.post(
        "/couriers/{courier_id}/variants", response_model=_CourierVariant
    )
    async def create_courier_variant(
        courier_id: str,
        body: _CourierVariantCreate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Create a new variant for a courier — enforces plan-wise cap."""
        courier = await db.couriers.find_one(
            {"id": courier_id, "user_id": current_user["id"]},
            {"_id": 0, "id": 1},
        )
        if not courier:
            raise HTTPException(status_code=404, detail="Courier not found")
        if not (body.variant_name or "").strip():
            raise HTTPException(status_code=400, detail="variant_name is required")

        cap = _packing_variant_cap_for_user_h(current_user)
        if cap is not None:
            existing = await db.courier_variants.count_documents(
                {"courier_id": courier_id, "user_id": current_user["id"]},
            )
            if existing >= cap:
                raise HTTPException(
                    status_code=402,
                    detail=(
                        f"Packing variant limit reached for your plan ({cap}). "
                        "Upgrade to add more variants per courier."
                    ),
                )

        variant = _CourierVariant(
            user_id=current_user["id"],
            courier_id=courier_id,
            variant_name=body.variant_name.strip(),
            package_type=(body.package_type or "").strip(),
            category=(body.category or "").strip(),
            length_cm=float(body.length_cm or 0),
            width_cm=float(body.width_cm or 0),
            height_cm=float(body.height_cm or 0),
            weight_g=float(body.weight_g or 0),
            within_state_rate=float(body.within_state_rate or 0),
            outside_state_rate=float(body.outside_state_rate or 0),
            active=bool(body.active if body.active is not None else True),
        )
        await db.courier_variants.insert_one(variant.model_dump())
        return variant

    @couriers_router.put(
        "/couriers/{courier_id}/variants/{variant_id}",
        response_model=_CourierVariant,
    )
    async def update_courier_variant(
        courier_id: str,
        variant_id: str,
        body: _CourierVariantUpdate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        update: Dict[str, Any] = {}
        for field in (
            "variant_name", "package_type", "category", "length_cm",
            "width_cm", "height_cm", "weight_g", "within_state_rate",
            "outside_state_rate", "active",
        ):
            v = getattr(body, field)
            if v is not None:
                update[field] = (
                    v.strip() if isinstance(v, str) else
                    bool(v) if field == "active" else
                    float(v) if field in (
                        "length_cm", "width_cm", "height_cm", "weight_g",
                        "within_state_rate", "outside_state_rate",
                    ) else v
                )
        if not update:
            raise HTTPException(status_code=400, detail="No fields to update")
        res = await db.courier_variants.find_one_and_update(
            {"id": variant_id, "courier_id": courier_id,
             "user_id": current_user["id"]},
            {"$set": update},
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0},
        )
        if not res:
            raise HTTPException(status_code=404, detail="Variant not found")
        return _CourierVariant(**res)

    @couriers_router.delete("/couriers/{courier_id}/variants/{variant_id}")
    async def delete_courier_variant(
        courier_id: str,
        variant_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        res = await db.courier_variants.delete_one(
            {"id": variant_id, "courier_id": courier_id,
             "user_id": current_user["id"]},
        )
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Variant not found")
        return {"ok": True}

    @couriers_router.get("/me/all-variants")
    async def list_all_variants(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Convenience: return ALL variants across the user's couriers
        — used by the New Shipment form to pre-load the variant picker
        once on open instead of fetching per-courier."""
        rows = await db.courier_variants.find(
            {"user_id": current_user["id"], "active": True},
            {"_id": 0},
        ).sort("created_at", 1).to_list(500)
        by_courier: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            by_courier.setdefault(r["courier_id"], []).append(r)
        return {
            "variants":      rows,
            "by_courier":    by_courier,
            "package_types": _PACKAGE_TYPES,
            "categories":    _CATEGORIES,
        }

    # ====================  User-defined Categories  =======================

    @couriers_router.get("/me/categories")
    async def list_my_categories(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        doc = await db.user_meta.find_one(
            {"user_id": current_user["id"]},
            {"_id": 0, "custom_categories": 1},
        )
        custom = sorted((doc or {}).get("custom_categories") or [])
        return {"presets": _CATEGORIES, "custom": custom}

    @couriers_router.post("/me/categories")
    async def add_my_category(
        payload: Dict[str, Any] = Body(...),
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        name = (payload.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Category name required")
        if len(name) > 40:
            raise HTTPException(
                status_code=400, detail="Category name too long (40 chars max)"
            )
        if name.lower() in {c.lower() for c in _CATEGORIES}:
            raise HTTPException(
                status_code=400,
                detail=f"'{name}' is already a built-in category",
            )
        await db.user_meta.update_one(
            {"user_id": current_user["id"]},
            {"$addToSet": {"custom_categories": name},
             "$setOnInsert": {"user_id": current_user["id"]}},
            upsert=True,
        )
        doc = await db.user_meta.find_one(
            {"user_id": current_user["id"]},
            {"_id": 0, "custom_categories": 1},
        )
        return {
            "presets": _CATEGORIES,
            "custom": sorted((doc or {}).get("custom_categories") or []),
        }

    @couriers_router.delete("/me/categories/{name}")
    async def remove_my_category(
        name: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        await db.user_meta.update_one(
            {"user_id": current_user["id"]},
            {"$pull": {"custom_categories": name}},
        )
        doc = await db.user_meta.find_one(
            {"user_id": current_user["id"]},
            {"_id": 0, "custom_categories": 1},
        )
        return {
            "presets": _CATEGORIES,
            "custom": sorted((doc or {}).get("custom_categories") or []),
        }

    # =====================  Variants — copy-from  =========================

    @couriers_router.post(
        "/couriers/{courier_id}/variants/copy-from/{source_courier_id}"
    )
    async def copy_variants_from_courier(
        courier_id: str,
        source_courier_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Bulk-clone all active variants from one courier to another.
        Plan cap is honoured — if the source has more variants than the
        target's remaining slots, only the first N are copied and the
        rest are reported as `skipped`."""
        if courier_id == source_courier_id:
            raise HTTPException(
                status_code=400,
                detail="Source and target courier are the same",
            )

        target = await db.couriers.find_one(
            {"id": courier_id, "user_id": current_user["id"]},
            {"_id": 0, "id": 1, "name": 1},
        )
        if not target:
            raise HTTPException(
                status_code=404, detail="Target courier not found"
            )
        source = await db.couriers.find_one(
            {"id": source_courier_id, "user_id": current_user["id"]},
            {"_id": 0, "id": 1, "name": 1},
        )
        if not source:
            raise HTTPException(
                status_code=404, detail="Source courier not found"
            )

        src_variants = await db.courier_variants.find(
            {"user_id": current_user["id"],
             "courier_id": source_courier_id,
             "active": True},
            {"_id": 0},
        ).sort("created_at", 1).to_list(100)
        if not src_variants:
            raise HTTPException(
                status_code=400,
                detail="Source courier has no variants to copy",
            )

        cap = _packing_variant_cap_for_user_h(current_user)
        existing = await db.courier_variants.count_documents(
            {"courier_id": courier_id, "user_id": current_user["id"]},
        )
        remaining: Optional[int] = (
            None if cap is None else max(0, cap - existing)
        )
        if remaining == 0:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Target courier already at plan cap ({cap}). "
                    "Upgrade or remove some variants."
                ),
            )

        existing_names = set(
            v["variant_name"].strip().lower()
            for v in await db.courier_variants.find(
                {"courier_id": courier_id, "user_id": current_user["id"]},
                {"variant_name": 1, "_id": 0},
            ).to_list(200)
        )

        copied: List[Dict[str, Any]] = []
        skipped_dupes: List[str] = []
        skipped_cap: List[str] = []
        for v in src_variants:
            nm = v["variant_name"].strip()
            if nm.lower() in existing_names:
                skipped_dupes.append(nm)
                continue
            if remaining is not None and len(copied) >= remaining:
                skipped_cap.append(nm)
                continue
            clone = _CourierVariant(
                user_id=current_user["id"],
                courier_id=courier_id,
                variant_name=nm,
                package_type=v.get("package_type", ""),
                category=v.get("category", ""),
                length_cm=float(v.get("length_cm") or 0),
                width_cm=float(v.get("width_cm") or 0),
                height_cm=float(v.get("height_cm") or 0),
                weight_g=float(v.get("weight_g") or 0),
                within_state_rate=float(v.get("within_state_rate") or 0),
                outside_state_rate=float(v.get("outside_state_rate") or 0),
                active=True,
            )
            await db.courier_variants.insert_one(clone.model_dump())
            copied.append(clone.model_dump())
            existing_names.add(nm.lower())

        return {
            "ok": True,
            "copied_count": len(copied),
            "skipped_duplicates": skipped_dupes,
            "skipped_cap_full": skipped_cap,
            "source_courier_name": source["name"],
            "target_courier_name": target["name"],
        }
