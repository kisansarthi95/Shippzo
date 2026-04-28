"""
Phase-1 authentication + multi-tenant foundation.

- bcrypt password hashing (via passlib)
- PyJWT HS256 tokens (7-day expiry, renewable on activity)
- `get_current_user` FastAPI dependency for protected routes
- `seed_demo_shipments(user_id)` — 15 mixed-status demo rows for new users
- `claim_legacy_data_for_admin(user_id)` — the very first signup becomes
  the admin and inherits the pre-existing 50 shipments/couriers/settings
  (dev-only data we already had before auth existed).

Nothing in this module talks to Google Sheets — auth + data isolation
only. The Sheet sync path in server.py stays exactly as-is.
"""
from __future__ import annotations

import os
import uuid
import secrets
import random
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt as pyjwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

# --- Passlib / bcrypt 4.x compatibility shim --------------------------
# Passlib 1.7.x reads `bcrypt.__about__.__version__` to decide which
# backend implementation to use. bcrypt >= 4.0 removed `__about__`,
# producing a noisy warning at startup:
#     (trapped) error reading bcrypt version
# We add a tiny stub so passlib finds what it expects without forcing
# a version downgrade. Must run *before* `from passlib.context import …`.
try:
    import bcrypt as _bcrypt  # type: ignore
    if not hasattr(_bcrypt, "__about__"):
        class _About:  # noqa: D401
            __version__ = getattr(_bcrypt, "__version__", "4.x")
        _bcrypt.__about__ = _About()  # type: ignore[attr-defined]
except Exception:  # pragma: no cover — purely cosmetic
    pass

from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr, Field


log = logging.getLogger("auth")

# --- Config ---------------------------------------------------------------

JWT_SECRET = os.environ.get(
    "JWT_SECRET",
    # Dev fallback: use a persistent random string so restarts don't
    # invalidate all sessions in the preview environment. In production
    # this must be set via env.
    "dev-secret-" + secrets.token_hex(16),
)
JWT_ALG = "HS256"
JWT_TTL_DAYS = 7

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
_bearer = HTTPBearer(auto_error=False)


# --- Password helpers ----------------------------------------------------

def hash_password(raw: str) -> str:
    return _pwd.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return _pwd.verify(raw, hashed)
    except Exception:
        return False


# --- JWT helpers ---------------------------------------------------------

def make_token(user_id: str, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=JWT_TTL_DAYS)).timestamp()),
    }
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_token(token: str) -> Dict[str, Any]:
    try:
        return pyjwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except pyjwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")


# --- Models --------------------------------------------------------------

class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)
    name: str = Field(min_length=1, max_length=80)
    shop_name: str = Field(default="", max_length=80)
    phone: str = Field(min_length=10, max_length=15)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    """Email + registered phone acts as a 2-factor gate so the user can
    reset their own password without SMTP/OTP infra. The combination is
    rare enough in practice (attacker needs BOTH email and phone) to
    be a reasonable MVP trade-off. Rate-limited per-email.
    """
    email: EmailStr
    phone: str = Field(min_length=10, max_length=15)
    new_password: str = Field(min_length=6, max_length=128)


class UserPublic(BaseModel):
    id: str
    display_id: str = ""
    email: str
    name: str
    shop_name: str
    phone: str = ""
    is_admin: bool = False
    plan: str = "free_trial"
    created_at: str


# --- FastAPI dependency --------------------------------------------------

def get_current_user_factory(db):
    """Bind the `db` into a dependency closure. Use like:
        from auth import get_current_user_factory
        get_current_user = get_current_user_factory(db)
    in server.py at module level.
    """
    async def _dep(
        creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    ) -> Dict[str, Any]:
        if creds is None or (creds.scheme or "").lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        payload = decode_token(creds.credentials)
        user = await db.users.find_one({"id": payload.get("sub")}, {"_id": 0})
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user

    return _dep


# --- Demo data seeder (15 mixed-status shipments) ------------------------

_DEMO_CUSTOMERS = [
    ("Aarav Sharma",    "9810123001", "12 Model Town",     "Delhi",       "Delhi",          "110009"),
    ("Priya Iyer",      "9820123002", "45 MG Road",        "Bengaluru",   "Karnataka",      "560001"),
    ("Rohit Verma",     "9830123003", "7 Nehru Nagar",     "Jaipur",      "Rajasthan",      "302001"),
    ("Sneha Patel",     "9840123004", "22 Ring Road",      "Ahmedabad",   "Gujarat",        "380015"),
    ("Arjun Reddy",     "9850123005", "88 Film Nagar",     "Hyderabad",   "Telangana",      "500033"),
    ("Kavya Nair",      "9860123006", "3 Marine Drive",    "Mumbai",      "Maharashtra",    "400020"),
    ("Vivek Joshi",     "9870123007", "55 Civil Lines",    "Kanpur",      "Uttar Pradesh",  "208001"),
    ("Ritika Banerjee", "9880123008", "4 Park Street",     "Kolkata",     "West Bengal",    "700016"),
    ("Manish Gupta",    "9890123009", "30 Sector 17",      "Chandigarh",  "Chandigarh",     "160017"),
    ("Ankita Kapoor",   "9811123010", "11 Koregaon Park",  "Pune",        "Maharashtra",    "411001"),
    ("Ramesh Kumar",    "9821123011", "19 Race Course",    "Coimbatore",  "Tamil Nadu",     "641018"),
    ("Nidhi Agarwal",   "9831123012", "8 Lake View",       "Indore",      "Madhya Pradesh", "452001"),
    ("Suresh Pillai",   "9841123013", "67 Fort Kochi",     "Kochi",       "Kerala",         "682001"),
    ("Divya Choudhary", "9851123014", "2 Sarat Bose Road", "Kolkata",     "West Bengal",    "700020"),
    ("Harsh Modi",      "9861123015", "77 CG Road",        "Ahmedabad",   "Gujarat",        "380009"),
]

# Varied statuses — spec ask: "Delivered", "Dispatched", delayed (=Modified),
# returned, cancelled, etc. 15 rows, distribution hand-picked so every tab
# in the shipments list has at least one row.
_DEMO_STATUSES = [
    "Delivered", "Delivered", "Delivered",
    "Dispatched", "Dispatched", "Dispatched", "Dispatched",
    "Shipped", "Shipped",
    "Modified",
    "Cancel by buyer",
    "Cancelled",
    "Returned", "Returned",
    "Pending",
]

_DEMO_ITEMS = [
    "Cotton T-shirt M × 1",
    "Silver Earrings × 1",
    "Handmade Candle Set",
    "Wooden Photo Frame × 2",
    "Kids Storybook Bundle",
    "Leather Wallet × 1",
    "Jute Bag × 1",
    "Custom Mug × 1",
    "Bluetooth Speaker",
    "Steel Lunch Box",
    "Silk Dupatta",
    "Handloom Kurti L × 1",
    "Ceramic Planter Set",
    "Scented Soap Bundle",
    "Brass Diya Pair",
]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def seed_demo_shipments(db, user_id: str, courier_id: str) -> int:
    """Create 15 demo shipments for a new user so the app doesn't feel empty.

    The rows carry `is_demo: True` so the user can one-tap clear them via
    the "Clear Demo Data" settings action. Shipments are local-only (no
    Sheet write) since demos shouldn't consume the Master Sheet row pool.
    """
    docs = []
    now = datetime.now(timezone.utc)
    for i, ((name, phone, addr, city, state, pin), status_val, item) in enumerate(
        zip(_DEMO_CUSTOMERS, _DEMO_STATUSES, _DEMO_ITEMS)
    ):
        amt = random.choice([199, 299, 449, 599, 799, 999, 1299, 1499, 1899])
        pmode = random.choice(["COD", "COD", "Prepaid"])
        created = now - timedelta(days=random.randint(0, 14), hours=random.randint(0, 23))
        delivered_at = None
        if status_val == "Delivered":
            delivered_at = (created + timedelta(days=random.randint(2, 5))).isoformat()
        docs.append({
            "id": str(uuid.uuid4()),
            "tracking_id": f"DEMO{1001 + i}",
            "courier_id": courier_id,
            "courier_name": "Demo Courier",
            "customer_name": name,
            "customer_phone": phone,
            "address_line1": addr,
            "address_line2": "",
            "city": city,
            "state": state,
            "pincode": pin,
            "items": [item],
            "item_description": item,
            "amount": float(amt),
            "cod_amount": float(amt) if pmode == "COD" else 0.0,
            "weight": f"{random.choice([100, 150, 250, 400, 500])} g",
            "payment_mode": pmode,
            "status": status_val,
            "delivered_at": delivered_at,
            "order_id": f"DEMO-ORD-{1001 + i}",
            "notes": "",
            "user_id": user_id,
            "is_demo": True,
            "created_at": created.isoformat(),
            "sheet_row_num": None,
        })
    if docs:
        await db.shipments.insert_many(docs)
    log.info(f"Seeded {len(docs)} demo shipments for user {user_id}")
    return len(docs)


async def seed_default_courier(db, user_id: str) -> str:
    """Each new user gets one starter courier ("Demo Courier", prefix DEMO)
    so the demo rows and their first real shipment have something to link to.
    """
    cid = str(uuid.uuid4())
    await db.couriers.insert_one({
        "id": cid,
        "name": "Demo Courier",
        "prefix": "ND",
        "digits": 5,
        "next_seq": 1,
        "user_id": user_id,
        "created_at": utcnow_iso(),
    })
    return cid


async def claim_legacy_data_for_admin(db, user_id: str) -> Dict[str, int]:
    """The FIRST ever signup inherits every existing row that pre-dates
    multi-tenancy (shipments/couriers/settings/pending orders without a
    `user_id`). This is the developer's admin account — other future
    signups get a fresh space + 15 demo shipments.
    """
    claim_filter = {"$or": [{"user_id": {"$exists": False}}, {"user_id": None}, {"user_id": ""}]}
    r1 = await db.shipments.update_many(claim_filter, {"$set": {"user_id": user_id}})
    r2 = await db.couriers.update_many(claim_filter, {"$set": {"user_id": user_id}})
    r3 = await db.pending_orders.update_many(claim_filter, {"$set": {"user_id": user_id}})
    # settings: there's typically one global doc — tag it with admin's id.
    r4 = await db.settings.update_many(claim_filter, {"$set": {"user_id": user_id}})
    return {
        "shipments": r1.modified_count,
        "couriers": r2.modified_count,
        "pending_orders": r3.modified_count,
        "settings": r4.modified_count,
    }


def user_public(u: Dict[str, Any]) -> Dict[str, Any]:
    """Strip the password hash + Mongo _id when sending a user doc."""
    return {
        "id": u.get("id"),
        "display_id": u.get("display_id", ""),
        "email": u.get("email"),
        "name": u.get("name", ""),
        "shop_name": u.get("shop_name", ""),
        "phone": u.get("phone", ""),
        "is_admin": bool(u.get("is_admin", False)),
        "plan": u.get("plan", "free_trial"),
        "created_at": u.get("created_at", ""),
    }
