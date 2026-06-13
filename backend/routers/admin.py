"""
Admin-only endpoints — Phase-2 incremental refactor.

Phase-1 (previous session): scaffold + GET /admin/global-config moved.
Phase-2 (this session):     add 3 more admin handlers, all using the
                            same proven late-binding pattern:

  - GET    /admin/users
  - GET    /admin/users/{user_id}
  - POST   /admin/users/{user_id}/reset-password

All bound via `init()` after server.py finishes defining the helpers
they depend on, so the router file never tries to `from server import …`
at module load time (which would crash with a circular import).

Public API:
  - `admin_router`  : the APIRouter instance (prefix /api/admin)
  - `init()`        : called once by server.py before include_router()
"""
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field


admin_router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Models — kept here so the router file is self-contained.
# ---------------------------------------------------------------------------

class AdminResetPasswordRequest(BaseModel):
    """Body for POST /admin/users/{user_id}/reset-password."""
    new_password: str = Field(min_length=6, max_length=128)


class AdminWalletCreditRequest(BaseModel):
    """Body for POST /admin/users/{user_id}/wallet-credit.

    Positive `amount` adds credits, negative `amount` deducts (deduction
    is clamped at the user's current balance so wallet never goes below
    zero). `reason` is mandatory for audit trail.
    """
    amount: float = Field(..., description="Positive to add, negative to deduct")
    reason: str   = Field(..., min_length=3, max_length=200)


class AdminPlanExtendRequest(BaseModel):
    """Body for POST /admin/users/{user_id}/extend-plan.

    Adds `days` to the user's current `plan_expires_at` (or sets the
    extension from `now()` if the plan has already expired). Negative
    values are NOT allowed here — to shorten a plan, admin should use
    a credit refund + manual support instead.
    """
    days:   int = Field(..., ge=1, le=730, description="Days to extend (1..730)")
    reason: str = Field(..., min_length=3, max_length=200)


def init() -> None:
    """Late-binding registration of admin endpoints.

    Called exactly once by server.py at the bottom of the file, after
    `db`, `get_current_user`, `_require_admin`, `_get_admin_config`,
    `hash_password`, `_log_pwd_attempt` and friends are defined.
    """
    from server import (  # noqa: WPS433 — intentional late import
        db,
        get_current_user as _get_current_user,
        _require_admin as _require_admin_helper,
        _get_admin_config as _get_admin_config_helper,
        _log_pwd_attempt as _log_pwd_attempt_helper,
    )
    from auth import hash_password  # safe: no circular dep with auth.py

    # -----------------------------------------------------------------
    # GET /admin/global-config
    # -----------------------------------------------------------------
    @admin_router.get("/global-config")
    async def get_global_config(
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Return the admin's global configuration (AI rates, credit
        packages, plan pricing, feature flags). Admin-only.
        """
        _require_admin_helper(current_user)
        return await _get_admin_config_helper()

    # -----------------------------------------------------------------
    # GET /admin/users
    # -----------------------------------------------------------------
    @admin_router.get("/users")
    async def list_users(
        current_user: Dict[str, Any] = Depends(_get_current_user),
        q: str = "",
        plan: str = "",
        limit: int = 200,
        skip: int = 0,
    ):
        """List all users with aggregated usage stats.

        Query params:
          q     — case-insensitive search across email, name, shop_name
          plan  — filter by plan key (free_trial | silver | gold | platinum)
          limit — max rows (1..500, default 200)
          skip  — offset for pagination
        """
        _require_admin_helper(current_user)
        limit = max(1, min(int(limit or 200), 500))
        skip = max(0, int(skip or 0))

        match: Dict[str, Any] = {}
        if plan and plan.strip():
            match["plan"] = plan.strip()
        if q and q.strip():
            needle = re.escape(q.strip())
            regex = {"$regex": needle, "$options": "i"}
            match["$or"] = [
                {"email": regex},
                {"name": regex},
                {"shop_name": regex},
            ]

        total = await db.users.count_documents(match or {})
        cursor = (
            db.users.find(match or {}, {"_id": 0, "password_hash": 0})
            .sort("created_at", -1)
            .skip(skip)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)

        # Aggregated plan counts (always over the full collection so the
        # admin sees the global split even while filtering).
        plan_counts: Dict[str, int] = {}
        async for doc in db.users.aggregate([
            {"$group": {"_id": "$plan", "n": {"$sum": 1}}},
        ]):
            plan_counts[doc["_id"] or "free_trial"] = int(doc["n"])
        admin_count = await db.users.count_documents({"is_admin": True})

        # Wallet balances + this-month label counts for the visible page.
        uid_list = [d["id"] for d in docs if d.get("id")]
        wallets: Dict[str, float] = {}
        if uid_list:
            async for w in db.wallets.find({"user_id": {"$in": uid_list}}, {"_id": 0}):
                wallets[w["user_id"]] = float(w.get("remaining_credits", 0))

        now = datetime.now(timezone.utc)
        month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc).isoformat()
        label_counts: Dict[str, int] = {}
        if uid_list:
            pipeline = [
                {"$match": {
                    "user_id": {"$in": uid_list},
                    "created_at": {"$gte": month_start},
                }},
                {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
            ]
            async for doc in db.shipments.aggregate(pipeline):
                label_counts[doc["_id"]] = int(doc["n"])

        rows: List[Dict[str, Any]] = []
        for d in docs:
            uid = d.get("id", "")
            # Compute plan expiry status bucket.
            plan_expires_at = d.get("plan_expires_at")
            plan_expired = False
            plan_days_left: Optional[int] = None
            if plan_expires_at:
                try:
                    exp = datetime.fromisoformat(str(plan_expires_at))
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    plan_expired = now > exp
                    plan_days_left = max(0, (exp - now).days)
                except Exception:
                    pass
            rows.append({
                "id":                 uid,
                "display_id":         d.get("display_id", "") or "",
                "email":              d.get("email", ""),
                "name":               d.get("name", "") or "",
                "shop_name":          d.get("shop_name", "") or "",
                "phone":              d.get("phone", "") or "",
                "plan":               d.get("plan", "free_trial"),
                "is_admin":           bool(d.get("is_admin")),
                "plan_mocked":        bool(d.get("plan_mocked", False)),
                "plan_billing_cycle": d.get("plan_billing_cycle"),
                "plan_started_at":    d.get("plan_started_at"),
                "plan_expires_at":    plan_expires_at,
                "plan_expired":       plan_expired,
                "plan_days_left":     plan_days_left,
                "auto_renew":         d.get("auto_renew") is not False,
                "cancelled_at":       d.get("cancelled_at"),
                "created_at":         d.get("created_at", ""),
                "last_login_at":      d.get("last_login_at") or d.get("updated_at", ""),
                "wallet_balance":     wallets.get(uid, 0.0),
                "labels_this_month":  label_counts.get(uid, 0),
                "auth_provider":      d.get("auth_provider", "email"),
                # Phase-2b: surface the anti-abuse decision so admins
                # know which accounts were silently denied a free trial.
                "trial_denied_reason": d.get("trial_denied_reason", "") or "",
                "device_fingerprint":  (d.get("device_fingerprint", "") or "")[:12],
            })

        return {
            "total": total,
            "limit": limit,
            "skip": skip,
            "users": rows,
            "summary": {
                "total_users":   sum(plan_counts.values()),
                "admin_count":   admin_count,
                "plan_counts":   plan_counts,
                "displayed":     len(rows),
            },
        }

    # -----------------------------------------------------------------
    # GET /admin/users/{user_id}
    # -----------------------------------------------------------------
    @admin_router.get("/users/{user_id}")
    async def user_detail(
        user_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Deep-dive on a single user — recent shipments, wallet history."""
        _require_admin_helper(current_user)
        u = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
        if not u:
            raise HTTPException(status_code=404, detail="User not found")

        wallet = await db.wallets.find_one({"user_id": user_id}, {"_id": 0})
        # Phase-25c — return FULL shipment doc (sans Mongo _id) so the
        # admin UI can open an in-place detail panel without making a
        # second round-trip per shipment. The previous projection only
        # gave 7 columns and hid `id`, which made the rows un-tappable.
        recent_ships = await db.shipments.find(
            {"user_id": user_id}, {"_id": 0},
        ).sort("created_at", -1).limit(20).to_list(length=20)

        ship_count = await db.shipments.count_documents({"user_id": user_id})
        paid_orders = await db.razorpay_orders.count_documents(
            {"user_id": user_id, "status": "paid"},
        )

        recent_wallet_tx = await db.wallet_history.find(
            {"user_id": user_id}, {"_id": 0},
        ).sort("created_at", -1).limit(15).to_list(length=15)

        return {
            "user": u,
            "wallet": wallet or {"remaining_credits": 0},
            "shipment_count": ship_count,
            "paid_orders_count": paid_orders,
            "recent_shipments": recent_ships,
            "recent_wallet_tx": recent_wallet_tx,
        }

    # -----------------------------------------------------------------
    # POST /admin/users/{user_id}/reset-password
    # -----------------------------------------------------------------
    @admin_router.post("/users/{user_id}/reset-password")
    async def reset_user_password(
        user_id: str,
        payload: AdminResetPasswordRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        """Admin-only override — sets a new password for ANY user.
        The admin is expected to share the new password with the user
        over the phone. Every reset is logged in pwd_reset_attempts.
        """
        _require_admin_helper(current_user)
        target = await db.users.find_one({"id": user_id})
        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        now_iso = datetime.utcnow().isoformat() + "+00:00"
        await db.users.update_one(
            {"id": user_id},
            {"$set": {
                "password_hash":           hash_password(payload.new_password),
                "password_changed_at":     now_iso,
                "password_reset_by_admin": current_user.get("email", ""),
                "password_reset_at":       now_iso,
            }},
        )
        await _log_pwd_attempt_helper(
            target.get("email", ""), True,
            f"admin-reset by {current_user.get('email','')}",
        )
        return {
            "ok": True,
            "user_id": user_id,
            "display_id": target.get("display_id", ""),
            "email": target.get("email", ""),
            "message": "Password reset. Share the new password with the user over the phone.",
        }

    # -----------------------------------------------------------------
    # POST /admin/users/{user_id}/wallet-credit
    #
    # Super-admin manual wallet adjustment.
    # Positive `amount` → grants credits (admin_grant).
    # Negative `amount` → deducts credits (admin_deduct, clamped to 0).
    # Every adjustment is recorded in wallet_history with the
    # admin's email + reason. Negative-amount calls require is_admin
    # to be True (already enforced by _require_admin) — only the
    # super-admin (first registered user with is_admin=True) can do
    # any kind of manual adjustment, per product policy.
    # -----------------------------------------------------------------
    @admin_router.post("/users/{user_id}/wallet-credit")
    async def admin_wallet_credit(
        user_id: str,
        payload: AdminWalletCreditRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        _require_admin_helper(current_user)
        target = await db.users.find_one({"id": user_id}, {"_id": 0})
        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        # Late-import to avoid a server.py ↔ admin.py ↔ wallet.py cycle.
        from wallet import admin_adjust_credits

        result = await admin_adjust_credits(
            db,
            user_id,
            float(payload.amount),
            admin_email=current_user.get("email", "admin"),
            reason=payload.reason,
        )
        return {
            "ok": True,
            "user_id": user_id,
            "email": target.get("email", ""),
            "applied": result["applied"],
            "new_balance": float((result["wallet"] or {}).get("remaining_credits", 0)),
            "history": result["history"],
        }

    # -----------------------------------------------------------------
    # POST /admin/users/{user_id}/extend-plan
    #
    # Super-admin extension of `plan_expires_at` by N days.
    # - If plan already expired (or no expiry), extension starts from now().
    # - If plan still active,             extension stacks on existing expiry.
    # - Audit log appended to the user doc as `plan_extensions[]`.
    # -----------------------------------------------------------------
    @admin_router.post("/users/{user_id}/extend-plan")
    async def admin_extend_plan(
        user_id: str,
        payload: AdminPlanExtendRequest,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        _require_admin_helper(current_user)
        target = await db.users.find_one({"id": user_id})
        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        from datetime import timedelta

        # Parse current expiry (may be empty string, None, or ISO date).
        now = datetime.now(timezone.utc)
        cur_raw = target.get("plan_expires_at") or ""
        base_time = now
        if cur_raw:
            try:
                cur = datetime.fromisoformat(str(cur_raw).replace("Z", "+00:00"))
                if cur.tzinfo is None:
                    cur = cur.replace(tzinfo=timezone.utc)
                # Stack on existing expiry if it's still in the future.
                if cur > now:
                    base_time = cur
            except Exception:
                # Bad ISO string → treat as expired, extend from now().
                base_time = now

        new_expiry = base_time + timedelta(days=int(payload.days))
        new_iso = new_expiry.isoformat()

        # Append an audit entry to plan_extensions[] (create if missing).
        ext_entry = {
            "days":          int(payload.days),
            "reason":        payload.reason.strip(),
            "by_admin":      current_user.get("email", "admin"),
            "by_admin_id":   current_user.get("id", ""),
            "previous_expiry": cur_raw or None,
            "new_expiry":    new_iso,
            "at":            now.isoformat(),
        }
        await db.users.update_one(
            {"id": user_id},
            {
                "$set":  {"plan_expires_at": new_iso},
                "$push": {"plan_extensions": ext_entry},
            },
        )

        return {
            "ok":           True,
            "user_id":      user_id,
            "email":        target.get("email", ""),
            "previous_expiry": cur_raw or None,
            "new_expiry":   new_iso,
            "days_added":   int(payload.days),
        }

    # -----------------------------------------------------------------
    # GET /admin/users/{user_id}/shipments/export
    #
    # Returns a CSV with every shipment of the given user, including
    # ALL business-relevant fields (customer, address, courier, AWB,
    # amount, status, timestamps). Used by the super-admin to give
    # the customer a full backup or to investigate a support ticket.
    #
    # Wrapped in FastAPI Response so the browser / mobile client gets
    # the file straight from the response body (no extra round-trip).
    # -----------------------------------------------------------------
    @admin_router.get("/users/{user_id}/shipments/export")
    async def admin_export_shipments(
        user_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        import csv
        import io
        from fastapi.responses import Response

        _require_admin_helper(current_user)
        target = await db.users.find_one({"id": user_id}, {"_id": 0})
        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        ships = await db.shipments.find(
            {"user_id": user_id}, {"_id": 0},
        ).sort("created_at", -1).to_list(length=10_000)

        # Stable, business-friendly column order. Fields that don't
        # exist on a particular doc fall back to empty string, so
        # the CSV is rectangular regardless of schema drift over time.
        columns = [
            "id", "tracking_id", "master_order_id", "order_id",
            "customer_name", "customer_phone", "customer_alt_phone",
            "customer_email",
            "address_line1", "address_line2", "city", "state", "pincode",
            "courier", "courier_service",
            "items", "weight", "box_dimensions",
            "amount", "token_amount", "payment_type", "payment_mode",
            "status",
            "notes", "shipment_notes",
            "created_at", "updated_at", "shipped_at", "delivered_at",
        ]

        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(columns)
        for s in ships:
            row = []
            for col in columns:
                v = s.get(col, "")
                # Normalise None / nested dicts / lists for CSV safety.
                if v is None:
                    v = ""
                elif isinstance(v, (dict, list)):
                    import json as _json
                    v = _json.dumps(v, ensure_ascii=False)
                row.append(str(v))
            writer.writerow(row)

        # Prepend a UTF-8 BOM so Excel / Numbers / LibreOffice open the
        # download with the correct encoding and Indic / accented
        # characters render right out of the box.
        csv_body = "\ufeff" + buf.getvalue()
        # ASCII-safe filename + UTF-8 starred form so non-Latin shop
        # names survive Content-Disposition encoding.
        safe_email = re.sub(r"[^A-Za-z0-9_\-\.]", "_", target.get("email", "user"))
        filename = f"shipments_{safe_email}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
        return Response(
            content=csv_body,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Total-Shipments":   str(len(ships)),
            },
        )

    # -----------------------------------------------------------------
    # GET /admin/users/{user_id}/shipments/{shipment_id}
    #
    # Returns a single shipment (full doc) belonging to the target
    # user — used by the in-place "shipment detail" panel on the
    # admin Users screen. Mirrors the user-facing
    # `GET /shipments/{id}` endpoint but accepts cross-user lookups
    # because the caller is a verified admin.
    # -----------------------------------------------------------------
    @admin_router.get("/users/{user_id}/shipments/{shipment_id}")
    async def admin_get_shipment(
        user_id: str,
        shipment_id: str,
        current_user: Dict[str, Any] = Depends(_get_current_user),
    ):
        _require_admin_helper(current_user)
        doc = await db.shipments.find_one(
            {"id": shipment_id, "user_id": user_id}, {"_id": 0},
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Shipment not found for this user")
        return doc
