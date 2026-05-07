"""
Team Members router — Phase A
─────────────────────────────
Lets the SHOP-OWNER (the user_admin who bought the app) add staff
contacts under their account. Each staff member has:

    • name            : human-readable label (shown in SLA alert buttons)
    • phone           : the WhatsApp number that receives the SLA alert
    • role            : free-text job role (e.g. "Logistics Manager")
    • permissions     : list of feature keys (subset of registry) that
                        define what the staff member can see/do once
                        Phase-C login is built. For Phase A this is
                        stored but not enforced — visual only.
    • paid_extra      : True when this slot was unlocked by paying the
                        extra-member fee (beyond the free quota).

Plan-based caps are read from `plans.PlanSpec.team_member_cap` and the
admin's overrides via `resolve_plan(...)`. Beyond the cap a member can
still be added but only after the user pays the extra-member fee
(handled via /pay-extra-member, see below — wallet OR Razorpay).

Phase B+C — Team-member auth: each row also persists `email` +
`password_hash`. Members log in via `/api/team/login` which returns a
JWT carrying parent_user_id + permissions, validated by the auth
dependency on every request.
"""
from __future__ import annotations
import os
import uuid
import hashlib
import hmac
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Body
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field

from plans import resolve_plan


# Lazy import to avoid circular reference. JWT_SECRET is shared with the
# main user-token signer so a single auth dependency can decode both
# owner and team-member tokens transparently.
JWT_SECRET = os.environ.get("JWT_SECRET", "shippzo-dev-secret-change-me")
JWT_ALG    = "HS256"
TEAM_TOKEN_TTL_DAYS = 30

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_phone(p: str) -> str:
    """Strip non-digits. If 10 digits, prefix 91 (India)."""
    digits = "".join(c for c in (p or "") if c.isdigit())
    if len(digits) == 10:
        digits = "91" + digits
    return digits


# ─── Pydantic models (module-level to avoid Pydantic-v2 ForwardRef bugs) ──
class TeamMemberCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    phone: str = Field(..., min_length=8, max_length=20)
    role: str = Field("", max_length=80)
    permissions: List[str] = Field(default_factory=list)
    # Phase B+C — login credentials. Optional at create time so legacy
    # callers (Phase A flow) keep working; when provided we hash and
    # store, enabling the team member to /api/team/login.
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(None, min_length=6, max_length=80)


class TeamMemberUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    phone: Optional[str] = Field(None, max_length=20)
    role: Optional[str] = Field(None, max_length=80)
    permissions: Optional[List[str]] = None
    active: Optional[bool] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(None, min_length=6, max_length=80)


class TeamLoginPayload(BaseModel):
    email: EmailStr
    password: str


class PayExtraMemberPayload(BaseModel):
    method: str = Field(..., pattern="^(wallet|razorpay)$")


class CreateExtraMemberPayload(BaseModel):
    name: str
    phone: str
    role: str = ""
    permissions: List[str] = Field(default_factory=list)
    slot_token: str


# Phase D — Razorpay verify payload (module-level so FastAPI/Pydantic v2
# can resolve the schema at app boot; nested-class definitions inside
# `build_router` produced ForwardRef errors at request time).
class _TMRzpVerifyPayload(BaseModel):
    razorpay_order_id:   str
    razorpay_payment_id: str
    razorpay_signature:  str


def _public(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in doc.items() if k != "_id"}


def build_router(db, get_current_user) -> APIRouter:
    """Build the APIRouter, capturing `db` and the auth dependency in
    closure. Call this once from server.py and `app.include_router()`.
    """
    router = APIRouter(prefix="/api", tags=["team-members"])

    @router.get("/me/team-members")
    async def list_my_team_members(
        current_user: Dict[str, Any] = Depends(get_current_user),
    ):
        plan = await resolve_plan(db, current_user)
        rows = await db.team_members.find(
            {"user_id": current_user["id"], "active": {"$ne": False}}
        ).sort("created_at", 1).to_list(50)
        free_used = sum(1 for r in rows if not r.get("paid_extra"))
        extra_used = sum(1 for r in rows if r.get("paid_extra"))
        # Platform admins (is_admin=True) bypass plan caps so they can
        # manage / demo the feature for any tier without being blocked
        # by their own personal subscription level.
        cap = 99 if current_user.get("is_admin") else int(plan.team_member_cap or 0)
        return {
            "members":               [_public(r) for r in rows],
            "free_cap":              cap,
            "free_used":             free_used,
            "extra_used":            extra_used,
            "extra_member_price":    int(plan.extra_member_price_inr or 0),
            "plan_key":              plan.key,
            "plan_name":             plan.name,
            "can_add_free":          free_used < cap,
            "can_buy_extra":         int(plan.extra_member_price_inr or 0) > 0,
        }

    @router.post("/me/team-members")
    async def create_team_member(
        body: TeamMemberCreate = Body(...),
        current_user: Dict[str, Any] = Depends(get_current_user),
    ):
        plan = await resolve_plan(db, current_user)
        cap = 99 if current_user.get("is_admin") else int(plan.team_member_cap or 0)
        free_used = await db.team_members.count_documents({
            "user_id": current_user["id"],
            "active": {"$ne": False},
            "paid_extra": {"$ne": True},
        })
        if free_used >= cap:
            raise HTTPException(
                status_code=402,
                detail={
                    "message":     "Team member quota reached. Buy an extra slot to continue.",
                    "code":        "EXTRA_REQUIRED",
                    "plan":        plan.key,
                    "free_cap":    cap,
                    "free_used":   free_used,
                    "extra_price": int(plan.extra_member_price_inr or 0),
                },
            )
        phone = _normalise_phone(body.phone)
        if not phone or len(phone) < 10:
            raise HTTPException(status_code=400, detail="Invalid phone number")
        dup = await db.team_members.find_one({
            "user_id": current_user["id"], "phone": phone, "active": {"$ne": False},
        })
        if dup:
            raise HTTPException(status_code=409, detail="A team member with this phone already exists")
        doc = {
            "id":           str(uuid.uuid4()),
            "user_id":      current_user["id"],
            "name":         body.name.strip(),
            "phone":        phone,
            "role":         (body.role or "").strip(),
            "permissions":  list(body.permissions or []),
            "paid_extra":   False,
            "active":       True,
            "created_at":   _now(),
            "updated_at":   _now(),
        }
        # Phase B+C — login credentials (optional). When provided we
        # store a bcrypt hash so the team member can /api/team/login.
        if body.email:
            doc["email"] = body.email.lower().strip()
            existing = await db.team_members.find_one({
                "email": doc["email"], "active": {"$ne": False},
            })
            if existing:
                raise HTTPException(409, "This email is already used by another team member")
        if body.password:
            doc["password_hash"] = _pwd_ctx.hash(body.password)
        await db.team_members.insert_one(doc)
        return _public(doc)

    @router.put("/me/team-members/{member_id}")
    async def update_team_member(
        member_id: str,
        body: TeamMemberUpdate = Body(...),
        current_user: Dict[str, Any] = Depends(get_current_user),
    ):
        setdoc: Dict[str, Any] = {"updated_at": _now()}
        if body.name is not None:
            setdoc["name"] = body.name.strip()
        if body.phone is not None:
            ph = _normalise_phone(body.phone)
            if not ph or len(ph) < 10:
                raise HTTPException(status_code=400, detail="Invalid phone number")
            setdoc["phone"] = ph
        if body.role is not None:
            setdoc["role"] = body.role.strip()
        if body.permissions is not None:
            setdoc["permissions"] = list(body.permissions)
        if body.active is not None:
            setdoc["active"] = bool(body.active)
        if body.email is not None:
            setdoc["email"] = body.email.lower().strip()
        if body.password:
            setdoc["password_hash"] = _pwd_ctx.hash(body.password)
        res = await db.team_members.update_one(
            {"id": member_id, "user_id": current_user["id"]},
            {"$set": setdoc},
        )
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Member not found")
        doc = await db.team_members.find_one({"id": member_id, "user_id": current_user["id"]})
        return _public(doc) if doc else {"ok": True}

    @router.delete("/me/team-members/{member_id}")
    async def delete_team_member(
        member_id: str,
        current_user: Dict[str, Any] = Depends(get_current_user),
    ):
        res = await db.team_members.update_one(
            {"id": member_id, "user_id": current_user["id"]},
            {"$set": {"active": False, "updated_at": _now()}},
        )
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="Member not found")
        return {"ok": True}

    @router.post("/me/team-members/pay-extra")
    async def pay_extra_member(
        body: PayExtraMemberPayload = Body(...),
        current_user: Dict[str, Any] = Depends(get_current_user),
    ):
        """Unlock ONE extra-member slot.

        Phase D (2026-05-07): Razorpay path is now REAL — we create a
        signed Razorpay order and persist it under
        ``db.razorpay_orders`` with ``purpose="team_extra_member"``.
        The slot token is created in **unconsumed + unpaid** state; it
        is marked paid only after ``/me/team-members/razorpay/verify``
        successfully validates the signature returned by Razorpay
        Checkout. Wallet path stays unchanged — wallet has no signature
        round-trip so the slot token is paid immediately.
        """
        plan = await resolve_plan(db, current_user)
        price = int(plan.extra_member_price_inr or 0)
        if price <= 0:
            raise HTTPException(status_code=400, detail="Extra members are not enabled for your plan")

        # ── Wallet path (real deduction, unchanged) ───────────────
        if body.method == "wallet":
            wal = await db.wallets.find_one({"user_id": current_user["id"]}) or {"balance": 0}
            bal = int(wal.get("balance") or 0)
            if bal < price:
                raise HTTPException(
                    status_code=402,
                    detail=f"Insufficient wallet balance. Need ₹{price}, have ₹{bal}",
                )
            await db.wallets.update_one(
                {"user_id": current_user["id"]},
                {"$inc": {"balance": -price}, "$set": {"updated_at": _now()}},
                upsert=True,
            )
            await db.credit_history.insert_one({
                "id":         str(uuid.uuid4()),
                "user_id":    current_user["id"],
                "kind":       "team_extra_member",
                "amount":     -price,
                "note":       f"Extra team-member slot ({plan.name})",
                "created_at": _now(),
            })

            token = str(uuid.uuid4())
            await db.team_extra_tokens.insert_one({
                "id":         token,
                "user_id":    current_user["id"],
                "method":     "wallet",
                "amount":     price,
                "consumed":   False,
                "paid":       True,            # wallet → already paid
                "created_at": _now(),
            })
            return {
                "ok":         True,
                "slot_token": token,
                "amount":     price,
                "method":     "wallet",
                "razorpay_order_id": None,
            }

        # ── Razorpay path (REAL, Phase D) ─────────────────────────
        # Late-import the configured client + public key from server.py
        # so this router stays decoupled from server bootstrap order.
        try:
            from server import _rzp_client, _RZP_KEY_ID  # noqa: WPS433
        except Exception:
            _rzp_client = None
            _RZP_KEY_ID = ""

        if not _rzp_client or not _RZP_KEY_ID:
            raise HTTPException(
                status_code=503,
                detail="Razorpay is not configured on the server.",
            )

        # Create the unpaid slot token first so we can stamp it onto
        # the Razorpay order's `notes` for webhook traceability.
        token = str(uuid.uuid4())
        receipt = f"team-{current_user['id'][:8]}-{int(datetime.now(timezone.utc).timestamp())}"
        try:
            rzp_order = _rzp_client.order.create({
                "amount":          price * 100,   # paise
                "currency":        "INR",
                "receipt":         receipt,
                "payment_capture": 1,
                "notes": {
                    "user_id":     current_user["id"],
                    "user_email":  current_user.get("email", ""),
                    "purpose":     "team_extra_member",
                    "slot_token":  token,
                    "plan":        plan.name,
                },
            })
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Razorpay error: {e}")

        await db.team_extra_tokens.insert_one({
            "id":                token,
            "user_id":           current_user["id"],
            "method":            "razorpay",
            "amount":            price,
            "consumed":          False,
            "paid":              False,           # flipped on /verify
            "razorpay_order_id": rzp_order["id"],
            "created_at":        _now(),
        })

        # Mirror into the central razorpay_orders ledger so all
        # purchase types (wallet, plan, team-extra) share one audit log.
        await db.razorpay_orders.insert_one({
            "id":                str(uuid.uuid4()),
            "user_id":           current_user["id"],
            "razorpay_order_id": rzp_order["id"],
            "amount_inr":        price,
            "amount_paise":      price * 100,
            "purpose":           "team_extra_member",
            "slot_token":        token,
            "status":            "created",
            "created_at":        _now(),
        })

        return {
            "ok":               True,
            "slot_token":       token,
            "amount":           price,
            "method":           "razorpay",
            # Razorpay Checkout payload (matches /wallet/razorpay/create-order shape)
            "razorpay_order_id": rzp_order["id"],
            "key_id":           _RZP_KEY_ID,
            "amount_paise":     rzp_order["amount"],
            "currency":         rzp_order["currency"],
            "receipt":          rzp_order["receipt"],
            "user_email":       current_user.get("email", ""),
            "user_name":        current_user.get(
                "name", current_user.get("email", "User"),
            ),
        }

    # ── Phase D — verify Razorpay payment for team-extra-member slot ──
    @router.post("/me/team-members/razorpay/verify")
    async def rzp_verify_team_extra(
        payload: _TMRzpVerifyPayload = Body(...),
        current_user: Dict[str, Any] = Depends(get_current_user),
    ):
        """Step 2 of the Razorpay flow for buying an extra team-member
        slot.

        Razorpay Checkout posts back the three signed values; we verify
        them server-side against our key secret, then flip the slot
        token's ``paid`` flag. The frontend then calls
        ``/me/team-members/with-extra`` with the (now paid) slot_token
        to actually create the team-member row. Idempotent on
        ``razorpay_payment_id``."""
        try:
            from server import _rzp_client  # noqa: WPS433
        except Exception:
            _rzp_client = None

        if not _rzp_client:
            raise HTTPException(status_code=503, detail="Razorpay not configured")

        order = await db.razorpay_orders.find_one({
            "razorpay_order_id": payload.razorpay_order_id,
            "user_id": current_user["id"],
        })
        if not order:
            raise HTTPException(status_code=404, detail="Order not found for this user")
        if (order.get("purpose") or "") != "team_extra_member":
            raise HTTPException(
                status_code=400,
                detail=(
                    "This order isn't a team-member slot. Use "
                    "/wallet/razorpay/verify or /plans/razorpay/verify."
                ),
            )

        # Idempotency
        if order.get("status") == "paid":
            tok = await db.team_extra_tokens.find_one(
                {"id": order.get("slot_token"), "user_id": current_user["id"]},
            )
            return {
                "ok":               True,
                "already_credited": True,
                "slot_token":       order.get("slot_token"),
                "amount":           int(order.get("amount_inr") or 0),
                "consumed":         bool((tok or {}).get("consumed", False)),
            }

        # Signature verification
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
                    "status":              "verify_failed",
                    "error":               str(e),
                    "razorpay_payment_id": payload.razorpay_payment_id,
                }},
            )
            raise HTTPException(status_code=400, detail=f"Payment verification failed: {e}")

        # Flip slot token to paid + record payment id
        slot_token = order.get("slot_token")
        await db.team_extra_tokens.update_one(
            {"id": slot_token, "user_id": current_user["id"]},
            {"$set": {
                "paid":                True,
                "paid_at":              _now(),
                "razorpay_payment_id": payload.razorpay_payment_id,
            }},
        )
        await db.razorpay_orders.update_one(
            {"_id": order["_id"]},
            {"$set": {
                "status":              "paid",
                "razorpay_payment_id": payload.razorpay_payment_id,
                "razorpay_signature":  payload.razorpay_signature,
                "paid_at":             _now(),
            }},
        )

        return {
            "ok":               True,
            "already_credited": False,
            "slot_token":       slot_token,
            "amount":           int(order.get("amount_inr") or 0),
        }


    @router.post("/me/team-members/with-extra")
    async def create_extra_team_member(
        body: CreateExtraMemberPayload = Body(...),
        current_user: Dict[str, Any] = Depends(get_current_user),
    ):
        tok = await db.team_extra_tokens.find_one({
            "id": body.slot_token, "user_id": current_user["id"], "consumed": False,
        })
        if not tok:
            raise HTTPException(status_code=400, detail="Invalid or already-used slot token")
        # Phase D — every slot token must be paid before it can be
        # consumed. Wallet tokens are stamped paid=True at creation
        # time; Razorpay tokens are flipped to paid=True only after
        # /me/team-members/razorpay/verify succeeds. Tokens predating
        # Phase D didn't carry the field, so we treat missing as paid
        # (true) to keep wallet-flow backwards-compatible.
        if tok.get("paid", True) is False:
            raise HTTPException(
                status_code=402,
                detail=(
                    "This slot token has not been paid for. Complete the "
                    "Razorpay payment first."
                ),
            )
        phone = _normalise_phone(body.phone)
        if not phone or len(phone) < 10:
            raise HTTPException(status_code=400, detail="Invalid phone number")
        dup = await db.team_members.find_one({
            "user_id": current_user["id"], "phone": phone, "active": {"$ne": False},
        })
        if dup:
            raise HTTPException(status_code=409, detail="A team member with this phone already exists")
        doc = {
            "id":           str(uuid.uuid4()),
            "user_id":      current_user["id"],
            "name":         body.name.strip(),
            "phone":        phone,
            "role":         (body.role or "").strip(),
            "permissions":  list(body.permissions or []),
            "paid_extra":   True,
            "active":       True,
            "created_at":   _now(),
            "updated_at":   _now(),
            "extra_token":  body.slot_token,
        }
        await db.team_members.insert_one(doc)
        await db.team_extra_tokens.update_one(
            {"id": body.slot_token},
            {"$set": {"consumed": True, "consumed_at": _now()}},
        )
        return _public(doc)

    # ─── Phase B+C — team-member login ─────────────────────────────
    @router.post("/team/login")
    async def team_login(body: TeamLoginPayload = Body(...)):
        """Authenticate a team member by email + password and issue
        a JWT carrying parent_user_id + permissions. The main auth
        dependency (server.get_current_user) detects the token kind
        and merges the parent's user document with the member's
        permission set so every existing endpoint keeps working
        unchanged — except now it can call `require_permission(...)`."""
        email = body.email.lower().strip()
        member = await db.team_members.find_one({
            "email": email, "active": {"$ne": False}, "password_hash": {"$exists": True},
        })
        if not member or not _pwd_ctx.verify(body.password, member["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        # Parent must still be active for the team session to be valid.
        parent = await db.users.find_one({"id": member["user_id"]})
        if not parent:
            raise HTTPException(status_code=403, detail="Parent account no longer exists")
        now = datetime.now(timezone.utc)
        payload = {
            "kind":           "team",
            "team_member_id": member["id"],
            "parent_user_id": member["user_id"],
            "permissions":    list(member.get("permissions") or []),
            "iat":            int(now.timestamp()),
            "exp":            int((now + timedelta(days=TEAM_TOKEN_TTL_DAYS)).timestamp()),
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)
        return {
            "token": token,
            "kind":  "team",
            "team_member": {
                "id":          member["id"],
                "name":        member.get("name"),
                "role":        member.get("role"),
                "permissions": member.get("permissions") or [],
            },
            "parent_business": parent.get("shop_name") or parent.get("name"),
        }

    return router


# ─── Helper used by SLA engine / serializer ─────────────────────────
async def get_team_contacts(db, user_id: str) -> List[Dict[str, str]]:
    """Returns the staff list shaped for the SLA alerts UI:
        [{name, phone, role}, ...]
    Used by `_alert_to_public()` in server.py so the WhatsApp button
    can render Name on top, Role mid, Phone bottom.
    """
    rows = await db.team_members.find(
        {"user_id": user_id, "active": {"$ne": False}},
        {"_id": 0, "name": 1, "phone": 1, "role": 1},
    ).sort("created_at", 1).to_list(20)
    return [
        {
            "name":  r.get("name") or "",
            "phone": r.get("phone") or "",
            "role":  r.get("role") or "",
        }
        for r in rows if r.get("phone")
    ]
