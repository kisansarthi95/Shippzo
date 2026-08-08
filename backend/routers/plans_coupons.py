"""
Plans + Coupons read-only/admin endpoints — Phase-4a-extra refactor.

Extracts 9 endpoints that revolve around the plan catalogue,
admin-controlled metadata, and the coupon system out of server.py.

The HEAVIER endpoints (/plans/upgrade, /plans/razorpay/create-order,
/plans/razorpay/verify) STAY in server.py for now — they share a
non-trivial amount of state with `_extend_plan_expiry`,
`_plan_billing_meta`, and the wallet razorpay flow already factored
out. Moving them is a Phase-4b task.

Endpoints relocated (all under /api):

  Plan catalogue + admin-controlled metadata
  -----------------------------------------
  GET /plans                       list_plans
  GET /plans-pricing               get_plans_pricing_public
  GET /credit-packages             get_credit_packages_public
  GET /me/ai-rates                 me_ai_rates

  Coupon system
  -------------
  GET    /admin/coupons             admin_list_coupons
  POST   /admin/coupons             admin_create_coupon
  PUT    /admin/coupons/{id}        admin_update_coupon
  DELETE /admin/coupons/{id}        admin_delete_coupon
  GET    /admin/coupons/analytics   admin_coupon_analytics
  POST   /coupons/validate          coupon_validate

Pattern: late-binding `init()` — same as routers/wallet.py.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

# Coupon helpers + Pydantic models live in their own module
# (/app/backend/coupons.py) — we import them eagerly because that
# module has NO dependency on server.py, so no circular-import risk.
from coupons import (
    CouponCreate,
    CouponUpdate,
    coupon_to_api,
    new_coupon_doc,
    validate_coupon,
    ensure_code_valid,
)


plans_coupons_router = APIRouter(prefix="/api", tags=["plans-coupons"])


# ============================== Models ==============================

class CouponValidateRequest(BaseModel):
    """Body for POST /coupons/validate (user-side dry-run)."""
    code: str
    plan_key: str           # silver | gold | platinum
    billing_cycle: str      # monthly | yearly


def init() -> None:
    """Register routes after server.py finishes initialising."""
    import logging
    _logger = logging.getLogger("routers.plans_coupons")
    from server import (  # noqa: WPS433 — intentional late import
        db,
        get_current_user as _get_current_user,
        _require_admin,
        _get_admin_config,
        _plan_billing_meta,
        public_plan_list,
    )

    # =================  Plan catalogue + metadata  =====================

    @plans_coupons_router.get("/plans")
    async def list_plans(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Return the 4-tier plan catalogue plus a hint about which plan
        the caller is currently on (so the Plans screen can badge it)."""
        return {
            "plans": await public_plan_list(db),
            "current": current_user.get("plan") or "free_trial",
        }

    @plans_coupons_router.get("/plans-pricing")
    async def get_plans_pricing_public(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Public read of plan_pricing + countdown for the Plans screen.
        Available to every logged-in user (not just admins)."""
        cfg = await _get_admin_config()
        return {
            "plan_pricing": cfg["plan_pricing"],
            "countdown": cfg["countdown"],
        }

    @plans_coupons_router.get("/credit-packages")
    async def get_credit_packages_public(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Read-only list every logged-in user can fetch for the
        Wallet Top-up screen."""
        cfg = await _get_admin_config()
        return {"packages": cfg["credit_packages"]}

    @plans_coupons_router.get("/me/ai-rates")
    async def me_ai_rates(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Read-only: rates the user will be charged for Smart Paste calls.
        These are admin-controlled now; per-user overrides are no longer used."""
        cfg = await _get_admin_config()
        return cfg["global_ai_rates"]

    # =================  Coupons — admin CRUD  =========================

    @plans_coupons_router.get("/admin/coupons")
    async def admin_list_coupons(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        _require_admin(current_user)
        cur = db.coupons.find({}, {"_id": 0}).sort("created_at", -1)
        out: List[Dict[str, Any]] = []
        async for c in cur:
            out.append(coupon_to_api(c))
        return {"coupons": out}

    @plans_coupons_router.post("/admin/coupons")
    async def admin_create_coupon(
        payload: CouponCreate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        _require_admin(current_user)
        existing = await db.coupons.find_one({"code": payload.code})
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Coupon '{payload.code}' already exists",
            )
        doc = new_coupon_doc(payload)
        await db.coupons.insert_one(doc)
        return {"ok": True, "coupon": coupon_to_api(doc)}

    @plans_coupons_router.put("/admin/coupons/{coupon_id}")
    async def admin_update_coupon(
        coupon_id: str,
        payload: CouponUpdate,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        _require_admin(current_user)
        update_fields: Dict[str, Any] = {}
        raw = payload.model_dump(exclude_unset=True)
        for k, v in raw.items():
            update_fields[k] = v
        if not update_fields:
            raise HTTPException(status_code=400, detail="No fields to update")
        update_fields["updated_at"] = (
            datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
        )
        res = await db.coupons.find_one_and_update(
            {"id": coupon_id},
            {"$set": update_fields},
            return_document=True,
        )
        if not res:
            raise HTTPException(status_code=404, detail="Coupon not found")
        return {"ok": True, "coupon": coupon_to_api(res)}

    @plans_coupons_router.delete("/admin/coupons/{coupon_id}")
    async def admin_delete_coupon(
        coupon_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        _require_admin(current_user)
        res = await db.coupons.delete_one({"id": coupon_id})
        if res.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Coupon not found")
        return {"ok": True, "deleted": coupon_id}

    @plans_coupons_router.get("/admin/coupons/analytics")
    async def admin_coupon_analytics(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Usage analytics dashboard for coupons. Aggregates from the
        coupons collection only (no expensive joins needed) — every
        coupon doc already carries `used_count`, status, and metadata.

        Response shape (aligned with the frontend contract):
            {
                totals: {
                    redemptions:    int,   # sum of used_count across all coupons
                    total_discount: float, # sum of used_count * flat_value
                    total_revenue:  float, # sum of used_count * (min_order_amount or 0)
                },
                coupons: [
                    {
                        code, label, discount, type,
                        redemptions, max_uses, status,
                        total_discount, total_revenue,
                    }
                ],
                total_coupons: int,
                status_counts: { status: count },
                # Legacy aliases retained for older frontend builds.
                total_used, active, top5,
            }
        """
        _require_admin(current_user)
        coupons: List[Dict[str, Any]] = []
        async for c in db.coupons.find({}, {"_id": 0}):
            coupons.append(c)

        # ── Per-coupon aggregates ─────────────────────────────────
        # `redemptions` mirrors `used_count` (frontend nomenclature).
        # Total discount = redemptions * discount_value for FLAT
        # coupons. For PERCENT coupons we can't compute the exact
        # discount without the underlying transactions, so we
        # approximate: redemptions * (min_order_amount * pct / 100)
        # when min_order_amount is set, else 0.
        rows: List[Dict[str, Any]] = []
        totals_redemptions = 0
        totals_discount    = 0.0
        totals_revenue     = 0.0
        for c in coupons:
            used = int(c.get("used_count") or 0)
            dtype = (c.get("discount_type") or "flat").lower()
            dval  = float(c.get("discount_value") or 0)
            min_order = float(c.get("min_order_amount") or 0)
            if dtype in ("percent", "percentage", "pct"):
                per_redemption_discount = (min_order * dval / 100.0) if min_order else 0.0
            else:
                per_redemption_discount = dval
            row_discount = round(used * per_redemption_discount, 2)
            row_revenue  = round(used * max(min_order, 0.0), 2)
            totals_redemptions += used
            totals_discount    += row_discount
            totals_revenue     += row_revenue
            rows.append({
                "code":           c.get("code"),
                "label":          c.get("label"),
                "discount":       c.get("discount_value"),
                "type":           c.get("discount_type"),
                # Phase F11.J — frontend contract:
                "redemptions":    used,
                "total_discount": row_discount,
                "total_revenue":  row_revenue,
                # Legacy alias so older frontend builds keep working.
                "used_count":     used,
                "max_uses":       int(c.get("max_uses") or 0) or None,
                "status":         c.get("status") or "active",
            })

        rows.sort(key=lambda r: r["redemptions"], reverse=True)
        status_counts: Dict[str, int] = {}
        active = 0
        for c in coupons:
            s = c.get("status") or "active"
            status_counts[s] = status_counts.get(s, 0) + 1
            if s == "active":
                active += 1

        return {
            # Phase F11.J — canonical shape.
            "totals": {
                "redemptions":    totals_redemptions,
                "total_discount": round(totals_discount, 2),
                "total_revenue":  round(totals_revenue, 2),
            },
            "coupons":       rows,
            "total_coupons": len(coupons),
            "status_counts": status_counts,
            # ── Legacy fields (kept for backward compat) ──────────
            "total_used":    totals_redemptions,
            "active":        active,
            "top5":          rows[:5],
        }

    # =================  Coupons — user validation  ====================

    @plans_coupons_router.post("/coupons/validate")
    async def coupon_validate(
        payload: CouponValidateRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """User-side: check whether a coupon applies to a plan/cycle and
        return the discounted total. No DB writes happen here — the
        actual consumption (used_count++) only fires on successful
        payment-verify.
        """
        code = ensure_code_valid(payload.code)
        if payload.plan_key not in ("silver", "gold", "platinum"):
            raise HTTPException(status_code=400, detail="Invalid plan_key")
        if payload.billing_cycle not in ("monthly", "yearly"):
            raise HTTPException(status_code=400, detail="Invalid billing_cycle")
        cfg = await _get_admin_config()
        meta = _plan_billing_meta(
            cfg["plan_pricing"], payload.plan_key, payload.billing_cycle,
        )
        base_inr = int(meta["price_inr"])
        coupon = await db.coupons.find_one({"code": code})
        ok, reason, discount, final_inr = validate_coupon(
            coupon, payload.plan_key, payload.billing_cycle, base_inr,
            user_email=current_user.get("email"),
        )
        # For percent coupons, surface the admin's *configured* percentage
        # directly (e.g. 25) rather than back-computing from the floored
        # discount (which would be 24 for base=1791, discount=447). For
        # flat coupons we still compute from the actual money savings.
        savings_pct = 0
        if ok and base_inr > 0:
            if coupon and coupon.get("discount_type") == "percent":
                savings_pct = int(coupon.get("discount_value") or 0)
            else:
                savings_pct = int((discount / base_inr) * 100)
        return {
            "ok":         ok,
            "reason":     reason,
            "code":       code,
            "base_inr":   base_inr,
            "discount":   discount,
            "final_inr":  final_inr,
            "savings_pct": savings_pct,
        }
