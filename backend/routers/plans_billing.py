"""
Plan upgrade + Razorpay subscription endpoints — Phase-5c-1 refactor.

Extracts 3 mutation endpoints out of server.py monolith:

  POST /plans/upgrade                  upgrade_plan          (mock flow)
  POST /plans/razorpay/create-order    rzp_create_plan_order (real Razorpay)
  POST /plans/razorpay/verify          rzp_verify_plan_subscription

Sister wallet endpoints (/wallet/razorpay/*) already live in
routers/wallet.py — both call into the same _extend_plan_expiry helper
which STAYS in server.py (also referenced by the wallet webhook).

Pattern: late-binding `init()` — same as routers/wallet.py.
"""
from datetime import datetime, timezone
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from coupons import ensure_code_valid, validate_coupon


plans_billing_router = APIRouter(prefix="/api", tags=["plans-billing"])


# ============================== Models ==============================

class UpgradePlanRequest(BaseModel):
    """Body for POST /plans/upgrade (mock upgrade)."""
    plan: str  # free_trial | silver | gold | platinum


class PlanRazorpayCreateOrderRequest(BaseModel):
    """Body for POST /plans/razorpay/create-order."""
    plan_key: str             # silver | gold | platinum
    billing_cycle: str        # monthly | yearly
    coupon_code: Optional[str] = None  # 2026-04-30: discount support


class RazorpayVerifyRequest(BaseModel):
    """Three values returned by Razorpay Checkout on success.

    Note: this is a duplicate of the same model in routers/wallet.py;
    both routers verify Razorpay payments but each owns its purpose
    (wallet_topup vs plan_subscription) so keeping the small DRY
    duplication here is cleaner than adding a shared models/ module
    just for one model.
    """
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


def init() -> None:
    """Register routes after server.py finishes initialising."""
    import logging
    _logger = logging.getLogger("routers.plans_billing")
    from server import (  # noqa: WPS433 — intentional late import
        db,
        get_current_user as _get_current_user,
        PLAN_TABLE,
        plan_start_payload,
        user_public,
        _rzp_client,
        _RZP_KEY_ID,
        _get_admin_config,
        _plan_billing_meta,
        _extend_plan_expiry,
    )

    # =================  Plan upgrade (mock)  ==========================

    @plans_billing_router.post("/plans/upgrade")
    async def upgrade_plan(
        payload: UpgradePlanRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """MOCK upgrade flow.

        Simply switches the user's plan record and restarts the
        relevant validity window (trial_expires_at for free_trial,
        open-ended for paid tiers). No money changes hands.

        Real money flows through /plans/razorpay/create-order +
        /verify below.

        SECURITY: Downgrading to free_trial after it's been consumed
        does NOT reset the lifetime trial counter — the user will still
        hit the 10-label cap immediately. This prevents "reset-abuse".
        """
        key = (payload.plan or "").strip().lower()
        if key not in PLAN_TABLE:
            raise HTTPException(
                status_code=400, detail=f"Unknown plan '{payload.plan}'",
            )
        set_payload = await plan_start_payload(db, key)
        # Stamp a flag so the UI can display "Upgrade mocked".
        set_payload["plan_mocked"] = True
        await db.users.update_one(
            {"id": current_user["id"]}, {"$set": set_payload},
        )
        fresh = await db.users.find_one(
            {"id": current_user["id"]}, {"_id": 0},
        )
        return {
            "ok": True,
            "mocked": True,
            "plan": key,
            "plan_started_at": set_payload["plan_started_at"],
            "plan_expires_at": set_payload.get("plan_expires_at"),
            "user": user_public(fresh or {}),
        }

    # =================  Cancel auto-renewal  ============================

    @plans_billing_router.post("/me/cancel-subscription")
    async def cancel_subscription(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """User-initiated cancellation. Currently we use Razorpay Orders
        (one-time charges, NOT recurring subscriptions) so there is
        nothing to cancel mid-cycle — the user simply doesn't pay next
        time. The paid plan stays active until plan_expires_at, after
        which ensure_can_create_label blocks label creation.

        This endpoint flips an `auto_renew=false` flag (forward-compatible
        with future Razorpay Subscriptions integration) and stamps a
        cancellation timestamp the Settings UI shows.

        Phase-5d (2026-05-07): the original endpoint in server.py lost
        its decorator during a previous refactor and silently 404-ed
        for weeks. Recovered + relocated to this router with the bug
        fix. Frontend (`Api.cancelSubscription`) needs no change — it
        already targets POST /api/me/cancel-subscription.
        """
        fresh = await db.users.find_one({"id": current_user["id"]}, {"_id": 0}) or {}
        plan = fresh.get("plan") or "free_trial"
        if plan == "free_trial":
            raise HTTPException(
                status_code=400,
                detail="You're on the free trial — there's nothing to cancel.",
            )
        await db.users.update_one(
            {"id": current_user["id"]},
            {"$set": {
                "auto_renew":   False,
                "cancelled_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        return {
            "ok":              True,
            "plan":            plan,
            "plan_expires_at": fresh.get("plan_expires_at"),
            "message": (
                "Auto-renewal cancelled. Your plan stays active until "
                f"{fresh.get('plan_expires_at') or 'expiry'}, after which "
                "you'll be moved to the free trial."
            ),
        }

    # =================  Razorpay plan subscription  ====================

    @plans_billing_router.post("/plans/razorpay/create-order")
    async def rzp_create_plan_order(
        payload: PlanRazorpayCreateOrderRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Step 1 of Plan Subscription Razorpay flow.

        Creates a Razorpay order keyed to a plan/cycle and persists the
        intent so the verify endpoint can extend the user's plan
        validity correctly on success.

        Coupon support: if `coupon_code` is supplied, validate it and
        apply the discount to the price BEFORE creating the Razorpay
        order. The coupon's used_count is bumped only on /verify
        success — i.e. only if the user actually pays.
        """
        if not _rzp_client:
            raise HTTPException(
                status_code=503,
                detail="Razorpay is not configured on the server.",
            )
        if payload.plan_key not in PLAN_TABLE:
            raise HTTPException(
                status_code=400, detail=f"Unknown plan '{payload.plan_key}'",
            )

        cfg = await _get_admin_config()
        meta = _plan_billing_meta(
            cfg["plan_pricing"], payload.plan_key, payload.billing_cycle,
        )
        base_inr = int(meta["price_inr"])

        # ── Apply coupon discount before creating Razorpay order ──
        coupon_meta: Dict[str, Any] = {"applied": False}
        if payload.coupon_code:
            code = ensure_code_valid(payload.coupon_code)
            coupon_doc = await db.coupons.find_one({"code": code})
            ok, reason, discount, final_inr = validate_coupon(
                coupon_doc, payload.plan_key, payload.billing_cycle,
                base_inr, user_email=current_user.get("email"),
            )
            if not ok:
                raise HTTPException(
                    status_code=400, detail=f"Coupon: {reason}",
                )
            coupon_meta = {
                "applied":   True,
                "code":      code,
                "discount":  discount,
                "base_inr":  base_inr,
                "final_inr": final_inr,
            }
            inr = final_inr
        else:
            inr = base_inr

        # Razorpay receipt: 40-char limit. Encode plan + cycle for ops.
        cycle_short = "y" if payload.billing_cycle == "yearly" else "m"
        receipt = (
            f"plan-{payload.plan_key[:6]}-{cycle_short}-"
            f"{current_user['id'][:6]}-"
            f"{int(datetime.utcnow().timestamp())}"
        )
        receipt = receipt[:40]

        try:
            rzp_order = _rzp_client.order.create({
                "amount": inr * 100,  # paise
                "currency": "INR",
                "receipt": receipt,
                "payment_capture": 1,
                "notes": {
                    "user_id":       current_user["id"],
                    "user_email":    current_user.get("email", ""),
                    "purpose":       "plan_subscription",
                    "plan_key":      payload.plan_key,
                    "billing_cycle": payload.billing_cycle,
                    "months":        meta["months"],
                    "bonus_months":  meta["bonus_months"],
                },
            })
        except Exception as e:
            _logger.exception("rzp create plan order failed")
            raise HTTPException(
                status_code=502, detail=f"Razorpay error: {e}",
            )

        await db.razorpay_orders.insert_one({
            "id":                 str(uuid.uuid4()),
            "user_id":            current_user["id"],
            "razorpay_order_id":  rzp_order["id"],
            "amount_inr":         inr,
            "amount_paise":       inr * 100,
            "purpose":            "plan_subscription",
            "plan_key":           payload.plan_key,
            "billing_cycle":      payload.billing_cycle,
            "months":             meta["months"],
            "bonus_months":       meta["bonus_months"],
            "status":             "created",
            "created_at":         datetime.utcnow().isoformat() + "+00:00",
            # Coupon trail (consumed on verify if payment succeeds).
            "coupon_code":     coupon_meta.get("code"),
            "coupon_discount": coupon_meta.get("discount") or 0,
            "coupon_base_inr": base_inr,
        })

        plan_meta = PLAN_TABLE.get(payload.plan_key)
        return {
            "key_id":        _RZP_KEY_ID,
            "order_id":      rzp_order["id"],
            "amount_paise":  rzp_order["amount"],
            "amount_inr":    inr,
            "currency":      rzp_order["currency"],
            "receipt":       rzp_order["receipt"],
            "purpose":       "plan_subscription",
            "plan_key":      payload.plan_key,
            "plan_name": (
                getattr(plan_meta, "name", payload.plan_key.title())
                if plan_meta else payload.plan_key.title()
            ),
            "billing_cycle": payload.billing_cycle,
            "months":        meta["months"],
            "bonus_months":  meta["bonus_months"],
            "user_email":    current_user.get("email", ""),
            "user_name":     current_user.get(
                "name", current_user.get("email", "User"),
            ),
            "coupon":   coupon_meta,
            "base_inr": base_inr,
        }

    @plans_billing_router.post("/plans/razorpay/verify")
    async def rzp_verify_plan_subscription(
        payload: RazorpayVerifyRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Step 2 of Plan Subscription Razorpay flow.

        Verifies the payment signature, switches the user's plan, and
        extends plan_expires_at by (months + bonus_months) — carrying
        over any unused validity if the user is already on a paid plan.

        Bumps the coupon's used_count on success (idempotent: a paid
        order is never re-credited).
        """
        if not _rzp_client:
            raise HTTPException(
                status_code=503, detail="Razorpay not configured",
            )

        order = await db.razorpay_orders.find_one({
            "razorpay_order_id": payload.razorpay_order_id,
            "user_id":           current_user["id"],
        })
        if not order:
            raise HTTPException(
                status_code=404, detail="Order not found for this user",
            )
        if order.get("purpose") != "plan_subscription":
            raise HTTPException(
                status_code=400,
                detail=(
                    "This order isn't a plan subscription. Use "
                    "/wallet/razorpay/verify for top-ups."
                ),
            )

        # Idempotency.
        if order.get("status") == "paid":
            fresh = await db.users.find_one(
                {"id": current_user["id"]}, {"_id": 0},
            )
            return {
                "ok":               True,
                "already_credited": True,
                "plan":             order.get("plan_key"),
                "billing_cycle":    order.get("billing_cycle"),
                "plan_expires_at":  (fresh or {}).get("plan_expires_at"),
            }

        # Verify signature.
        try:
            _rzp_client.utility.verify_payment_signature({
                "razorpay_order_id":   payload.razorpay_order_id,
                "razorpay_payment_id": payload.razorpay_payment_id,
                "razorpay_signature":  payload.razorpay_signature,
            })
        except Exception as e:
            await db.razorpay_orders.update_one(
                {"_id": order["_id"]},
                {"$set": {
                    "status":             "verify_failed",
                    "error":              str(e),
                    "razorpay_payment_id": payload.razorpay_payment_id,
                }},
            )
            raise HTTPException(
                status_code=400,
                detail=f"Payment verification failed: {e}",
            )

        # Switch the plan + extend validity.
        plan_key = order.get("plan_key")
        months = int(order.get("months", 1))
        bonus_months = int(order.get("bonus_months", 0))
        fresh = await db.users.find_one(
            {"id": current_user["id"]}, {"_id": 0},
        ) or {}
        same_plan = (fresh.get("plan") == plan_key)
        new_expiry = _extend_plan_expiry(
            fresh.get("plan_expires_at") if same_plan else None,
            months, bonus_months,
        )

        set_payload = {
            "plan":               plan_key,
            "plan_started_at": (
                fresh.get("plan_started_at")
                if same_plan
                else (datetime.utcnow().isoformat() + "+00:00")
            ),
            "plan_expires_at":     new_expiry,
            "plan_billing_cycle":  order.get("billing_cycle"),
            "plan_mocked":         False,
            "last_paid_payment_id": payload.razorpay_payment_id,
            "last_paid_at": datetime.utcnow().isoformat() + "+00:00",
        }
        await db.users.update_one(
            {"id": current_user["id"]}, {"$set": set_payload},
        )

        # 2026-04-30 — Coupon consumption: bump used_count atomically on
        # successful payment. We never block the user response if this
        # write fails (the payment is already complete).
        coupon_code = (order.get("coupon_code") or "").strip()
        if coupon_code:
            try:
                await db.coupons.update_one(
                    {"code": coupon_code},
                    {"$inc": {"used_count": 1},
                     "$set": {"updated_at": datetime.utcnow().replace(
                         tzinfo=timezone.utc).isoformat()}},
                )
            except Exception:
                _logger.exception(
                    "Coupon used_count bump failed for code=%s", coupon_code,
                )

        await db.razorpay_orders.update_one(
            {"_id": order["_id"]},
            {"$set": {
                "status":              "paid",
                "razorpay_payment_id": payload.razorpay_payment_id,
                "razorpay_signature":  payload.razorpay_signature,
                "paid_at": datetime.utcnow().isoformat() + "+00:00",
                "applied_expires_at": new_expiry,
            }},
        )

        return {
            "ok":               True,
            "already_credited": False,
            "plan":             plan_key,
            "billing_cycle":    order.get("billing_cycle"),
            "amount_inr":       int(order.get("amount_inr", 0)),
            "months":           months,
            "bonus_months":     bonus_months,
            "plan_expires_at":  new_expiry,
        }
