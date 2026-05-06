"""
Wallet + Razorpay top-up endpoints — Phase-4a incremental refactor.

Extracts 7 endpoints out of the server.py monolith:

  GET  /wallet                         get_wallet
  GET  /wallet/history                 get_wallet_history
  POST /wallet/purchase                purchase_credits      (mock top-up)
  GET  /wallet/quote                   wallet_quote          (label cost dry-run)
  POST /wallet/razorpay/create-order   rzp_create_order      (real Razorpay)
  POST /wallet/razorpay/verify         rzp_verify_and_credit (signature verify)
  POST /wallet/razorpay/webhook        rzp_webhook           (Razorpay webhook)

Public API surface 100% unchanged. Pattern: late-binding `init()`
defines every route inside the function body so it can `from server
import …` without triggering a circular import — exactly the pattern
that admin.py / couriers.py / custom_fields.py already use.
"""
from datetime import datetime
import json
import uuid
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel


wallet_router = APIRouter(prefix="/api", tags=["wallet"])


# ============================== Models ==============================
# These are router-local; nothing in server.py references them
# anymore after extraction.

class PurchaseCreditsRequest(BaseModel):
    """Body for POST /wallet/purchase (mock top-up)."""
    amount_inr: float  # validated against admin-configured packages


class RazorpayCreateOrderRequest(BaseModel):
    """Body for POST /wallet/razorpay/create-order.

    `amount_inr` is the rupee value the user wants to top up — we
    multiply by 100 internally to send paise to Razorpay.
    """
    amount_inr: int


class RazorpayVerifyRequest(BaseModel):
    """Body for POST /wallet/razorpay/verify.

    Three values returned by Razorpay Checkout on success that we
    must verify against our server-side secret before crediting.
    """
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


def init() -> None:
    """Register all routes after server.py finishes defining its
    helpers. Late-imports keep us out of circular-import territory."""
    import logging
    _logger = logging.getLogger("routers.wallet")
    from server import (  # noqa: WPS433 — intentional late import
        db,
        get_current_user as _get_current_user,
        _rzp_client,
        _RZP_KEY_ID,
        _RZP_WEBHOOK_SECRET,
        _get_admin_config,
        wallet_ensure,
        wallet_add_credits,
        wallet_list_history,
        wallet_balance,
        wallet_classify_and_cost,
        plan_room_status,
        _extend_plan_expiry,
    )

    # ---------------------- /wallet — balance ------------------------

    @wallet_router.get("/wallet")
    async def get_wallet(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        w = await wallet_ensure(db, current_user["id"])
        return {
            "total_credits": round(float(w.get("total_credits", 0.0)), 2),
            "used_credits": round(float(w.get("used_credits", 0.0)), 2),
            "remaining_credits": round(float(w.get("remaining_credits", 0.0)), 2),
            "updated_at": w.get("updated_at"),
        }

    # ---------------------- /wallet/history --------------------------

    @wallet_router.get("/wallet/history")
    async def get_wallet_history(
        limit: int = 100,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        entries = await wallet_list_history(
            db, current_user["id"], limit=max(1, min(500, limit)),
        )
        return {"entries": entries, "count": len(entries)}

    # ---------------------- /wallet/purchase (mock) -----------------

    @wallet_router.post("/wallet/purchase")
    async def purchase_credits(
        payload: PurchaseCreditsRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """MOCK credit top-up.

        Razorpay wiring lives in /wallet/razorpay/* below — this endpoint
        stays for legacy callers and admin testing. The admin configures
        available `credit_packages` in /admin/global-config; this endpoint
        matches the request amount to a package and credits the user
        with the BONUSED amount. If no exact-match package is found we
        fall back to a 1:1 conversion so custom amounts still work.
        """
        inr = float(payload.amount_inr or 0)
        if inr <= 0:
            raise HTTPException(status_code=400, detail="amount_inr must be > 0")
        if inr < 10 or inr > 100000:
            raise HTTPException(
                status_code=400,
                detail="Top-up must be between ₹10 and ₹1,00,000",
            )
        cfg = await _get_admin_config()
        pkg = next(
            (p for p in cfg["credit_packages"] if int(p["amount_inr"]) == int(inr)),
            None,
        )
        if pkg:
            credits = float(pkg["credits"])
            bonus_str = (
                f" (incl. {pkg['bonus']} bonus)" if pkg.get("bonus") else ""
            )
            desc = (
                f"Top-up ₹{int(inr)} → {credits:g} credits"
                f"{bonus_str} (mocked)"
            )
        else:
            credits = round(inr, 2)  # custom amount → 1:1 fallback
            desc = f"Top-up ₹{int(inr)} → {credits:g} credits (mocked)"
        res = await wallet_add_credits(
            db, current_user["id"], credits,
            ctype="purchase",
            description=desc,
        )
        wallet = res["wallet"]
        return {
            "ok": True,
            "mocked": True,
            "amount_inr": inr,
            "credits_added": credits,
            "bonus": (pkg or {}).get("bonus", 0),
            "balance": round(float(wallet.get("remaining_credits", 0.0)), 2),
            "history_id": res["history"]["id"],
        }

    # ---------------------- /wallet/quote ----------------------------

    @wallet_router.get("/wallet/quote")
    async def wallet_quote(
        address: str = "",
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Dry-run: show the user what ONE more label will cost right now.

        Phase-4b: complexity is classified by the LLM (cached + heuristic
        fallback); the reason string is surfaced so the UI can explain
        *why* an address was tagged simple/medium/complex.
        """
        room = await plan_room_status(db, current_user)
        plan_has_room = (
            bool(room["plan_has_room"])
            and not room["trial_expired"]
            and not room["daily_blocked"]
        )
        _s3 = await db.settings.find_one(
            {"user_id": current_user["id"]},
            {
                "_id": 0,
                "ai_cost_simple": 1,
                "ai_cost_medium": 1,
                "ai_cost_complex": 1,
            },
        ) or {}
        ai_costs3 = {
            "simple":  float(_s3.get("ai_cost_simple", 0.5)),
            "medium":  float(_s3.get("ai_cost_medium", 1.0)),
            "complex": float(_s3.get("ai_cost_complex", 2.0)),
        }
        bd, reason = await wallet_classify_and_cost(
            current_user, address, plan_has_room, ai_costs=ai_costs3,
        )
        bal = await wallet_balance(db, current_user["id"])
        return {
            "plan": room["plan"],
            "plan_has_room": plan_has_room,
            "trial_expired": room["trial_expired"],
            "daily_blocked": room["daily_blocked"],
            "ai_complexity": bd.ai_complexity,
            "ai_reason": reason,
            "ai_credits": bd.ai_credits,
            "ai_applies": bd.ai_applies,
            "shipment_credits": bd.shipment_credits,
            "total": bd.total,
            "wallet_balance": round(bal, 2),
            "can_afford": (bd.total <= bal + 1e-6),
            "ai_rates": ai_costs3,
        }

    # =================== Razorpay flow ==============================

    @wallet_router.post("/wallet/razorpay/create-order")
    async def rzp_create_order(
        payload: RazorpayCreateOrderRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Step 1 of Razorpay top-up flow.

        Creates a Razorpay order on the server (so the order_id is signed
        by Razorpay, not by us). We persist the order locally so the
        /verify endpoint can match the signed payment back to the user
        even before Razorpay sends a webhook.

        Returns the data the client needs to invoke Razorpay Checkout:
            {key_id, order_id, amount (paise), currency, receipt}
        """
        if not _rzp_client:
            raise HTTPException(
                status_code=503,
                detail="Razorpay is not configured on the server.",
            )
        inr = int(payload.amount_inr or 0)
        if inr < 10 or inr > 100000:
            raise HTTPException(
                status_code=400,
                detail="Amount must be between ₹10 and ₹1,00,000",
            )
        cfg = await _get_admin_config()
        pkg = next(
            (p for p in cfg["credit_packages"] if int(p["amount_inr"]) == inr),
            None,
        )
        credits = float(pkg["credits"]) if pkg else float(inr)
        bonus = int(pkg["bonus"]) if pkg else 0

        # Razorpay receipt has a 40-char limit.
        receipt = (
            f"wallet-{current_user['id'][:8]}-"
            f"{int(datetime.utcnow().timestamp())}"
        )
        try:
            rzp_order = _rzp_client.order.create({
                "amount": inr * 100,  # paise
                "currency": "INR",
                "receipt": receipt,
                "payment_capture": 1,
                "notes": {
                    "user_id": current_user["id"],
                    "user_email": current_user.get("email", ""),
                    "credits": credits,
                    "bonus": bonus,
                    "purpose": "wallet_topup",
                },
            })
        except Exception as e:
            _logger.exception("rzp create order failed")
            raise HTTPException(status_code=502, detail=f"Razorpay error: {e}")

        await db.razorpay_orders.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": current_user["id"],
            "razorpay_order_id": rzp_order["id"],
            "amount_inr": inr,
            "amount_paise": inr * 100,
            "credits_to_grant": credits,
            "bonus_credits": bonus,
            "status": "created",
            "created_at": datetime.utcnow().isoformat() + "+00:00",
        })

        return {
            "key_id":  _RZP_KEY_ID,
            "order_id": rzp_order["id"],
            "amount_paise": rzp_order["amount"],
            "amount_inr": inr,
            "currency": rzp_order["currency"],
            "receipt": rzp_order["receipt"],
            "credits_to_grant": credits,
            "bonus_credits": bonus,
            "user_email": current_user.get("email", ""),
            "user_name": current_user.get(
                "name", current_user.get("email", "User"),
            ),
        }

    @wallet_router.post("/wallet/razorpay/verify")
    async def rzp_verify_and_credit(
        payload: RazorpayVerifyRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Step 2 of Razorpay top-up flow.

        Razorpay Checkout calls this endpoint with the three signed
        values. We verify the signature server-side using our key
        secret, then credit the wallet exactly once (idempotent on
        razorpay_payment_id).
        """
        if not _rzp_client:
            raise HTTPException(status_code=503, detail="Razorpay not configured")

        # 1. Look up our local order record.
        order = await db.razorpay_orders.find_one({
            "razorpay_order_id": payload.razorpay_order_id,
            "user_id": current_user["id"],
        })
        if not order:
            raise HTTPException(
                status_code=404, detail="Order not found for this user",
            )
        if (order.get("purpose") or "wallet_topup") != "wallet_topup":
            raise HTTPException(
                status_code=400,
                detail=(
                    "This order isn't a wallet top-up. Use "
                    "/plans/razorpay/verify for plan subscriptions."
                ),
            )

        # 2. Idempotency: if already paid + credited, just return the wallet.
        if order.get("status") == "paid":
            bal = await wallet_balance(db, current_user["id"])
            return {
                "ok": True,
                "already_credited": True,
                "credits_added": order.get("credits_to_grant", 0),
                "balance": bal,
            }

        # 3. Verify signature with Razorpay's util.
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
                    "status": "verify_failed",
                    "error": str(e),
                    "razorpay_payment_id": payload.razorpay_payment_id,
                }},
            )
            raise HTTPException(
                status_code=400, detail=f"Payment verification failed: {e}",
            )

        # 4. Credit the wallet (single source of truth).
        credits = float(order.get("credits_to_grant", 0))
        bonus = int(order.get("bonus_credits", 0))
        bonus_str = f" (incl. {bonus} bonus)" if bonus else ""
        desc = (
            f"Top-up ₹{int(order['amount_inr'])} → "
            f"{credits:g} credits{bonus_str}"
        )

        res = await wallet_add_credits(
            db, current_user["id"], credits,
            ctype="purchase",
            description=desc,
            order_id=payload.razorpay_payment_id,
        )

        # 5. Mark the order paid (so re-submits are idempotent).
        await db.razorpay_orders.update_one(
            {"_id": order["_id"]},
            {"$set": {
                "status": "paid",
                "razorpay_payment_id": payload.razorpay_payment_id,
                "razorpay_signature":  payload.razorpay_signature,
                "paid_at": datetime.utcnow().isoformat() + "+00:00",
            }},
        )

        wallet = res["wallet"]
        return {
            "ok": True,
            "already_credited": False,
            "amount_inr": int(order["amount_inr"]),
            "credits_added": credits,
            "bonus": bonus,
            "balance": round(float(wallet.get("remaining_credits", 0.0)), 2),
            "history_id": res["history"]["id"],
        }

    @wallet_router.post("/wallet/razorpay/webhook")
    async def rzp_webhook(request: Request):
        """Optional safety net — Razorpay calls this independently of
        the browser-side /verify call. Useful if the user closes the
        app mid-flow. Verifies signature against
        RAZORPAY_WEBHOOK_SECRET (set in Razorpay dashboard + .env).

        Branches by purpose: wallet_topup credits the wallet,
        plan_subscription extends plan validity (calls
        `_extend_plan_expiry` from server.py).
        """
        if not _RZP_WEBHOOK_SECRET or not _rzp_client:
            return {"ok": True, "skipped": "webhook not configured"}
        body = await request.body()
        sig = request.headers.get("x-razorpay-signature", "")
        try:
            _rzp_client.utility.verify_webhook_signature(
                body.decode("utf-8"), sig, _RZP_WEBHOOK_SECRET,
            )
        except Exception as e:
            raise HTTPException(
                status_code=400, detail=f"Webhook verify failed: {e}",
            )
        try:
            evt = json.loads(body)
        except Exception:
            return {"ok": True}
        if evt.get("event") == "payment.captured":
            pay = evt.get("payload", {}).get("payment", {}).get("entity", {})
            order_id = pay.get("order_id")
            payment_id = pay.get("id")
            order = await db.razorpay_orders.find_one(
                {"razorpay_order_id": order_id},
            )
            if order and order.get("status") != "paid":
                purpose = order.get("purpose") or "wallet_topup"
                if purpose == "plan_subscription":
                    plan_key = order.get("plan_key")
                    months = int(order.get("months", 1))
                    bonus_months = int(order.get("bonus_months", 0))
                    user_doc = await db.users.find_one(
                        {"id": order["user_id"]}, {"_id": 0},
                    ) or {}
                    same_plan = (user_doc.get("plan") == plan_key)
                    new_expiry = _extend_plan_expiry(
                        user_doc.get("plan_expires_at") if same_plan else None,
                        months, bonus_months,
                    )
                    await db.users.update_one(
                        {"id": order["user_id"]},
                        {"$set": {
                            "plan": plan_key,
                            "plan_started_at": (
                                user_doc.get("plan_started_at")
                                if same_plan
                                else (datetime.utcnow().isoformat() + "+00:00")
                            ),
                            "plan_expires_at": new_expiry,
                            "plan_billing_cycle": order.get("billing_cycle"),
                            "plan_mocked": False,
                            "last_paid_payment_id": payment_id,
                            "last_paid_at": (
                                datetime.utcnow().isoformat() + "+00:00"
                            ),
                        }},
                    )
                    await db.razorpay_orders.update_one(
                        {"_id": order["_id"]},
                        {"$set": {
                            "status": "paid",
                            "razorpay_payment_id": payment_id,
                            "applied_expires_at": new_expiry,
                            "paid_at": datetime.utcnow().isoformat() + "+00:00",
                        }},
                    )
                else:
                    # Wallet top-up (legacy default).
                    credits = float(order.get("credits_to_grant", 0))
                    bonus = int(order.get("bonus_credits", 0))
                    bonus_str = f" (incl. {bonus} bonus)" if bonus else ""
                    desc = (
                        f"Top-up ₹{int(order['amount_inr'])} → "
                        f"{credits:g} credits{bonus_str} (webhook)"
                    )
                    await wallet_add_credits(
                        db, order["user_id"], credits,
                        ctype="purchase", description=desc,
                        order_id=payment_id,
                    )
                    await db.razorpay_orders.update_one(
                        {"_id": order["_id"]},
                        {"$set": {
                            "status": "paid",
                            "razorpay_payment_id": payment_id,
                            "paid_at": datetime.utcnow().isoformat() + "+00:00",
                        }},
                    )
        return {"ok": True}
